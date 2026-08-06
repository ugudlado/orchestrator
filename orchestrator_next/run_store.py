"""RunStore — durable backing for session/run state. Redis, mandatory.

One key per run id, holding the JSON/YAML payload the caller persists. No
file-store fallback: a session/ticket run's state lives in the RunStore for
as long as the run is alive; nothing is ever written into the repo.
"""
from __future__ import annotations

import os
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

REDIS_KEY_PREFIX = "orc:run:live:"
REDIS_LOCK_PREFIX = "orc:run:lock:"


class RedisRunStore:
    """Key ``orc:run:live:<id>`` → payload text, on a Redis client."""

    def __init__(self, client) -> None:
        self.client = client

    def _key(self, run_id: str) -> str:
        return f"{REDIS_KEY_PREFIX}{run_id}"

    def _lock_key(self, run_id: str) -> str:
        return f"{REDIS_LOCK_PREFIX}{run_id}"

    def load(self, run_id: str) -> str | None:
        return self.client.get(self._key(run_id))

    def save(self, run_id: str, text: str) -> None:
        self.client.set(self._key(run_id), text, ex=SESSION_TTL)

    def delete(self, run_id: str) -> None:
        self.client.delete(self._key(run_id))

    def list_ids(self) -> list[str]:
        return [k.rsplit(":", 1)[-1] for k in self.client.scan_iter(match=f"{REDIS_KEY_PREFIX}*")]

    def lock(self, run_id: str) -> bool:
        return bool(self.client.set(self._lock_key(run_id), "1", nx=True, ex=900))

    def unlock(self, run_id: str) -> None:
        self.client.delete(self._lock_key(run_id))


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
