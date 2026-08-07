"""RunStore — durable backing for session/run state. Redis, mandatory.

One key per run id, holding the JSON/YAML payload the caller persists. No
file-store fallback: a run's state lives in the RunStore for as long as it's
alive; on completion it's archived (renamed, TTL removed), never deleted —
the archived key is the machine-readable audit trail. Nothing is ever
written into the repo.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class RunStore(Protocol):
    def load(self, run_id: str, *, archived: bool = False) -> str | None: ...
    def save(self, run_id: str, text: str) -> None: ...
    def delete(self, run_id: str) -> None: ...
    def list_ids(self, *, archived: bool = False) -> list[str]: ...
    def lock(self, run_id: str) -> bool: ...
    def refresh_lock(self, run_id: str) -> None: ...
    def unlock(self, run_id: str) -> None: ...
    def archive(self, run_id: str) -> None: ...


# Refreshed on every save; overridable for cloud multi-day sessions.
SESSION_TTL = int(os.environ.get("ORCHESTRATOR_ACP_SESSION_TTL", 14 * 86400))

# ponytail: fixed lock TTL, refreshed once per drive_loop iteration (a step
# taking longer than this between iterations loses the lock) — raise this or
# refresh more often if a single step ever runs past ~15 minutes.
LOCK_TTL = 900

REDIS_KEY_PREFIX = "orc:run:live:"
REDIS_ARCHIVE_PREFIX = "orc:run:archive:"
REDIS_LOCK_PREFIX = "orc:run:lock:"


class RedisRunStore:
    """Key ``orc:run:live:<id>`` → payload text, on a Redis client."""

    def __init__(self, client) -> None:
        self.client = client

    def _key(self, run_id: str) -> str:
        return f"{REDIS_KEY_PREFIX}{run_id}"

    def _archive_key(self, run_id: str) -> str:
        return f"{REDIS_ARCHIVE_PREFIX}{run_id}"

    def _lock_key(self, run_id: str) -> str:
        return f"{REDIS_LOCK_PREFIX}{run_id}"

    def load(self, run_id: str, *, archived: bool = False) -> str | None:
        key = self._archive_key(run_id) if archived else self._key(run_id)
        return self.client.get(key)

    def save(self, run_id: str, text: str) -> None:
        self.client.set(self._key(run_id), text, ex=SESSION_TTL)

    def delete(self, run_id: str) -> None:
        self.client.delete(self._key(run_id))

    def list_ids(self, *, archived: bool = False) -> list[str]:
        prefix = self._archive_key("") if archived else self._key("")
        return [k[len(prefix):] for k in self.client.scan_iter(match=f"{prefix}*")]

    def lock(self, run_id: str) -> bool:
        return bool(self.client.set(self._lock_key(run_id), "1", nx=True, ex=LOCK_TTL))

    def refresh_lock(self, run_id: str) -> None:
        self.client.set(self._lock_key(run_id), "1", ex=LOCK_TTL)

    def unlock(self, run_id: str) -> None:
        self.client.delete(self._lock_key(run_id))

    def archive(self, run_id: str) -> None:
        """Rename the live key to the archive namespace and drop its TTL —
        archived runs persist until manual cleanup, not automatic expiry.

        A no-op if the live key is already gone (e.g. archived already) —
        real Redis raises on RENAME of a missing key; check first so callers
        don't need their own existence guard.
        """
        if self.client.get(self._key(run_id)) is None:
            return
        self.client.rename(self._key(run_id), self._archive_key(run_id))
        self.client.persist(self._archive_key(run_id))


def open_store() -> RunStore:
    """Redis-backed RunStore. Raises RedisRequiredError if unreachable.

    No fallback of any kind — a misconfigured or absent Redis fails loudly
    with a start hint, rather than silently degrading to local files.
    """
    from orchestrator_next.sessions import RedisRequiredError, redis_url

    if not redis_url():
        raise RedisRequiredError(
            "Redis is required (REDIS_URL or ORCHESTRATOR_ACP_REDIS_URL unset). "
            "Start one: `brew services start redis` or "
            "`docker run -d -p 6379:6379 redis`."
        )
    from orchestrator_next.sessions import _redis_client

    client = _redis_client()
    if client is None:
        raise RedisRequiredError(
            "REDIS_URL is set but the redis client is unavailable "
            "(package missing or connection failed). Start one: "
            "`brew services start redis` or `docker run -d -p 6379:6379 redis`."
        )
    return RedisRunStore(client)


def _state_root() -> Path:
    """Machine-local materialization dir — never inside a repo, never gitignored
    (nothing to ignore: it isn't under any repo)."""
    return Path(os.environ.get("ORCHESTRATOR_HOME_DIR", "~/.orchestrator")).expanduser() / "state"


def materialize(store: "RunStore", run_id: str, *, repo_root: str = "") -> Path:
    """Load ``run_id``'s state text and write it to a stable per-run path.

    Stable (not a per-call tempfile) so ``--seed-only`` followed by a separate
    ``orchestrator next``/``done`` invocation still resolves the same file —
    both look up the same run_id and land on the same path. When ``repo_root``
    is given, rebind the materialized state's ``repo_root`` to it (a resume on
    a different machine/checkout must not keep the previous machine's path).
    """
    import yaml

    text = store.load(run_id)
    if text is None:
        raise FileNotFoundError(f"no state for run_id={run_id}")
    if repo_root:
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            raw = None
        if isinstance(raw, dict):
            raw["repo_root"] = repo_root
            text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    path = _state_root() / f"{run_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def persist(store: "RunStore", run_id: str, state_path: Path | str) -> None:
    """Save the materialized file's current contents back to the store."""
    store.save(run_id, Path(state_path).read_text(encoding="utf-8"))
