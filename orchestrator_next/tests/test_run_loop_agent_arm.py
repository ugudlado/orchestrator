"""End-to-end canary for the in-process agent arm (run_loop).

No real model: a fake `claude` binary echoes a canned COMPLETION block. Drives
the full chain — build_prompt → invoke_tool → split_stdout → parse_completion →
_agent_payload → record — plus the dispatch loop and Python seeding.

This is the gate set in PLAN-python-driver.md: the agent arm must execute, not
just import.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
_SCRIPTS_PARENT = str(_HERE.parents[1])  # parent of orchestrator_next package
sys.path.insert(0, _SCRIPTS_PARENT)

from orchestrator_next import run_loop  # noqa: E402


def _fake_claude(bin_dir: Path) -> Path:
    """A stand-in `claude` binary: ignore args, print a valid claude-json
    envelope whose assistant text ends with a COMPLETION block."""
    fake = bin_dir / "claude"
    # split_stdout's claude adapter expects JSON; embed COMPLETION in the text.
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "cat <<'JSON'\n"
        '{"type":"result","result":"did the work\\n\\n'
        "COMPLETION:\\n  step_id: fake-agent-step\\n  status: completed\\n"
        '  outputs:\\n    note: ok\\n",'
        '"usage":{"input_tokens":10,"output_tokens":5}}\n'
        "JSON\n"
    )
    fake.chmod(0o755)
    return fake


def test_agent_arm_runs_end_to_end(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)
    (repo / "spec" / "project.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "project": {"name": "t", "repo": "t", "summary": "s"},
        "quality_bar": {"max_spawn_failures": 3},
        "rules": [],
    }))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_claude(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    # agents.yaml: route a 'tester' agent to the fake claude.
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(yaml.safe_dump({
        "models": {"opus": {"model_id": "claude-opus-4-7", "subprocess": "claude"}},
        "tools": {"claude": {
            "binary": str(bin_dir / "claude"),
            "args_template": ["-p", "{prompt}"],
        }},
    }))

    # A minimal state with one agent step already dispatch-ready.
    state_dir = repo / ".orchestrator" / "fake"
    state_dir.mkdir(parents=True)
    state_yaml = state_dir / "20260101T000000_feature_state.yaml"
    state_yaml.write_text(yaml.safe_dump({
        "change_id": "fake", "schema": "feature", "version": 1,
        "status": "active", "phase": "main", "repo_root": str(repo),
        "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": [
            {"id": "fake-agent-step", "status": "pending", "model": "opus"},
        ]}},
        "step_history": [],
    }))

    # Build the dispatched action by hand (bypass contract loading: point the
    # node straight at run_agent_step, which is the unit under test).
    action = {
        "step_id": "fake-agent-step", "phase": "main", "attempt": 1,
        "model": "opus", "instruction": "do the work",
        "step_context": {}, "started_at": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setenv("REPO_ROOT", str(repo))

    payload = run_loop.run_agent_step(
        action, repo_root=str(repo), models_yaml=str(models_yaml),
        ticket_id="", state_raw=yaml.safe_load(state_yaml.read_text()),
        state_yaml_path=str(state_yaml), tmp_dir=tmp_path,
    )

    # The whole chain produced a well-formed done-payload.
    assert payload["step_id"] == "fake-agent-step"
    assert payload["status"] == "completed"
    assert payload["agent"] == "opus"
    assert payload["outputs"].get("note") == "ok"
    assert payload["usage"]["input_tokens"] == 10
    assert payload["usage"]["output_tokens"] == 5

    # And record() accepts the payload we built (advisor bug-watch #2/#3).
    from orchestrator_next.record import record
    result, code = record(str(state_yaml), payload)
    assert code == 0, f"record rejected agent payload: {result}"

    final = yaml.safe_load(state_yaml.read_text())
    hist = final.get("step_history") or []
    assert any(e.get("step_id") == "fake-agent-step" and e.get("status") == "completed"
               for e in hist), f"step not recorded completed: {hist}"


def test_malformed_completion_is_recoverable(tmp_path, monkeypatch):
    """LOCKED policy: a bad COMPLETION → failed payload (retryable), not a crash."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bad = bin_dir / "claude"
    bad.write_text("#!/usr/bin/env bash\necho '{\"type\":\"result\",\"result\":\"no completion here\"}'\n")
    bad.chmod(0o755)

    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(yaml.safe_dump({
        "models": {"opus": {"model_id": "claude-opus-4-7", "subprocess": "claude"}},
        "tools": {"claude": {"binary": str(bad), "args_template": ["-p", "{prompt}"]}},
    }))

    action = {
        "step_id": "s", "phase": "main", "attempt": 1, "model": "opus",
        "instruction": "x", "step_context": {},
    }
    payload = run_loop.run_agent_step(
        action, repo_root=str(tmp_path), models_yaml=str(models_yaml),
        ticket_id="", state_raw={}, state_yaml_path=str(tmp_path / "s.yaml"),
        tmp_dir=tmp_path,
    )
    assert payload["status"] == "failed", "malformed COMPLETION must be recoverable-failed, not fatal"
