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


PACK_GIT_URL = "https://github.com/ugudlado/prompt-packs.git"
WORKFLOW_CONFIG_GIT_URL = "https://github.com/ugudlado/workflow-config.git"
PACK_DOWNLOAD_HINT = (
    f"git clone --depth 1 {PACK_GIT_URL} ~/.orchestrator/pack "
    "(base roles); or pull workflows into the repo: "
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
      3. Engine checkout ``config/`` ("checkout")
      4. ``~/.orchestrator/pack/config/`` ("pack")

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
    checkout = bundled_config_root()
    if (checkout / "workflows").is_dir():
        return checkout, "checkout"
    pack = pack_root() / "config"
    if (pack / "workflows").is_dir():
        return pack, "pack"
    raise ConfigRootError(
        f"no workflow config found (checked ORCHESTRATOR_CONFIG, "
        f"repo .orchestrator/<pack>/, {pack}) — "
        + PACK_DOWNLOAD_HINT
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
        # Fall back to active config_root (checkout / global pack / env).
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
