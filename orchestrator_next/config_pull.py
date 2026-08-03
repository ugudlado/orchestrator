"""Pull a workflow-config source into ``.orchestrator/<pack>/``.

Layout (only convention):

    <repo>/.orchestrator/<pack>/
      workflows/feature.yaml
      steps/<id>/SKILL.md
      lib/
      models.yaml
      config-lock.yaml

Usage::

    orchestrator config pull <git-url-or-path> [pack-name] [--skills] [--ref REF]

When ``pack-name`` is omitted, the basename of the git URL / directory is used.
Optional ``--skills`` symlinks each step's SKILL.md into ``<repo>/skills/<name>/``.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from orchestrator_next.paths import WORKFLOW_CONFIG_GIT_URL

_STEP_EXCLUDE_DIR_NAMES = frozenset({"runs", "__pycache__", ".pytest_cache"})
_CONFIG_ENTRIES = (
    "workflows",
    "steps",
    "lib",
    "models.yaml",
    "models.example.yaml",
    "pricing.yaml",
)
_PACK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _log(msg: str) -> None:
    print(f"config pull: {msg}", file=sys.stderr)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def resolve_repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    if env:
        return Path(env).resolve()
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        top = ""
    return Path(top or os.getcwd()).resolve()


def default_pack_name(source: str) -> str:
    """Derive a pack folder name from a git URL or filesystem path."""
    path = Path(source)
    if path.exists():
        name = path.resolve().name
    else:
        parsed = urlparse(source)
        name = Path(parsed.path).name
        if name.endswith(".git"):
            name = name[: -len(".git")]
    name = name.strip() or "config"
    if not _PACK_NAME_RE.match(name):
        raise ValueError(
            f"cannot derive a safe pack name from {source!r}; pass one explicitly"
        )
    return name


def validate_pack_name(name: str) -> str:
    if not _PACK_NAME_RE.match(name):
        raise ValueError(
            f"invalid pack name {name!r} — use letters, digits, . _ - "
            "(must start with alphanumeric)"
        )
    return name


def _skill_export_name(skill_md: Path, step_id: str) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return step_id
    if not text.startswith("---"):
        return step_id
    parts = text.split("---", 2)
    if len(parts) < 3:
        return step_id
    for line in parts[1].splitlines():
        line = line.strip()
        if line.startswith("name:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            if value:
                return value
    return step_id


def find_pack_config_root(checkout: Path) -> Path:
    for candidate in (checkout / "config", checkout, checkout / ".orchestrator"):
        if (candidate / "workflows").is_dir() and (candidate / "steps").is_dir():
            return candidate
    raise FileNotFoundError(
        f"no workflows/+steps/ under {checkout} (expected config/ or pack root)"
    )


def _copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(*_STEP_EXCLUDE_DIR_NAMES, "*.pyc"),
            symlinks=False,
        )
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _copy_step(src_step: Path, dst_step: Path) -> None:
    if dst_step.exists():
        shutil.rmtree(dst_step)
    dst_step.mkdir(parents=True)
    for child in sorted(src_step.iterdir()):
        if child.name in _STEP_EXCLUDE_DIR_NAMES:
            continue
        if child.is_symlink():
            target = child.resolve()
            dest = dst_step / child.name
            if target.is_dir():
                shutil.copytree(
                    target,
                    dest,
                    ignore=shutil.ignore_patterns(*_STEP_EXCLUDE_DIR_NAMES, "*.pyc"),
                    symlinks=False,
                )
            elif target.is_file():
                shutil.copy2(target, dest)
            continue
        if child.is_dir():
            shutil.copytree(
                child,
                dst_step / child.name,
                ignore=shutil.ignore_patterns(*_STEP_EXCLUDE_DIR_NAMES, "*.pyc"),
                symlinks=False,
            )
        else:
            shutil.copy2(child, dst_step / child.name)


def pull_into_pack(
    config_root: Path,
    repo_root: Path,
    pack_name: str,
    *,
    export_skills: bool,
    source_label: str,
    source_sha: str | None,
) -> dict[str, Any]:
    """Copy source config into ``repo_root/.orchestrator/<pack_name>/``."""
    dest = repo_root / ".orchestrator" / pack_name
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in _CONFIG_ENTRIES:
        src = config_root / name
        if not src.exists():
            continue
        if name == "steps":
            dest_steps = dest / "steps"
            if dest_steps.exists():
                shutil.rmtree(dest_steps)
            dest_steps.mkdir(parents=True)
            for step_dir in sorted(p for p in src.iterdir() if p.is_dir()):
                _copy_step(step_dir, dest_steps / step_dir.name)
                copied.append(f"steps/{step_dir.name}")
            continue
        _copy_tree(src, dest / name)
        copied.append(name)

    if not (dest / "workflows").is_dir() or not (dest / "steps").is_dir():
        raise RuntimeError(f"pull incomplete: expected workflows/ and steps/ under {dest}")

    skills_exported: list[str] = []
    if export_skills:
        skills_root = repo_root / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        for step_dir in sorted((dest / "steps").iterdir()):
            if not step_dir.is_dir():
                continue
            skill_md = step_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            export_name = _skill_export_name(skill_md, step_dir.name)
            dest_skill = skills_root / export_name
            if dest_skill.exists() or dest_skill.is_symlink():
                if dest_skill.is_dir() and not dest_skill.is_symlink():
                    shutil.rmtree(dest_skill)
                else:
                    dest_skill.unlink()
            rel = os.path.relpath(step_dir, skills_root)
            dest_skill.symlink_to(rel, target_is_directory=True)
            skills_exported.append(export_name)

    lock = {
        "version": 1,
        "pack": pack_name,
        "source": source_label,
        "source_sha": source_sha,
        "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "export_skills": export_skills,
        "skills": skills_exported,
        "entries": copied,
    }
    (dest / "config-lock.yaml").write_text(
        yaml.safe_dump(lock, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return lock


def fetch_source(source: str, ref: str | None) -> tuple[Path, str, str | None, Path | None]:
    path = Path(source)
    if path.exists():
        root = find_pack_config_root(path.resolve())
        sha = None
        proc = _git(path.resolve(), "rev-parse", "HEAD")
        if proc.returncode == 0:
            sha = proc.stdout.strip() or None
        return root, str(path.resolve()), sha, None

    tmp = Path(tempfile.mkdtemp(prefix="orchestrator-config-"))
    clone_cmd = ["git", "clone", "--depth", "1"]
    if ref:
        clone_cmd.extend(["--branch", ref])
    clone_cmd.extend([source, str(tmp / "src")])
    proc = subprocess.run(clone_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {proc.stderr.strip() or proc.stdout.strip()}")
    checkout = tmp / "src"
    sha = _git(checkout, "rev-parse", "HEAD").stdout.strip() or None
    root = find_pack_config_root(checkout)
    label = source if not ref else f"{source}@{ref}"
    return root, label, sha, tmp


def pull(
    *,
    repo_root: Path,
    source: str,
    pack_name: str,
    ref: str | None,
    export_skills: bool,
) -> dict[str, Any]:
    config_root, label, sha, cleanup = fetch_source(source, ref)
    try:
        return pull_into_pack(
            config_root,
            repo_root,
            pack_name,
            export_skills=export_skills,
            source_label=label,
            source_sha=sha,
        )
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestrator config pull",
        description=(
            "Pull workflow config into .orchestrator/<pack>/ "
            "(workflows + steps with SKILL.md). Optional --skills exports IDE links."
        ),
    )
    parser.add_argument(
        "source",
        help=f"git URL or local path (e.g. {WORKFLOW_CONFIG_GIT_URL})",
    )
    parser.add_argument(
        "pack",
        nargs="?",
        default=None,
        help="destination folder under .orchestrator/ (default: source basename)",
    )
    parser.add_argument("--repo", default=None, help="consumer repo root")
    parser.add_argument("--ref", default=None, help="git branch/tag for remote sources")
    parser.add_argument(
        "--skills",
        action="store_true",
        help="also symlink step SKILL.md packs into <repo>/skills/<name>/",
    )
    args = parser.parse_args(argv)

    repo_root = resolve_repo_root(args.repo)
    try:
        pack_name = validate_pack_name(args.pack or default_pack_name(args.source))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _log(f"repo={repo_root}")
    _log(f"pack={pack_name}")
    _log(f"source={args.source}" + (f" ref={args.ref}" if args.ref else ""))
    try:
        lock = pull(
            repo_root=repo_root,
            source=args.source,
            pack_name=pack_name,
            ref=args.ref,
            export_skills=args.skills,
        )
    except (OSError, RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    dest = repo_root / ".orchestrator" / pack_name
    _log(f"wrote {dest}")
    if lock.get("skills"):
        _log(f"skills: {', '.join(lock['skills'])}")
    elif args.skills:
        _log("skills: (none — no step SKILL.md found)")
    print(yaml.safe_dump(lock, sort_keys=False, default_flow_style=False), end="")
    return 0
