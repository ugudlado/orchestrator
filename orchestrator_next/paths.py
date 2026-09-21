"""Anchor resolution for the orchestrator engine — paths for config and state."""
from __future__ import annotations

import os
from pathlib import Path


def _cli_root() -> Path:
    """Repo root where this package is installed.

    orchestrator_next/paths.py → .parent is the package dir, .parent.parent is
    the repo root. resolve() follows a symlinked install on $PATH to the real
    checkout (same mechanism as bin/orchestrator's realpath).
    """
    return Path(__file__).resolve().parent.parent


class ConfigRootError(RuntimeError):
    """Raised when no config root is set — resolution is explicit-only."""


class WorkflowRefError(RuntimeError):
    """Raised when a workflow name / pack/workflow ref cannot be resolved."""


def pack_root() -> Path:
    """Global downloaded base-role pack (~/.orchestrator/pack)."""
    return Path.home() / ".orchestrator" / "pack"


def bundled_config_root() -> Path:
    """The config/ dir alongside a dev checkout of the engine repo."""
    return _cli_root() / "config"


def repo_root_from_env() -> Path | None:
    raw = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    return Path(raw) if raw else None


#: Synthetic pack label for a root the caller pointed at explicitly.
DEFAULT_PACK = "default"

CONFIG_HINT = (
    "point the engine at a pack root: `--config <path>` on `start`, "
    "or the ORCHESTRATOR_CONFIG environment variable"
)


def config_root_with_source() -> tuple[Path, str]:
    """Resolve the active config root, plus a label for which source won.

    Explicit only: the pack root is an input to the engine, never something it
    goes looking for. ``--config`` on ``start`` sets ORCHESTRATOR_CONFIG for
    the process, and ``start`` persists the resolved root in the run doc so
    every later verb reads it back off the run rather than the environment.
    """
    explicit = os.environ.get("ORCHESTRATOR_CONFIG")
    if explicit:
        return Path(explicit), "env"
    raise ConfigRootError("no workflow config root set — " + CONFIG_HINT)


def config_root() -> Path:
    """Resolve the active config root (workflows/, steps/, models.yaml)."""
    root, _source = config_root_with_source()
    return root


def list_workflows(
    repo_root: Path | None = None,
) -> dict[str, list[tuple[str, Path]]]:
    """Map bare workflow name → [(pack_name, config_root), ...]."""
    try:
        root = config_root()
    except ConfigRootError:
        return {}
    out: dict[str, list[tuple[str, Path]]] = {}
    wf_dir = root / "workflows"
    if not wf_dir.is_dir():
        return out
    for path in sorted(wf_dir.glob("*.yaml")):
        out.setdefault(path.stem, []).append((DEFAULT_PACK, root))
    return out


def resolve_workflow_ref(
    ref: str,
    repo_root: Path | None = None,
) -> tuple[str, str, Path]:
    """Resolve a workflow name → (pack, workflow, config_root).

    One config root, so the name is a bare workflow name and the pack label is
    always ``DEFAULT_PACK``. Pack qualification (``mypack/feature``) is gone
    with multi-pack auto-resolution: whoever runs the engine names the root.
    """
    ref = (ref or "").strip()
    if not ref or "/" in ref or ".." in ref:
        raise WorkflowRefError(
            f"workflow ref must be a bare workflow name (got {ref!r})"
        )
    root = config_root()
    if not (root / "workflows" / f"{ref}.yaml").is_file():
        available = ", ".join(sorted(list_workflows())) or "(none)"
        raise WorkflowRefError(
            f"unknown workflow {ref!r} in {root / 'workflows'} "
            f"(available: {available})"
        )
    return DEFAULT_PACK, ref, root


# ---------------------------------------------------------------------------
# Phase 2.1 — engine-owned run base paths
# ---------------------------------------------------------------------------
def run_base(state_raw: dict) -> Path:
    """The checkout a run's paths hang off: its worktree, else its repo root."""
    base = os.path.expanduser(str(state_raw.get("worktree_path") or "")) or str(
        state_raw.get("repo_root") or ""
    )
    return Path(base) if base else Path.cwd()


def run_slug(state_raw: dict) -> str:
    return str(state_raw.get("slug") or state_raw.get("change_id") or "")


def run_dir(slug: str, repo_root: str | Path) -> Path:
    """Engine-owned per-run directory: ``<repo>/.orchestrator/runs/<slug>/``."""
    return Path(repo_root) / ".orchestrator" / "runs" / slug


def artifacts_dir(state_raw: dict, artifacts_root: str | None = None) -> Path:
    """Where a run's named artifacts live.

    Default is ``run_dir(slug)/artifacts/``. A recipe may override with
    ``artifacts_root: spec/changes/{slug}`` — a template resolved against the
    run's worktree (or repo root when there is none).
    """
    base = run_base(state_raw)
    slug = run_slug(state_raw)
    if artifacts_root:
        rendered = str(artifacts_root).format(slug=slug)
        path = Path(rendered)
        return path if path.is_absolute() else base / path
    return run_dir(slug, base) / "artifacts"


def scratch_dir(state_raw: dict) -> Path:
    """Throwaway per-run workspace — gitignored, discarded on archive."""
    return run_dir(run_slug(state_raw), run_base(state_raw)) / "scratch"


def new_run_id() -> str:
    """A sortable, stdlib-only run identifier (UUIDv7 — time-ordered)."""
    import uuid

    return str(uuid.uuid7())


def pack_sha(config_root_path: str | Path) -> str:
    """Identify the exact pack a run was seeded from.

    The sha256 of the pack's workflow YAML files: always populated, always
    changes when the pack does, and computed without shelling out to git.
    """
    import hashlib

    root = Path(config_root_path)
    digest = hashlib.sha256()
    wf_dir = root / "workflows"
    if wf_dir.is_dir():
        for path in sorted(wf_dir.glob("*.yaml")):
            try:
                digest.update(path.name.encode("utf-8"))
                digest.update(path.read_bytes())
            except OSError:
                continue
    return digest.hexdigest()
