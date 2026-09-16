"""Pull a workflows pack source into ``.orchestrator/<pack>/``.

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
import hashlib
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
from orchestrator_next.trust import TrustError, check_source, verify_signature

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


# ---------------------------------------------------------------------------
# Lock contents (plan phase 3.2 — the `recipes.lock` concept, kept colocated
# as <pack>/config-lock.yaml rather than inventing a second file)
# ---------------------------------------------------------------------------
#: Contract fields `config update` diffs before letting a pack move.
LOCK_CONTRACT_FIELDS = ("version", "kind", "tools", "side_effects")


#: The lock cannot hash itself, and skills/ symlinks are a local export choice.
_TREE_HASH_EXCLUDE_NAMES = frozenset({"config-lock.yaml"})


def tree_sha256(root: Path) -> str:
    """Content hash of a pack tree — the identity of a non-git source.

    Hashes every file under the pack root (path + bytes, sorted), so a
    hand-edited step changes the digest. This is both the `commit` a non-git
    source records and the `pack_sha256` drift detection compares against.
    """
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if any(part in _STEP_EXCLUDE_DIR_NAMES for part in path.parts):
            continue
        if path.name in _TREE_HASH_EXCLUDE_NAMES:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


def read_step_contract(step_dir: Path) -> dict[str, Any]:
    """The contract fields the lock and `config update` care about.

    Read with plain `yaml.safe_load` rather than `parser.load_contract` on
    purpose: the lock must stay readable for a pack whose contract shape the
    installed engine does not understand yet, and `config update` must be able
    to diff an old contract against a new one without either side having to
    validate.
    """
    for name in ("contract.yaml", f"{step_dir.name}.yaml"):
        candidate = step_dir / name
        if candidate.is_file():
            doc = _load_yaml(candidate)
            if doc:
                return {k: doc.get(k) for k in LOCK_CONTRACT_FIELDS if k in doc}
    return {}


def read_pack_contracts(pack_root: Path) -> dict[str, dict[str, Any]]:
    """`{step_id: {version, kind, tools, side_effects}}` for a pack on disk."""
    steps_dir = pack_root / "steps"
    if not steps_dir.is_dir():
        return {}
    return {
        step.name: read_step_contract(step)
        for step in sorted(steps_dir.iterdir())
        if step.is_dir()
    }


def read_lock(pack_dir: Path) -> dict[str, Any]:
    """The pack's config-lock.yaml (empty dict when absent/unreadable)."""
    return _load_yaml(pack_dir / "config-lock.yaml")


