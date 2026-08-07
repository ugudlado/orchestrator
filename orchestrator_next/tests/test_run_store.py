"""RunStore: Redis-backed store, mandatory (no file-store fallback)."""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next.run_store import RedisRunStore, materialize, open_store, persist
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


def test_archive_renames_and_drops_ttl():
    fake = FakeRedis()
    store = RedisRunStore(fake)
    store.save("run-1", '{"a": 1}')
    assert "run-1" in store.list_ids()

    store.archive("run-1")

    assert "run-1" not in store.list_ids()
    assert "run-1" in store.list_ids(archived=True)
    assert store.load("run-1") is None  # live key gone
    from orchestrator_next.run_store import REDIS_ARCHIVE_PREFIX
    assert fake.ttl(f"{REDIS_ARCHIVE_PREFIX}run-1") == -1  # persisted, no TTL


def test_archive_is_idempotent_when_already_archived():
    """Real Redis raises on RENAME of a missing key — archive() must guard
    so a re-entrant archive call (double-call, crash-and-retry) is a no-op
    rather than propagating a raw Redis error."""
    fake = FakeRedis()
    store = RedisRunStore(fake)
    store.save("run-1", '{"a": 1}')

    store.archive("run-1")
    store.archive("run-1")  # must not raise

    assert "run-1" in store.list_ids(archived=True)


def test_lock_refresh_extends_ttl():
    fake = FakeRedis()
    store = RedisRunStore(fake)
    assert store.lock("run-1")
    from orchestrator_next.run_store import LOCK_TTL, REDIS_LOCK_PREFIX
    assert fake.ttl(f"{REDIS_LOCK_PREFIX}run-1") == LOCK_TTL

    fake.ttls[f"{REDIS_LOCK_PREFIX}run-1"] = 1  # simulate near-expiry
    store.refresh_lock("run-1")
    assert fake.ttl(f"{REDIS_LOCK_PREFIX}run-1") == LOCK_TTL


def test_materialize_writes_stable_path_and_persist_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path))
    store = RedisRunStore(FakeRedis())
    store.save("run-1", yaml.safe_dump({"change_id": "run-1", "repo_root": "/old/machine"}))

    path = materialize(store, "run-1", repo_root="/new/machine")
    assert path == tmp_path / "state" / "run-1.yaml"
    raw = yaml.safe_load(path.read_text())
    assert raw["repo_root"] == "/new/machine"

    # Same run_id materializes to the same path (stable across --seed-only / next / done).
    path2 = materialize(store, "run-1", repo_root="/new/machine")
    assert path2 == path

    raw["status"] = "completed"
    path.write_text(yaml.safe_dump(raw))
    persist(store, "run-1", path)
    assert "completed" in store.load("run-1")


def test_materialize_raises_for_unknown_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path))
    store = RedisRunStore(FakeRedis())
    with pytest.raises(FileNotFoundError):
        materialize(store, "does-not-exist")
