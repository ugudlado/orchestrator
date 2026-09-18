"""orchestrator_next.judge — hard fallback to None on every failure mode."""
from __future__ import annotations

import sys
import types

import pytest
import yaml

from orchestrator_next import judge
from orchestrator_next.record import record


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_JUDGE", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


def test_ask_returns_none_when_flag_off(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_JUDGE", "off")
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    assert judge.ask(state={}, questions={}) is None


def test_ask_returns_none_when_key_unset(monkeypatch):
    assert judge.ask(state={}, questions={}) is None


def test_enabled_false_when_sdk_import_fails(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    real_import = __import__

    def fake_import(name, *a, **k):
        if name == "typesafe_sdk":
            raise ImportError("no sdk")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert judge.enabled() is False
    assert judge.ask(state={}, questions={}) is None


def test_ask_returns_none_when_sdk_raises(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")

    fake_sdk = types.ModuleType("typesafe_sdk")

    class _BoomClient:
        def __enter__(self):
            raise RuntimeError("network down")

        def __exit__(self, *a):
            return False

    fake_sdk.TypeSafeClient = _BoomClient
    monkeypatch.setitem(sys.modules, "typesafe_sdk", fake_sdk)
    assert judge.ask(state={"a": 1}, questions={"q": object()}) is None


def test_choice_and_ask_never_import_sdk_when_it_is_missing(tmp_path, monkeypatch):
    """Regression: judge.enabled() was true (key set, flag not off) but
    typesafe_sdk was NOT importable, and record.py's abandoned-triage path
    called judge.choice(...) as a plain argument BEFORE judge.ask() got a
    chance to short-circuit — choice() imported typesafe_sdk unguarded and
    raised ImportError instead of falling back. choice()/noul() must check
    enabled() themselves and return None, so no caller can hit the SDK
    import when it is absent."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    real_import = __import__

    def fake_import(name, *a, **k):
        if name.startswith("typesafe_sdk"):
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert judge.choice("x", {"a": "b"}) is None
    assert judge.noul("x") is None

    empty = tmp_path / "empty_contracts"
    empty.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(empty))

    state = {
        "change_id": "judge-import-guard-test",
        "phase": "main",
        "schema": "feature",
        "workflow_plan": {
            "main": {
                "nodes": [{"id": "design", "status": "in_progress", "depends_on": []}],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))

    _result, exit_code = record(
        str(path),
        {
            "step_id": "design",
            "phase": "main",
            "status": "abandoned",
            "agent": "strong",
            "outputs": {"reason": "the tool crashed mid-run"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )
    assert exit_code == 0

    raw = yaml.safe_load(path.read_text())
    assert raw["status"] == "needs_you"
    assert raw["step_history"][-1]["judge"] is None
