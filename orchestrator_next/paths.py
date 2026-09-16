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


PACK_GIT_URL = "https://github.com/ugudlado/skills.git"
WORKFLOW_CONFIG_GIT_URL = "https://github.com/ugudlado/workflows.git"
PACK_DOWNLOAD_HINT = (
    "pull a workflow pack into the repo: "
    f"orchestrator config pull {WORKFLOW_CONFIG_GIT_URL} [pack-name]"
)

# Synthetic pack name used when a single legacy flat/legacy-config root is present.
LEGACY_FLAT_PACK = "default"


def pack_root() -> Path:
    """Global downloaded base-role pack (~/.orchestrator/pack)."""
    return Path.home() / ".orchestrator" / "pack"


def engine_data_dir() -> Path:
    """Engine-owned data shipped in the wheel (pricing rates, models seed)."""
    return Path(__file__).resolve().parent / "data"


def bundled_config_root() -> Path:
    """The config/ dir alongside a dev checkout of the engine repo."""
    return _cli_root() / "config"


def repo_root_from_env() -> Path | None:
    raw = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    return Path(raw) if raw else None


def list_config_packs(repo_root: Path | None = None) -> list[tuple[str, Path]]:
    """Named config packs under ``<repo>/.orchestrator/<pack>/``.

    A pack is a directory that contains ``workflows/``. Ticket state dirs
    (``.orchestrator/<slug>/``) are skipped because they have no workflows/.

    Also accepts legacy layouts as a single pack named ``default``:
    ``.orchestrator/workflows/`` (flat) or ``.orchestrator/config/workflows/``.
    """
    root = repo_root if repo_root is not None else repo_root_from_env()
    if root is None:
        return []
    orch = Path(root) / ".orchestrator"
    if not orch.is_dir():
        return []

    packs: list[tuple[str, Path]] = []
    for child in sorted(orch.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "workflows").is_dir():
            packs.append((child.name, child))

    if packs:
        return packs

    # Legacy single-root layouts (pre multi-pack).
    for candidate, name in (
        (orch, LEGACY_FLAT_PACK),
        (orch / "config", LEGACY_FLAT_PACK),
    ):
        if (candidate / "workflows").is_dir():
            return [(name, candidate)]
    return []


def _vendored_config_root(repo_root: Path) -> Path | None:
    """Pick a default vendored pack when ORCHESTRATOR_CONFIG is unset.

    - Exactly one pack → that pack
    - Multiple packs → None (caller must use pack/workflow or set ORCHESTRATOR_CONFIG)
    """
    packs = list_config_packs(repo_root)
    if len(packs) == 1:
        return packs[0][1]
    return None


def config_root_with_source() -> tuple[Path, str]:
    """Resolve the active config root, plus a label for which source won.

    Resolution order (first hit wins):
      1. ORCHESTRATOR_CONFIG — explicit config root ("env")
      2. Exactly one ``<repo>/.orchestrator/<pack>/`` ("vendored")

    Plan phase 3.2 deleted the two implicit fallbacks (the engine checkout's
    ``config/`` and ``~/.orchestrator/pack/config``): a run must be able to say
    exactly which pulled, locked pack it came from, and a silent global
    fallback makes that unanswerable.

    Multiple vendored packs with no env → ConfigRootError (use pack/workflow).
    """
    explicit = os.environ.get("ORCHESTRATOR_CONFIG")
    if explicit:
        return Path(explicit), "env"
    repo_root = repo_root_from_env()
    if repo_root is not None:
        packs = list_config_packs(repo_root)
        if len(packs) == 1:
            return packs[0][1], "vendored"
        if len(packs) > 1:
            names = ", ".join(p[0] for p in packs)
            raise ConfigRootError(
                f"multiple config packs under {repo_root / '.orchestrator'} ({names}); "
                "pass a workflow as <pack>/<workflow> or set ORCHESTRATOR_CONFIG — "
                + PACK_DOWNLOAD_HINT
            )
    raise ConfigRootError(
        "no workflow config found (checked ORCHESTRATOR_CONFIG and "
        "repo .orchestrator/<pack>/) — " + PACK_DOWNLOAD_HINT
    )


def config_root() -> Path:
    """Resolve the active config root (workflows/, steps/, models.yaml)."""
    root, _source = config_root_with_source()
    return root


def list_workflows(
    repo_root: Path | None = None,
) -> dict[str, list[tuple[str, Path]]]:
    """Map bare workflow name → [(pack_name, config_root), ...]."""
    packs = list_config_packs(repo_root)
    if not packs:
        # Fall back to the active config_root (ORCHESTRATOR_CONFIG).
        try:
            root = config_root()
        except ConfigRootError:
            return {}
        return _workflows_in_root(LEGACY_FLAT_PACK, root)

    out: dict[str, list[tuple[str, Path]]] = {}
    for pack_name, root in packs:
        for wf, hits in _workflows_in_root(pack_name, root).items():
            out.setdefault(wf, []).extend(hits)
    return out


