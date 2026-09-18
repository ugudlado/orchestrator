"""orchestrator_next.judge — hard fallback to None on every failure mode."""
from __future__ import annotations

import sys
import types

import pytest

from orchestrator_next import judge


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
