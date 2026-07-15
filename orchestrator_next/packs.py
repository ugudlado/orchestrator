"""`orchestrator pack` — install/remove/list config packs (ORC-119).

A config pack is a directory of workflows/ + steps/ following the convention
in docs/pack-convention.md. This module implements the "dumb mechanics"
locked by the plan (docs/plan-config-repo-split.md, Phase P):

  - Install = copy workflows/ + steps/ into $ORCHESTRATOR_CONFIG, recording
    every installed path in <config_root>/.packs.json under the pack's name.
  - Validate fully before copying anything (all-or-nothing).
  - Conflicts (existing step id / workflow name) refuse the whole install.
  - No layering, no in-place upgrade — remove then add.
  - pack add/remove refuse when the config root is a tracked git path (so
    this dev checkout's own config/ can never be mutated by pack ops).
  - pack list is read-only and has no such restriction.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from orchestrator_next.paths import ConfigRootError, config_root

SUPPORTED_PROTOCOLS = {1}

RECEIPTS_FILENAME = ".packs.json"

_CONCRETE_MODEL_RE = re.compile(r"^(claude-|us\.anthropic\.)")


class PackError(ValueError):
    """Raised for any pack operation failure (validation, conflict, safety)."""


def _clean_git_env() -> dict:
    """Env for subprocess `git` calls, stripped of ambient GIT_* vars.

    When this code runs from inside a pre-commit hook subprocess, pre-commit
    sets GIT_INDEX_FILE/GIT_DIR/GIT_WORK_TREE to point at its own temporary
    staging context. Any `git` subprocess we spawn (clone, tracked-root check)
    would silently inherit those and operate against the wrong index/worktree
    instead of the real one. Stripping every GIT_* var (not just those three)
    avoids that whole class of leak.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


@dataclass
class PackValidationResult:
    name: str
    version: str
    protocol: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    workflow_files: list[Path] = field(default_factory=list)
    step_dirs: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------
# Source resolution (local path or git URL)
# --------------------------------------------------------------------------

def _looks_like_git_url(source: str) -> bool:
    return (
        source.startswith(("http://", "https://", "git@", "ssh://", "file://"))
        or source.endswith(".git")
    )


