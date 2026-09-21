"""State storage: one YAML document per run, with compare-and-swap.

`state.yaml` is the entire memory of a run. This module owns the two
primitives the rest of the engine funnels everything through — `load` and
`save` — so no other module touches the file directly.

Optimistic concurrency
----------------------
`load()` returns `(doc, token)`; `save()` requires that token back and raises
`StateConflictError` if the file moved underneath you. The token is the
pre-write bytes, so a concurrent writer is a loud retry rather than a silently
dropped step outcome. That is data-loss protection, not an optimization.

A handle is a filesystem path (or a `file://` URL). Remote backends are gone:
the driver names the state directory, and a run is a file inside it.
"""
from __future__ import annotations

import os
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

import yaml

# An opaque compare-and-swap token: the pre-write bytes.
Token = Any


class StateConflictError(RuntimeError):
    """The document changed between `load` and `save`."""


class StateNotFoundError(FileNotFoundError):
    """No run document at this handle."""


# ---------------------------------------------------------------------------
# Handle parsing
# ---------------------------------------------------------------------------
class StateHandle:
    """Where a run lives: a filesystem path."""

    __slots__ = ("scheme", "location", "run_id", "raw")

    def __init__(self, scheme: str, location: str, run_id: str, raw: str) -> None:
        self.scheme = scheme
        self.location = location
        self.run_id = run_id
        self.raw = raw

    @property
    def is_file(self) -> bool:
        return self.scheme == "file"

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"StateHandle({self.scheme}:{self.location} #{self.run_id})"


def parse_handle(handle: str | os.PathLike[str] | StateHandle) -> StateHandle:
    """Parse a path (or `file://` URL) into a StateHandle."""
    if isinstance(handle, StateHandle):
        return handle
    raw = str(handle)
    parsed = urllib.parse.urlsplit(raw)
    scheme = parsed.scheme.lower()

    # A Windows drive letter ("C:\...") parses as scheme "c" — treat any
    # single-character scheme as a path, not a URL.
    if not scheme or len(scheme) == 1:
        return StateHandle("file", str(Path(raw)), "", raw)

    if scheme == "file":
        return StateHandle("file", urllib.parse.unquote(parsed.path), "", raw)

    raise ValueError(
        f"unsupported state URL scheme {scheme!r} in {raw!r} — "
        f"state is a file path; pass --state <dir> to name the directory"
    )


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` for the compare-and-swap.

    A sidecar lock file, so the lock never depends on the state document
    existing yet. `flock` serializes writers in this process and across
    processes; where it is unavailable the compare-and-swap degrades to the
    unlocked form rather than failing the write.
    """
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover — non-POSIX
        yield
        return
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _summary(run_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "slug": doc.get("slug") or "",
        "change_id": doc.get("change_id") or "",
        "ticket_id": doc.get("ticket_id") or "",
        "schema": doc.get("schema") or "",
        "status": doc.get("status") or "",
        "steps": len(doc.get("step_history") or []),
    }


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------
class StateStore(Protocol):
    def load(self, handle: StateHandle) -> tuple[dict[str, Any], Token]: ...
    def save(self, handle: StateHandle, doc: dict[str, Any], token: Token) -> Token: ...
    def create(self, handle: StateHandle, doc: dict[str, Any]) -> Token: ...
    def exists(self, handle: StateHandle) -> bool: ...
    def list_runs(self, handle: StateHandle) -> list[dict[str, Any]]: ...


def open_store(handle: str | os.PathLike[str] | StateHandle) -> tuple[StateStore, StateHandle]:
    """Resolve a handle and return the store that serves it."""
    h = parse_handle(handle)
    if h.scheme == "file":
        return FileStore(), h
    raise ValueError(f"no store for scheme {h.scheme!r}")


# ---------------------------------------------------------------------------
# File backend
# ---------------------------------------------------------------------------
class FileStore:
    """One YAML document per run. The token is the pre-write bytes.

    Write, re-parse, restore the previous bytes if the re-parse fails — so a
    half-written document never becomes the run's memory. A file that changed
    since it was read raises StateConflictError rather than being overwritten.
    """

    def load(self, handle: StateHandle) -> tuple[dict[str, Any], Token]:
        path = Path(handle.location)
        if not path.is_file():
            raise StateNotFoundError(f"state.yaml not found: {path}")
        pre = path.read_bytes()
        doc = yaml.safe_load(pre.decode("utf-8")) or {}
        if not isinstance(doc, dict):
            raise ValueError(f"state.yaml is not a YAML mapping: {path}")
        return doc, pre

    def save(self, handle: StateHandle, doc: dict[str, Any], token: Token) -> Token:
        path = Path(handle.location)
        with _exclusive(path):
            # Compare INSIDE the lock: reading the file, finding it unchanged
            # and then writing is only safe if no one can interleave between
            # the two. Without the lock several concurrent writers all read
            # the same bytes, all compare equal, and all "win" — the lost
            # update this token exists to prevent.
            if token is not None and path.is_file():
                if path.read_bytes() != token:
                    raise StateConflictError(
                        f"{path} changed since it was read — reload and retry"
                    )
            text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                                  allow_unicode=True)
            try:
                yaml.safe_load(text)
            except yaml.YAMLError:
                # Never let an unparseable document become the run's memory.
                raise
            # Atomic replace: a reader never sees a half-written document.
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return path.read_bytes()

    def create(self, handle: StateHandle, doc: dict[str, Any]) -> Token:
        path = Path(handle.location)
        path.parent.mkdir(parents=True, exist_ok=True)
        return self.save(handle, doc, None)

    def exists(self, handle: StateHandle) -> bool:
        return Path(handle.location).is_file()

    def list_runs(self, handle: StateHandle) -> list[dict[str, Any]]:
        base = Path(handle.location)
        root = base if base.is_dir() else base.parent
        out = []
        for p in sorted(root.glob("*.yaml")):
            try:
                doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(doc, dict):
                out.append(_summary(p.stem, doc))
        return out


# ---------------------------------------------------------------------------
# Convenience API used by the engine
# ---------------------------------------------------------------------------
def load_doc(handle: str | StateHandle) -> tuple[dict[str, Any], Token, StateHandle]:
    store, h = open_store(handle)
    doc, token = store.load(h)
    return doc, token, h


def save_doc(handle: str | StateHandle, doc: dict[str, Any], token: Token) -> Token:
    store, h = open_store(handle)
    return store.save(h, doc, token)
