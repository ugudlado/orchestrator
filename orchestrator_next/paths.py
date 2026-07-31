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


PACK_GIT_URL = "https://github.com/ugudlado/orchestrator.git"
PACK_DOWNLOAD_HINT = (
    f"download the workflow pack: git clone --depth 1 {PACK_GIT_URL} ~/.orchestrator/pack "
    "(update later with: git -C ~/.orchestrator/pack pull)"
)


def pack_root() -> Path:
    """Global downloaded workflow pack — a clone of the config repo. Carries
    config/ and skills/ side by side, downloaded once like the CLI itself."""
    return Path.home() / ".orchestrator" / "pack"


def bundled_config_root() -> Path:
    """The config/ dir alongside a dev checkout of the engine repo. The wheel
    deliberately ships NO config — installs get workflows from the downloaded
    pack (~/.orchestrator/pack) or a repo-vendored .orchestrator/config."""
    return _cli_root() / "config"


def config_root_with_source() -> tuple[Path, str]:
    """Resolve the workflow-config root, plus a label for which source won.

    Resolution order (first hit wins, no layering/merging), repo→global:
      1. ORCHESTRATOR_CONFIG  — points at the config/ dir directly ("env")
      2. <repo_root>/.orchestrator/config/ — vendored pack, if it has workflows/
         (repo_root from ORCHESTRATOR_REPO_ROOT or REPO_ROOT env) ("vendored")
      3. config/ of a dev checkout of the engine repo ("checkout")
      4. ~/.orchestrator/pack/config/ — downloaded global pack ("pack")

    No hit → hard error carrying the pack download one-liner.

    INVARIANT: this function owns the trailing `config` segment. Callers must
    join only what's *below* it (e.g. config_root() / "steps"), NOT
    config_root() / "config" / "steps" — that double-joins and breaks
    resolution.
    """
    explicit = os.environ.get("ORCHESTRATOR_CONFIG")
    if explicit:
        return Path(explicit), "env"
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
    if repo_root:
        vendored = Path(repo_root) / ".orchestrator" / "config"
        if (vendored / "workflows").is_dir():
            return vendored, "vendored"
    checkout = bundled_config_root()
    if (checkout / "workflows").is_dir():
        return checkout, "checkout"
    pack = pack_root() / "config"
    if (pack / "workflows").is_dir():
        return pack, "pack"
    raise ConfigRootError(
        f"no workflow config found (checked ORCHESTRATOR_CONFIG, repo .orchestrator/config, {pack}) — "
        + PACK_DOWNLOAD_HINT
    )


def config_root() -> Path:
    """Resolve the workflow-config root: the directory holding workflows/,
    steps/, and agents.yaml. See config_root_with_source() for the ordered
    resolution rule and source labels; doctor uses that to report which
    source resolved.
    """
    root, _source = config_root_with_source()
    return root
