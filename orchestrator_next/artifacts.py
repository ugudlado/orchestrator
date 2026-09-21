"""Named-artifact resolution, hashing, and resume idempotency (plan Phase 2.3).

A step contract declares ``in:`` / ``out:`` entries; each entry that carries
``artifact: <name>`` names a file relative to the run's artifacts base (see
``paths.artifacts_dir``). This module is where those names become paths, where
the engine hashes what a step produced, and where a resume decides a node can
be skipped because nothing it reads or writes has changed.

Everything here is pure except the filesystem reads — no state mutation, so
the functions are directly unit-testable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Size of the streaming read used when hashing an artifact.
_CHUNK = 1 << 20


class ArtifactError(RuntimeError):
    """A declared artifact is missing, or its ``validate:`` script rejected it."""


def sha256_file(path: str | Path) -> str:
    """Hex sha256 of a file's contents. Raises OSError when unreadable."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_specs(io_map: dict[str, dict]) -> dict[str, dict]:
    """The subset of an ``in:``/``out:`` block that names files."""
    return {
        name: spec
        for name, spec in (io_map or {}).items()
        if spec.get("artifact")
    }


def resolve_path(spec: dict, base: Path) -> Path:
    """Absolute path for one artifact spec, relative to the artifacts base."""
    path = Path(str(spec["artifact"]))
    return path if path.is_absolute() else base / path


def relative_to_base(path: Path, base: Path) -> str:
    """``path`` as recorded in state: relative to base when it lives under it."""
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except (ValueError, OSError):
        return str(path)


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ArtifactRecord:
    """One entry of a node's ``artifacts:`` list."""
    name: str
    path: str      # relative to the artifacts base
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": self.path, "sha256": self.sha256}


def collect(
    io_map: dict[str, dict],
    base: Path,
    *,
    overrides: dict[str, Any] | None = None,
    require: bool = True,
) -> list[dict[str, str]]:
    """Hash every artifact declared in ``io_map``.

    ``overrides`` lets a structured ``done --out`` point an artifact at a path
    other than the contract default. A declared, non-optional artifact that is
    missing raises ArtifactError when ``require`` is set; an optional or
    absent-but-not-required one is simply skipped.
    """
    overrides = overrides or {}
    out: list[dict[str, str]] = []
    missing: list[str] = []
    for name, spec in sorted(artifact_specs(io_map).items()):
        override = overrides.get(name)
        path = Path(str(override)) if override else resolve_path(spec, base)
        if not path.is_absolute():
            path = base / path
        if not path.is_file():
            if require and not spec.get("optional"):
                missing.append(f"{name} ({path})")
            continue
        out.append(
            ArtifactRecord(
                name=name,
                path=relative_to_base(path, base),
                sha256=sha256_file(path),
            ).as_dict()
        )
    if missing:
        raise ArtifactError(
            "declared out: artifacts not found: " + ", ".join(missing)
        )
    return out


# ---------------------------------------------------------------------------
# resume idempotency
# ---------------------------------------------------------------------------
def hash_map(records: list[dict] | None) -> dict[str, str]:
    """``[{name, path, sha256}] → {name: sha256}``, ignoring malformed rows."""
    out: dict[str, str] = {}
    for rec in records or []:
        if isinstance(rec, dict) and rec.get("name") and rec.get("sha256"):
            out[str(rec["name"])] = str(rec["sha256"])
    return out


def current_hashes(io_map: dict[str, dict], base: Path) -> dict[str, str]:
    """``{name: sha256}`` for every declared artifact that exists on disk."""
    out: dict[str, str] = {}
    for name, spec in artifact_specs(io_map).items():
        path = resolve_path(spec, base)
        if path.is_file():
            try:
                out[name] = sha256_file(path)
            except OSError:
                continue
    return out


def is_unchanged(
    recorded_inputs: dict[str, str],
    recorded_outputs: dict[str, str],
    live_inputs: dict[str, str],
    live_outputs: dict[str, str],
) -> bool:
    """True when a completed node can be skipped on resume.

    A node is unchanged when the engine has a record of what it read and wrote
    and both still hash the same. An empty record means the node predates
    artifact recording, so it is never skipped — re-running is the safe default.
    """
    if not recorded_outputs:
        return False
    if live_outputs != recorded_outputs:
        return False
    return live_inputs == recorded_inputs


def node_is_unchanged(node: dict, contract: Any, base: Path) -> bool:
    """``is_unchanged`` applied to a plan node plus its contract.

    The node carries what the engine recorded when the step last completed;
    the contract says what the step reads and writes today.
    """
    recorded = hash_map(node.get("artifacts"))
    recorded_inputs = hash_map(node.get("input_artifacts"))
    inputs = getattr(contract, "inputs", None) or {}
    outputs = getattr(contract, "outputs", None) or {}
    return is_unchanged(
        recorded_inputs,
        recorded,
        current_hashes(inputs, base),
        current_hashes(outputs, base),
    )
