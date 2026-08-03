"""ORC-117 T-3: generic payload-root hoist in run_loop._agent_payload."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.run_loop import _agent_payload  # noqa: E402


def _action(step_id="implement"):
    return {"step_id": step_id, "phase": "main", "model": "sonnet"}


def test_novel_root_key_hoisted():
    """A novel top-level key moves into outputs and is removed from root."""
    completion = {"outputs": {"reason": "test"}, "custom_key": "v", "step_id": "implement", "phase": "main", "status": "completed"}
    payload = _agent_payload(_action(), completion, {}, None)
    assert payload["outputs"]["custom_key"] == "v"
    assert "custom_key" not in payload


def test_reserved_keys_not_hoisted():
    """Reserved protocol keys stay at root, not hoisted into outputs."""
    completion = {
        "step_id": "s", "phase": "p", "status": "completed",
        "agent": "a", "agent_id": "aid", "attempt": 1,
        "started_at": "t", "usage": {}, "outputs": {"reason": "test"}, "evidence": {}, "state_patch": {},
    }
    payload = _agent_payload(_action(), completion, {}, None)
    for key in ("step_id", "phase", "status", "agent", "agent_id", "attempt",
                "started_at", "usage", "evidence", "state_patch"):
        assert key not in payload["outputs"], f"reserved key {key!r} was hoisted into outputs"


def test_legacy_three_keys_still_hoisted():
    """The three original whitelisted keys still get hoisted (regression coverage)."""
    completion = {
        "outputs": {"reason": "test"},
        "learn_result": {"a": 1},
        "phase_review_report": {"verdict": "pass"},
        "discovery_result": {},
        "step_id": "s", "phase": "p", "status": "completed",
    }
    payload = _agent_payload(_action(), completion, {}, None)
    assert payload["outputs"]["learn_result"] == {"a": 1}
    assert payload["outputs"]["phase_review_report"] == {"verdict": "pass"}
    assert payload["outputs"]["discovery_result"] == {}


def test_no_overwrite_of_existing_outputs_key():
    """An existing key in outputs is not overwritten by a same-name root key."""
    completion = {
        "outputs": {"reason": "test", "foo": "keep"},
        "foo": "drop",
        "step_id": "s", "phase": "p", "status": "completed",
    }
    payload = _agent_payload(_action(), completion, {}, None)
    assert payload["outputs"]["foo"] == "keep"