def _clone_shallow(url: str, dest: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
        env=_clean_git_env(),
    )
    if result.returncode != 0:
        raise PackError(f"git clone failed for {url}:\n{result.stderr}")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _load_pack_yaml(pack_root: Path) -> dict:
    pack_yaml = pack_root / "pack.yaml"
    if not pack_yaml.is_file():
        raise PackError(f"pack.yaml not found at {pack_yaml}")
    try:
        with open(pack_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise PackError(f"pack.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError("pack.yaml must be a YAML mapping")
    return data


def _check_contract_dirs(pack_root: Path, errors: list[str], warnings: list[str]) -> list[Path]:
    """Validate each steps/<id>/contract.yaml; return the list of step dirs (excluding lib/)."""
    steps_dir = pack_root / "steps"
    step_dirs: list[Path] = []
    if not steps_dir.is_dir():
        return step_dirs

    for entry in sorted(steps_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "lib":
            continue  # steps/lib/ is shared helpers, not a step — excluded per convention
        step_dirs.append(entry)

        contract_path = entry / "contract.yaml"
        if not contract_path.is_file():
            errors.append(f"steps/{entry.name}/: missing contract.yaml")
            continue
        try:
            with open(contract_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            errors.append(f"steps/{entry.name}/contract.yaml: invalid YAML — {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"steps/{entry.name}/contract.yaml: must be a YAML mapping")
            continue

        contract_id = data.get("id")
        if contract_id != entry.name:
            errors.append(
                f"steps/{entry.name}/contract.yaml: id {contract_id!r} does not match "
                f"directory name {entry.name!r}"
            )

        model = data.get("model")
        run = data.get("run")
        if not model and not run:
            errors.append(
                f"steps/{entry.name}/contract.yaml: must set exactly one of model: or run:"
            )
        elif model and _CONCRETE_MODEL_RE.match(str(model)):
            warnings.append(
                f"steps/{entry.name}/contract.yaml: model: {model!r} looks like a concrete "
                "model id, not an alias — packs must use capability-tier aliases "
                "(opus, sonnet, composer, ...)"
            )

    return step_dirs


def validate_pack(pack_root: Path, repo_root: str) -> PackValidationResult:
    """Validate a pack directory in isolation. Does not copy anything.

    Temporarily points ORCHESTRATOR_CONFIG at pack_root so validate_workflow
    and load_contract_for_step resolve against the pack's own workflows/steps,
    not whatever config root is currently active.
    """
    data = _load_pack_yaml(pack_root)

    name = data.get("name")
    version = data.get("version")
    protocol = data.get("protocol")

    errors: list[str] = []
    warnings: list[str] = []

    if not name or not isinstance(name, str):
        errors.append("pack.yaml: missing or invalid 'name'")
    if not version:
        errors.append("pack.yaml: missing 'version'")
    if protocol is None:
        errors.append("pack.yaml: missing 'protocol'")
    elif protocol not in SUPPORTED_PROTOCOLS:
        errors.append(
            f"pack.yaml: protocol {protocol!r} is not supported by this engine "
            f"(supported: {sorted(SUPPORTED_PROTOCOLS)})"
        )

    result = PackValidationResult(
        name=str(name or ""), version=str(version or ""), protocol=int(protocol) if isinstance(protocol, int) else 0,
        errors=errors, warnings=warnings,
    )

    if errors:
        # pack.yaml itself is broken — no point validating workflows/contracts.
        return result

    workflows_dir = pack_root / "workflows"
    workflow_files = sorted(workflows_dir.glob("*.yaml")) if workflows_dir.is_dir() else []
    result.workflow_files = workflow_files

    step_dirs = _check_contract_dirs(pack_root, errors, warnings)
    result.step_dirs = step_dirs

    if errors:
        return result

    # Reuse validate_workflow.py / parser.py against the pack in isolation —
    # swap ORCHESTRATOR_CONFIG (and neutralize the two env vars that take
    # precedence over it in parser._contract_search_dirs) for the duration.
    saved_config = os.environ.get("ORCHESTRATOR_CONFIG")
    saved_wf_dir = os.environ.pop("ORCHESTRATOR_WORKFLOW_DIR", None)
    saved_override = os.environ.pop("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", None)
    os.environ["ORCHESTRATOR_CONFIG"] = str(pack_root)
    try:
        from orchestrator_next.validate_workflow import validate_workflow

        for wf_path in workflow_files:
            schema_name = wf_path.stem
            try:
                validate_workflow(schema_name, repo_root)
            except SystemExit:
                errors.append(f"workflows/{wf_path.name}: failed validate-workflow checks")
    finally:
        if saved_config is None:
            os.environ.pop("ORCHESTRATOR_CONFIG", None)
        else:
            os.environ["ORCHESTRATOR_CONFIG"] = saved_config
        if saved_wf_dir is not None:
            os.environ["ORCHESTRATOR_WORKFLOW_DIR"] = saved_wf_dir
        if saved_override is not None:
            os.environ["ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE"] = saved_override

    result.errors = errors
    result.warnings = warnings
    return result


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

def _load_receipts(root: Path) -> dict:
    receipts_path = root / RECEIPTS_FILENAME
    if not receipts_path.is_file():
        return {}
    with open(receipts_path, encoding="utf-8") as f:
        return json.load(f) or {}


def _save_receipts(root: Path, receipts: dict) -> None:
    receipts_path = root / RECEIPTS_FILENAME
    with open(receipts_path, "w", encoding="utf-8") as f:
        json.dump(receipts, f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------
# pack add
# --------------------------------------------------------------------------

def _existing_step_ids(root: Path) -> set[str]:
    steps_dir = root / "steps"
    if not steps_dir.is_dir():
        return set()
    return {p.name for p in steps_dir.iterdir() if p.is_dir() and p.name != "lib"}


def _existing_workflow_names(root: Path) -> set[str]:
    workflows_dir = root / "workflows"
    if not workflows_dir.is_dir():
        return set()
    return {p.stem for p in workflows_dir.glob("*.yaml")}


def _relative_files(src_dir: Path) -> list[Path]:
    """All files under src_dir, relative to src_dir."""
    return sorted(p.relative_to(src_dir) for p in src_dir.rglob("*") if p.is_file())


def _git_head_sha(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, env=_clean_git_env(),
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _warn_if_gitignored(repo: Path, relpath: Path) -> None:
    """pack add vendors files meant to be committed; warn if the repo's
    .gitignore would silently swallow `git add` on them (e.g. the common
    blanket `.orchestrator/` ignore pattern used for state dirs)."""
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", str(relpath)],
        capture_output=True, env=_clean_git_env(),
    )
    if result.returncode == 0:
        print(
            f"warning: {relpath} is covered by {repo}/.gitignore — "
            f"`git add` won't pick it up. Use `git add -f {relpath}` or add a "
            f"`!{relpath}` negation to .gitignore.",
            file=sys.stderr,
        )


def pack_add(source: str, *, repo_root: str | None = None, force: bool = False) -> str:
    """Validate then install a pack from a local path or git URL into
    `<cwd>/.orchestrator/config/` — vendored and meant to be committed into
    whatever repo `pack add` was run from.

    `force`: overwrite an already-installed pack (upgrade) instead of
    refusing on receipt/collision conflicts.

    Returns the installed pack name.
    """
    repo_root = repo_root or os.getcwd()
    root = Path.cwd() / ".orchestrator" / "config"
    root.mkdir(parents=True, exist_ok=True)

    clone_dir_ctx: tempfile.TemporaryDirectory | None = None
    try:
        if _looks_like_git_url(source):
            clone_dir_ctx = tempfile.TemporaryDirectory(prefix="orchestrator-pack-")
            pack_root = Path(clone_dir_ctx.name) / "clone"
            _clone_shallow(source, pack_root)
        else:
            pack_root = Path(source).expanduser().resolve()
            if not pack_root.is_dir():
                raise PackError(f"pack source not found: {pack_root}")

        commit = _git_head_sha(pack_root)

        result = validate_pack(pack_root, repo_root)
        if not result.ok:
            raise PackError(
                f"pack '{result.name or source}' failed validation:\n"
                + "\n".join(f"  - {e}" for e in result.errors)
            )
        for w in result.warnings:
            print(f"warning: {w}", file=sys.stderr)

        receipts = _load_receipts(root)
        if result.name in receipts and not force:
            raise PackError(
                f"pack '{result.name}' is already installed — run "
                f"`orchestrator pack remove {result.name}` first to upgrade, "
                f"or pass --force"
            )

        # Conflict check across both workflows/ and steps/ before copying anything.
        new_workflow_names = {p.stem for p in result.workflow_files}
        new_step_ids = {d.name for d in result.step_dirs}

        existing_workflows = _existing_workflow_names(root)
        existing_steps = _existing_step_ids(root)
        if force:
            # Re-installing the same pack shouldn't collide with its own
            # previously-installed files.
            existing_workflows -= new_workflow_names
            existing_steps -= new_step_ids

        collisions = sorted(
            (new_workflow_names & existing_workflows)
            | (new_step_ids & existing_steps)
        )
        if collisions and not force:
            raise PackError(
                f"pack '{result.name}' conflicts with already-installed workflows/steps: "
                + ", ".join(collisions)
            )

        # Copy workflows/ and steps/ (including steps/lib/ if present) —
        # validate-then-copy-all-or-nothing: nothing above this point wrote
        # to the config root.
        installed_files: list[str] = []

        workflows_src = pack_root / "workflows"
        if workflows_src.is_dir():
            dest_workflows = root / "workflows"
            dest_workflows.mkdir(parents=True, exist_ok=True)
            for rel in _relative_files(workflows_src):
                src_file = workflows_src / rel
                dest_file = dest_workflows / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                installed_files.append(str((Path("workflows") / rel).as_posix()))

        steps_src = pack_root / "steps"
        if steps_src.is_dir():
            dest_steps = root / "steps"
            dest_steps.mkdir(parents=True, exist_ok=True)
            for rel in _relative_files(steps_src):
                src_file = steps_src / rel
                dest_file = dest_steps / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                installed_files.append(str((Path("steps") / rel).as_posix()))

        # models.yaml is a starter only — never overwrite a repo's own routing.
        models_src = pack_root / "models.yaml"
        dest_models = root / "models.yaml"
        if models_src.is_file() and not dest_models.exists():
            shutil.copy2(models_src, dest_models)
            installed_files.append("models.yaml")

        receipts[result.name] = {
            "version": result.version,
            "protocol": result.protocol,
            "source": source,
            "commit": commit,
            "files": installed_files,
        }
        _save_receipts(root, receipts)

        rel_root = root.relative_to(Path.cwd())
        _warn_if_gitignored(Path.cwd(), rel_root)
        print(f"next: git add {rel_root} && git commit")

        return result.name
    finally:
        if clone_dir_ctx is not None:
            clone_dir_ctx.cleanup()


# --------------------------------------------------------------------------
# pack remove
# --------------------------------------------------------------------------

def pack_remove(name: str) -> None:
    root = Path.cwd() / ".orchestrator" / "config"

    receipts = _load_receipts(root)
    if name not in receipts:
        raise PackError(f"no installed pack named '{name}' (see `orchestrator pack list`)")

    entry = receipts.pop(name)
    for rel in entry.get("files", []):
        file_path = root / rel
        if file_path.is_file():
            file_path.unlink()

    # Prune now-empty directories under steps/<id>/ and workflows/, but never
    # remove workflows/ or steps/ themselves (direct children of root) — other
    # packs live there.
    removed_dirs: set[Path] = set()
    for rel in entry.get("files", []):
        removed_dirs.add((root / rel).parent)
    for d in sorted(removed_dirs, key=lambda p: -len(p.parts)):
        while d.parent != root and d.is_dir() and not any(d.iterdir()):
            parent = d.parent
            d.rmdir()
            d = parent

    _save_receipts(root, receipts)


# --------------------------------------------------------------------------
# pack list
# --------------------------------------------------------------------------

def pack_list() -> list[dict]:
    """Read-only — no untracked-root restriction. Works against any config root,
    including this checkout's own git-tracked config/.
    """
    root = config_root()
    receipts = _load_receipts(root)

    rows = []
    for name, entry in sorted(receipts.items()):
        rows.append({
            "name": name,
            "version": entry.get("version", ""),
            "protocol": entry.get("protocol", ""),
            "source": entry.get("source", ""),
            "files": len(entry.get("files", [])),
        })

    # Bundled "core" pack: config/pack.yaml ships in the config root itself
    # but was never `pack add`-ed, so it has no receipt. Synthesize a row by
    # reading its pack.yaml and counting live files, unless a receipt with
    # the same name already exists (e.g. core was pack-add'ed elsewhere).
    core_pack_yaml = root / "pack.yaml"
    if core_pack_yaml.is_file():
        try:
            with open(core_pack_yaml, encoding="utf-8") as f:
                core_data = yaml.safe_load(f) or {}
        except yaml.YAMLError:
            core_data = {}
        core_name = core_data.get("name")
        if core_name and core_name not in receipts:
            file_count = 0
            for sub in ("workflows", "steps"):
                sub_dir = root / sub
                if sub_dir.is_dir():
                    file_count += sum(1 for p in sub_dir.rglob("*") if p.is_file())
            rows.append({
                "name": core_name,
                "version": core_data.get("version", ""),
                "protocol": core_data.get("protocol", ""),
                "source": "(bundled)",
                "files": file_count,
            })
            rows.sort(key=lambda r: r["name"])

    return rows


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

def _parse_flags(argv: list[str]) -> tuple[list[str], bool]:
    """Split --force out of positional args."""
    positional: list[str] = []
    force = False
    for arg in argv:
        if arg == "--force":
            force = True
        else:
            positional.append(arg)
    return positional, force


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: orchestrator pack <add|remove|list> [args]\n"
            "  orchestrator pack add <path|git-url> [--force]\n"
            "  orchestrator pack remove <name>\n"
            "  orchestrator pack list",
            file=sys.stderr,
        )
        return 1

    subcmd = argv[0]
    try:
        if subcmd == "add":
            rest, force = _parse_flags(argv[1:])
            if len(rest) < 1:
                print("usage: orchestrator pack add <path|git-url> [--force]", file=sys.stderr)
                return 1
            name = pack_add(rest[0], force=force)
            print(f"installed pack '{name}'")
            return 0

        if subcmd == "remove":
            rest, _force = _parse_flags(argv[1:])
            if len(rest) < 1:
                print("usage: orchestrator pack remove <name>", file=sys.stderr)
                return 1
            pack_remove(rest[0])
            print(f"removed pack '{rest[0]}'")
            return 0

        if subcmd == "list":
            rows = pack_list()
            if not rows:
                print("no packs installed")
                return 0
            header = ("NAME", "VERSION", "PROTOCOL", "SOURCE", "FILES")
            table_rows = [
                (r["name"], str(r["version"]), str(r["protocol"]), r["source"], str(r["files"]))
                for r in rows
            ]
            widths = [max(map(len, col)) for col in zip(header, *table_rows)]
            for row in (header, *table_rows):
                print("  ".join(f"{cell:<{w}}" for cell, w in zip(row, widths)))
            return 0

        print(f"unknown pack subcommand: {subcmd}", file=sys.stderr)
        return 1
    except ConfigRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
