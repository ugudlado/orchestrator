"""The mod driver's pure decision helpers (mod/protocol.ts), run for real.

Two decisions cost a live run (01a0af3f) real work, and both are pure
functions here so they can be pinned without an engine:

* `isFinalTurn` — which `turn.complete` is a subagent's ANSWER. Recording a
  step from an `aborted` / `refusal` / `error` turn writes a partial or empty
  answer into `done` as though the agent had finished.
* `shouldRaisePopup` — whether to raise `$.ui.ask` at all. With the pane open
  both surfaces offered the same gate, the pane's Button won, and the dialog
  (which cannot be retracted) stayed on screen stale.

As in test_mod_parse_json.py there is no TS runner in this repo, so Node
>=22.6's `--experimental-strip-types` imports the real `.ts` module rather
than a Python re-implementation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

MOD_DIR = Path(__file__).resolve().parents[1] / "mod"


def _node_supports_strip_types() -> bool:
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    major, minor = (int(p) for p in out.lstrip("v").split(".")[:2])
    return (major, minor) >= (22, 6)


requires_node = pytest.mark.skipif(
    not _node_supports_strip_types(),
    reason="needs Node >=22.6 for --experimental-strip-types",
)


def _call(fn: str, *args: object) -> object:
    """Calls `fn` from the real mod/protocol.ts with JSON-encoded args."""
    script = (
        'import("./protocol.ts").then(m => {'
        "  const a = JSON.parse(process.argv[1]);"
        f"  process.stdout.write(JSON.stringify(m.{fn}(...a)));"
        "});"
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", script, "--", json.dumps(args)],
        cwd=MOD_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --- isFinalTurn ---------------------------------------------------------
# `TurnCompleteReason` is exactly `answer | aborted | refusal | error`
# (claude-code.d.ts:8432). There is no awaiting-input member: a permission
# prompt does not end a turn, so these four are the whole space.


@requires_node
def test_answer_is_final() -> None:
    assert _call("isFinalTurn", {"reason": "answer", "isAborted": False}) is True


@requires_node
@pytest.mark.parametrize("reason", ["aborted", "refusal", "error"])
def test_non_answer_reasons_are_not_final(reason: str) -> None:
    assert _call("isFinalTurn", {"reason": reason, "isAborted": False}) is False


@requires_node
def test_aborted_flag_overrides_answer_reason() -> None:
    """d.ts:8396 ties `isAborted` to `reason === 'aborted'`; belt and braces."""
    assert _call("isFinalTurn", {"reason": "answer", "isAborted": True}) is False


@requires_node
def test_missing_reason_is_not_final() -> None:
    """An engine that sent no reason must not be read as a finished answer."""
    assert _call("isFinalTurn", {}) is False


# --- isAgentFinished -----------------------------------------------------


@requires_node
def test_running_agent_is_not_finished() -> None:
    agents = [{"id": "a1", "status": "running"}]
    assert _call("isAgentFinished", agents, "a1") is False


@requires_node
@pytest.mark.parametrize("status", ["completed", "failed", "killed"])
def test_terminal_statuses_are_finished(status: str) -> None:
    agents = [{"id": "a1", "status": status}]
    assert _call("isAgentFinished", agents, "a1") is True


@requires_node
def test_unlisted_agent_is_not_finished() -> None:
    """A workflow's agents carry ids no listing names (d.ts:141): absence is
    not evidence the loop stopped, so it must not end the wait."""
    assert _call("isAgentFinished", [{"id": "other", "status": "completed"}], "a1") is False


# --- shouldRaisePopup ----------------------------------------------------


@requires_node
def test_popup_raised_only_when_pane_closed() -> None:
    assert _call("shouldRaisePopup", False) is True


@requires_node
def test_popup_suppressed_when_pane_open() -> None:
    assert _call("shouldRaisePopup", True) is False


# --- isoStamp ------------------------------------------------------------


@requires_node
def test_iso_stamp_is_utc_z() -> None:
    """record.py parses `started_at` with fromisoformat after replacing a
    trailing Z, so the stamp must carry one."""
    stamp = _call("isoStamp", 0)
    assert stamp == "1970-01-01T00:00:00.000Z"