def _workflows_in_root(pack_name: str, root: Path) -> dict[str, list[tuple[str, Path]]]:
    wf_dir = root / "workflows"
    out: dict[str, list[tuple[str, Path]]] = {}
    if not wf_dir.is_dir():
        return out
    for path in sorted(wf_dir.glob("*.yaml")):
        out.setdefault(path.stem, []).append((pack_name, root))
    return out


def resolve_workflow_ref(
    ref: str,
    repo_root: Path | None = None,
) -> tuple[str, str, Path]:
    """Resolve ``feature`` or ``mypack/feature`` → (pack, workflow, config_root).

    Bare names work only when unique across all packs. Ambiguous bare names and
    unknown refs raise WorkflowRefError with a clear hint.
    """
    ref = (ref or "").strip()
    if not ref or ref.startswith("/") or ".." in ref.split("/"):
        raise WorkflowRefError(f"invalid workflow ref: {ref!r}")

    if "/" in ref:
        pack_name, _, workflow = ref.partition("/")
        if not pack_name or not workflow or "/" in workflow:
            raise WorkflowRefError(
                f"workflow ref must be <pack>/<workflow> or <workflow> (got {ref!r})"
            )
        packs = {name: root for name, root in list_config_packs(repo_root)}
        if pack_name not in packs:
            # Allow resolving against env/checkout when pack list is empty.
            if not packs and os.environ.get("ORCHESTRATOR_CONFIG"):
                root = Path(os.environ["ORCHESTRATOR_CONFIG"])
                if (root / "workflows" / f"{workflow}.yaml").is_file():
                    return pack_name, workflow, root
            available = ", ".join(sorted(packs)) or "(none)"
            raise WorkflowRefError(
                f"unknown config pack {pack_name!r} (packs: {available})"
            )
        root = packs[pack_name]
        if not (root / "workflows" / f"{workflow}.yaml").is_file():
            raise WorkflowRefError(
                f"workflow {workflow!r} not found in pack {pack_name!r} "
                f"({root / 'workflows'})"
            )
        return pack_name, workflow, root

    # Bare workflow name — must be unique.
    index = list_workflows(repo_root)
    hits = index.get(ref, [])
    if len(hits) == 1:
        pack_name, root = hits[0]
        return pack_name, ref, root
    if len(hits) > 1:
        opts = ", ".join(f"{p}/{ref}" for p, _ in hits)
        raise WorkflowRefError(
            f"workflow {ref!r} is not unique; use one of: {opts}"
        )
    raise WorkflowRefError(f"unknown workflow {ref!r}")


def workflow_mode(name: str, repo_root: Path | None = None) -> str:
    """Return the workflow YAML's top-level ``mode`` (default ``"ticket"``)."""
    import yaml

    try:
        _, wf, cfg = resolve_workflow_ref(name, repo_root)
    except WorkflowRefError:
        return "ticket"
    schema_yaml = cfg / "workflows" / f"{wf}.yaml"
    if not schema_yaml.is_file():
        return "ticket"
    doc = yaml.safe_load(schema_yaml.read_text(encoding="utf-8")) or {}
    return str(doc.get("mode") or "ticket")


# ---------------------------------------------------------------------------
# Phase 2.1 — engine-owned run base paths
# ---------------------------------------------------------------------------
#: Line added to a consumer repo's .gitignore so scratch never gets committed.
SCRATCH_GITIGNORE_LINE = ".orchestrator/runs/*/scratch/"


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


def ensure_scratch_gitignored(repo_root: str | Path) -> bool:
    """Append the scratch ignore line to ``<repo>/.gitignore`` when absent.

    Returns True when the file was modified. Best-effort: an unwritable repo
    is not an error the engine should fail a run over.
    """
    path = Path(repo_root) / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return False
    if SCRATCH_GITIGNORE_LINE in text.splitlines():
        return False
    suffix = "" if (not text or text.endswith("\n")) else "\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{suffix}# orchestrator per-run scratch (never committed)\n"
                    f"{SCRATCH_GITIGNORE_LINE}\n")
    except OSError:
        return False
    return True


def new_run_id() -> str:
    """A sortable, stdlib-only run identifier (UUIDv7 — time-ordered)."""
    import uuid

    return str(uuid.uuid7())


def pack_sha(config_root_path: str | Path) -> str:
    """Identify the exact pack a run was seeded from.

    A git checkout answers with its HEAD sha; anything else (a vendored copy,
    a wheel-bundled pack) falls back to the sha256 of the workflow YAML files,
    so the field is always populated and always changes when the pack does.
    """
    import hashlib
    import subprocess

    root = Path(config_root_path)
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

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
