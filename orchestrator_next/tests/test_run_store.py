"""RunStore: Redis-backed store, mandatory (no file-store fallback)."""
from __future__ import annotations

import pytest

from orchestrator_next.run_store import RedisRunStore, open_store
from orchestrator_next.tests.acp_redis_fake import FakeRedis


def test_save_load_delete_round_trip():
    store = RedisRunStore(FakeRedis())
    assert store.load("run-1") is None

    store.save("run-1", '{"a": 1}')
    assert store.load("run-1") == '{"a": 1}'
    assert "run-1" in store.list_ids()

    store.save("run-1", '{"a": 2}')
    assert store.load("run-1") == '{"a": 2}'

    store.delete("run-1")
    assert store.load("run-1") is None
    assert "run-1" not in store.list_ids()


def test_open_store_raises_without_redis_url(tmp_path, monkeypatch):
    from orchestrator_next.sessions import RedisRequiredError

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_ACP_REDIS_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RedisRequiredError):
        open_store()


def test_open_store_uses_redis_when_configured(monkeypatch):
    import orchestrator_next.sessions as acp
    from orchestrator_next.sessions import reset_redis_client_cache

    fake = FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    reset_redis_client_cache()
    monkeypatch.setattr(acp, "_redis_client", lambda: fake)
    store = open_store()
    assert isinstance(store, RedisRunStore)
    assert store.client is fake


def test_open_store_raises_on_misconfigured_redis(monkeypatch):
    import orchestrator_next.sessions as acp
    from orchestrator_next.sessions import RedisRequiredError, reset_redis_client_cache

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    reset_redis_client_cache()
    monkeypatch.setattr(acp, "_redis_client", lambda: None)
    with pytest.raises(RedisRequiredError):
        open_store()
