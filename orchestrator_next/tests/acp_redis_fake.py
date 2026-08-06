"""Shared fake Redis for ACP session tests."""
from __future__ import annotations

from orchestrator_next import sessions as acp
from orchestrator_next.sessions import reset_redis_client_cache


class FakeRedis:
    """Minimal dict-backed Redis stand-in (no fakeredis dependency)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    def ping(self) -> bool:
        return True

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttls[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        self.ttls.pop(key, None)
        return 1 if self.store.pop(key, None) is not None else 0

    def scan_iter(self, match: str = "*"):
        prefix = match.rstrip("*")
        for k in list(self.store):
            if k.startswith(prefix):
                yield k

    def rename(self, src: str, dst: str) -> bool:
        if src not in self.store:
            raise KeyError(f"no such key: {src}")
        self.store[dst] = self.store.pop(src)
        self.ttls[dst] = self.ttls.pop(src, None)
        return True

    def persist(self, key: str) -> bool:
        if key not in self.store:
            return False
        self.ttls[key] = None
        return True

    def ttl(self, key: str) -> int:
        if key not in self.store:
            return -2
        ex = self.ttls.get(key)
        return -1 if ex is None else ex


def install_fake_redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    reset_redis_client_cache()
    monkeypatch.setattr(acp, "_redis_client", lambda: fake)
    return fake