def pack_tree_hash(pack_dir: Path) -> str | None:
    """Hash of the pack as it sits in the consumer repo.

    Compared against the lock's `pack_sha256` to catch a hand-edited pack. The
    lock's `commit` names the *source*; this names the *installation*, and the
    two are not interchangeable — the copy excludes some source entries.
    """
    return tree_sha256(pack_dir) if pack_dir.is_dir() else None


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

    # A pulled pack means this repo will host runs: make sure their scratch
    # directories are ignored before the first one appears (plan Phase 2.1).
    from orchestrator_next.paths import ensure_scratch_gitignored

    if ensure_scratch_gitignored(repo_root):
        _log(f"gitignore: added scratch ignore to {repo_root / '.gitignore'}")

    # `commit` is the plan's pack identity: git HEAD for a repo source, sha256
    # of the whole pulled tree otherwise. `source_sha` is kept as the legacy
    # alias so older readers keep working.
    commit = source_sha or tree_sha256(config_root)
    lock = {
        "version": 1,
        "pack": pack_name,
        "source": source_label,
        "source_sha": source_sha,
        "commit": commit,
        "pack_sha256": tree_sha256(dest),
        "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "export_skills": export_skills,
        "skills": skills_exported,
        "entries": copied,
        "steps": {
            step_id: fields.get("version")
            for step_id, fields in read_pack_contracts(dest).items()
        },
    }
    (dest / "config-lock.yaml").write_text(
        yaml.safe_dump(lock, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return lock


def fetch_source(source: str, ref: str | None) -> tuple[Path, str, str | None, Path | None]:
    # Trust is checked BEFORE anything is fetched — a pack is executable
    # content, so an unlisted remote must never even be cloned.
    _log(f"trust: {check_source(source)}")
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
    try:
        _log(f"signature: {verify_signature(checkout, ref)}")
    except TrustError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
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


# ---------------------------------------------------------------------------
# `orchestrator config update [pack]` — plan phase 3.2
# ---------------------------------------------------------------------------
def diff_contracts(
    old: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
) -> list[str]:
    """Human-readable per-step contract changes between two packs.

    A pack bump can silently widen a step's `tools` or add a `write:` side
    effect, which is exactly the thing a consumer must see before it lands.
    """
    lines: list[str] = []
    for step_id in sorted(set(old) | set(new)):
        if step_id not in old:
            lines.append(f"+ {step_id}: new step ({_fmt_fields(new[step_id])})")
            continue
        if step_id not in new:
            lines.append(f"- {step_id}: removed")
            continue
        for field in LOCK_CONTRACT_FIELDS:
            before, after = old[step_id].get(field), new[step_id].get(field)
            if before != after:
                lines.append(
                    f"~ {step_id}.{field}: {_fmt_value(before)} -> {_fmt_value(after)}"
                )
    return lines


def _fmt_value(value: Any) -> str:
    if value is None:
        return "(unset)"
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _fmt_fields(fields: dict[str, Any]) -> str:
    return ", ".join(f"{k}={_fmt_value(v)}" for k, v in fields.items()) or "(no contract)"


def update(
    *,
    repo_root: Path,
    pack_name: str,
    apply: bool,
    ref: str | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    """Re-pull a pack's recorded source and diff its contracts.

    Returns ``(diff_lines, lock)`` — ``lock`` is None on a dry run.
    """
    dest = repo_root / ".orchestrator" / pack_name
    if not dest.is_dir():
        raise FileNotFoundError(f"no pack at {dest} — run `orchestrator config pull` first")
    lock = read_lock(dest)
    source = str(lock.get("source") or "")
    if not source:
        raise RuntimeError(
            f"{dest / 'config-lock.yaml'} has no `source` — re-pull the pack explicitly"
        )
    # The lock stores "<url>@<ref>" when the original pull pinned a ref.
    if ref is None and "@" in source and not Path(source).exists():
        source, _, ref = source.rpartition("@")

    old_contracts = read_pack_contracts(dest)
    config_root, label, sha, cleanup = fetch_source(source, ref)
    try:
        new_contracts = read_pack_contracts(config_root)
        lines = diff_contracts(old_contracts, new_contracts)
        if not apply:
            return lines, None
        new_lock = pull_into_pack(
            config_root,
            repo_root,
            pack_name,
            export_skills=bool(lock.get("export_skills")),
            source_label=label,
            source_sha=sha,
        )
        return lines, new_lock
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def update_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestrator config update",
        description=(
            "Re-pull a pack's recorded source and show what changed in its step "
            "contracts. Nothing is written without --yes."
        ),
    )
    parser.add_argument("pack", nargs="?", default=None, help="pack under .orchestrator/")
    parser.add_argument("--repo", default=None, help="consumer repo root")
    parser.add_argument("--ref", default=None, help="git branch/tag to update to")
    parser.add_argument("--yes", action="store_true", help="apply the update")
    args = parser.parse_args(argv)

    repo_root = resolve_repo_root(args.repo)
    pack_name = args.pack
    if not pack_name:
        from orchestrator_next.paths import list_config_packs

        packs = list_config_packs(repo_root)
        if len(packs) != 1:
            names = ", ".join(p[0] for p in packs) or "(none)"
            print(
                f"error: name the pack to update (packs under "
                f"{repo_root / '.orchestrator'}: {names})",
                file=sys.stderr,
            )
            return 1
        pack_name = packs[0][0]

    try:
        lines, new_lock = update(
            repo_root=repo_root, pack_name=pack_name, apply=args.yes, ref=args.ref
        )
    except (OSError, RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if lines:
        print(f"contract changes in pack {pack_name!r}:")
        for line in lines:
            print(f"  {line}")
    else:
        print(f"pack {pack_name!r}: no contract changes")
    if new_lock is None:
        print("\ndry run — re-run with --yes to apply.")
    else:
        print(f"\nupdated {repo_root / '.orchestrator' / pack_name} "
              f"(commit {new_lock.get('commit')})")
    return 0


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
