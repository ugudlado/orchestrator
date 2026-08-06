"""RunStore — durable backing for ACP session state (Redis or local file).

One key/file per session id, holding the JSON payload ACP sessions persist
(``{cwd, schema, workflow}``). File backend needs no Redis; Redis backend is
used when configured. See ``docs/plan-acp-simplify.md`` phase 3.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Protocol


class RunStore(Protocol):
    def load(self, run_id: str) -> str | None: ...
    def save(self, run_id: str, text: str) -> None: ...
    def delete(self, run_id: str) -> None: ...
    def list_ids(self) -> list[str]: ...
    def lock(self, run_id: str) -> bool: ...
    def unlock(self, run_id: str) -> None: ...


# Refreshed on every save; overridable for cloud multi-day sessions.
SESSION_TTL = int(os.environ.get("ORCHESTRATOR_ACP_SESSION_TTL", 14 * 86400))


class FileRunStore:
    """One JSON file per run under ``root/<run_id>.yaml``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.yaml"

    def _lock_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.lock"

    def load(self, run_id: str) -> str | None:
        path = self._path(run_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def save(self, run_id: str, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(run_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def delete(self, run_id: str) -> None:
        self._path(run_id).unlink(missing_ok=True)

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [p.stem for p in self.root.glob("*.yaml")]

    def lock(self, run_id: str) -> bool:
        # ponytail: stateless exclusive-create lock, no held fd — matches the
        # per-call open_store() pattern. Ceiling: a crashed holder (SIGKILL/OOM)
        # never runs the finally/unlock, so the lock is stale-after-900s
        # (matches the Redis SET NX EX ceiling) rather than held forever.
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._lock_path(run_id)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime < 900:
                    return False
                path.unlink(missing_ok=True)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (OSError, FileExistsError):
                return False
        os.close(fd)
        return True

    def unlock(self, run_id: str) -> None:
        self._lock_path(run_id).unlink(missing_ok=True)


_REDIS_PREFIX = "orc:acp:session:"
_REDIS_LOCK_PREFIX = "orc:acp:lock:"


class RedisRunStore:
    """Key ``orc:acp:session:<id>`` → payload text, on a Redis client."""

    def __init__(self, client) -> None:
        self.client = client

    def _key(self, run_id: str) -> str:
        return f"{_REDIS_PREFIX}{run_id}"

    def _lock_key(self, run_id: str) -> str:
        return f"{_REDIS_LOCK_PREFIX}{run_id}"

    def load(self, run_id: str) -> str | None:
        return self.client.get(self._key(run_id))

    def save(self, run_id: str, text: str) -> None:
        self.client.set(self._key(run_id), text, ex=SESSION_TTL)

    def delete(self, run_id: str) -> None:
        self.client.delete(self._key(run_id))

    def list_ids(self) -> list[str]:
        return [k.rsplit(":", 1)[-1] for k in self.client.scan_iter(match=f"{_REDIS_PREFIX}*")]

    def lock(self, run_id: str) -> bool:
        return bool(self.client.set(self._lock_key(run_id), "1", nx=True, ex=900))

    def unlock(self, run_id: str) -> None:
        self.client.delete(self._lock_key(run_id))


def default_file_root() -> Path:
    """Server-process cwd — NOT the session's client-declared cwd.

    Session save/load/delete must all resolve the same root regardless of
    which cwd a particular ACP client happened to pass in session/new.
    """
    return Path(os.getcwd()) / ".orchestrator" / "sessions" / "_state"


def open_store() -> RunStore:
    """Redis when configured and usable, else the file store.

    A session run works locally with NO Redis (file backend). Redis is only
    required when a URL is set but unusable — that raises, it does not
    silently fall back (misconfiguration should be loud).
    """
    from orchestrator_next.sessions import RedisRequiredError, redis_url

    if redis_url():
        from orchestrator_next.sessions import _redis_client

        client = _redis_client()
        if client is None:
            raise RedisRequiredError(
                "REDIS_URL is set but the redis client is unavailable "
                "(package missing or connection failed)."
            )
        return RedisRunStore(client)
    return FileRunStore(default_file_root())
