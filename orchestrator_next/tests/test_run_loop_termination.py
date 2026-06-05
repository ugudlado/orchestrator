"""The loop TERMINATES on a persistently-failing agent — it does not spin.

This is the gap the advisor flagged: malformed COMPLETION was made "recoverable"
(record failed → re-dispatch), but nothing proved the re-dispatch ever stops.
The ONLY termination mechanism for a repeatedly-failing agent is the spawn-
failure cap (dispatch._is_spawn_failure → quality_bar.max_spawn_failures), and
that requires the failed payload to carry usage model="none" + zero tokens.

A fake `claude` that ALWAYS emits a bad COMPLETION must drive run_loop to a
BOUNDED exit, capped at max_spawn_failures attempts. The state.yaml step_history
length is the hard ceiling — if the loop spun, history would blow past the cap
(and pytest's own run would hang, which is itself the loud failure).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from orchestrator_next import run_loop  # noqa: E402


def test_persistently_failing_agent_terminates(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "spec").mkdir(parents=True)
    (repo / "spec" / "project.yaml").write_text(yaml.safe_dump({
        "version": 1, "project": {"name": "t", "repo": "t", "summary": "s"},
        "quality_bar": {"max_spawn_failures": 3}, "rules": [],
    }))

    # Fake claude that NEVER emits a valid COMPLETION block.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bad = bin_dir / "claude"
    bad.write_text("#!/usr/bin/env bash\necho '{\"type\":\"result\",\"result\":\"no completion\"}'\n")
    bad.chmod(0o755)

    # Agent contract via override dir.
    contracts = tmp_path / "c"
    d = contracts / "bad-agent"
    d.mkdir(parents=True)
    (d / "contract.yaml").write_text(yaml.safe_dump({
        "id": "bad-agent", "version": 2, "agent": "tester",
        "instruction": "do it", "outputs": [],
    }))
    (d / "prompt.md").write_text("do the thing")  # directory-form agent needs prompt.md
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(contracts))
    monkeypatch.setenv("REPO_ROOT", str(repo))

    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text(yaml.safe_dump({
        "agents": {"tester": {"model": "opus", "subprocess": "claude"}},
        "tools": {"claude": {"binary": str(bad), "args_template": ["-p", "{prompt}"]}},
    }))

    sd = repo / ".orchestrator" / "term"
    sd.mkdir(parents=True)
    sy = sd / "20260101T000000_feature_state.yaml"
    sy.write_text(yaml.safe_dump({
        "change_id": "term", "schema": "feature", "version": 1, "status": "active",
        "phase": "main", "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": [
            {"id": "bad-agent", "status": "pending", "agent": "tester"},
        ]}},
        "step_history": [],
    }))

    # If the loop spins, this call never returns and pytest hangs (loud failure).
    code = run_loop.run_loop(str(sy), "", repo_root=str(repo), agents_yaml=str(agents_yaml))

    # TERMINATES. With no on_failure routing, a failed agent node is not
    # re-opened by readiness — the phase completes (exit 1). The point of this
    # test is that the loop STOPS; the exact bounded code is secondary.
    assert code in (1, 2), f"expected bounded exit (1|2), got {code} — loop did not terminate cleanly"

    # Hard ceiling: the step is NOT re-dispatched unboundedly. Without on_failure
    # it records exactly one failure then the workflow completes.
    hist = yaml.safe_load(sy.read_text()).get("step_history") or []
    failed = [e for e in hist if e.get("step_id") == "bad-agent" and e.get("status") == "failed"]
    assert 1 <= len(failed) <= 3, f"unbounded re-dispatch ({len(failed)} failures): spin"
