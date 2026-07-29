"""Tests for the optimize-prompts step (closed-loop gepa -> gates -> promote).

No test invokes a real optimizer — `prompt-optimize`/`prompt-eval` are stubs
that record their argv and replay scripted compare exit codes
(0 = gates passed, 1 = gates failed -> retry gepa, 2 = error -> abort pack).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_STEP_DIR = os.path.join(_REPO_ROOT, "config", "steps", "optimize-prompts")
_SCRIPT = os.path.join(_STEP_DIR, "optimize_prompts.py")


def _write_state(tmp_path, steps=("learn",)):
    state = {
        "change_id": "orc-42-demo",
        "ticket_id": "ORC-42",
        "step_history": [
            {"step_id": s, "phase": "main", "status": "completed", "attempt": 1}
            for s in steps
        ],
    }
    (tmp_path / "state.yaml").write_text(yaml.safe_dump(state, sort_keys=False))


def _write_pack(tmp_path, name="learn", train_rows=1):
    pack = tmp_path / "repo" / "skills" / name
    (pack / "scenarios").mkdir(parents=True)
    (pack / "SKILL.md").write_text("# skill\n")
    rows = []
    for i in range(train_rows):
        rows.append(json.dumps({
            "id": f"{name}-train-{i}",
            "scenario": f"{name} train {i}",
            "expect": ["expected"],
        }))
    (pack / "scenarios" / "train.jsonl").write_text(
        ("\n".join(rows) + "\n") if rows else ""
    )
    return pack


def _stub(tmp_path, compare_exits, promote_exit=0, gepa_exit=0):
    """One stub serving both binaries; scripted compare exits pop in order."""
    capture = tmp_path / "captured.jsonl"
    exits = tmp_path / "compare_exits.json"
    exits.write_text(json.dumps(list(compare_exits)))
    stub = tmp_path / "optimizer-stub"
    stub.write_text(f"""#!/usr/bin/env python3
import json, os, sys
capture, exits_path = {str(capture)!r}, {str(exits)!r}
cmd = sys.argv[1]
with open(capture, "a") as f:
    f.write(json.dumps({{"cmd": cmd, "argv": sys.argv[1:],
        "step_id": os.environ.get("ORCHESTRATOR_STEP_ID")}}) + "\\n")
if cmd == "gepa":
    pack = sys.argv[sys.argv.index("--pack") + 1]
    runs = os.path.join(pack, "runs")
    os.makedirs(runs, exist_ok=True)
    run_dir = os.path.join(runs, "run-%d" % len(os.listdir(runs)))
    os.makedirs(run_dir)
    print("run artifact: " + run_dir)
    sys.exit({gepa_exit})
if cmd == "compare":
    exits = json.load(open(exits_path))
    code = exits.pop(0) if exits else 0
    json.dump(exits, open(exits_path, "w"))
    sys.exit(code)
if cmd == "promote":
    sys.exit({promote_exit})
sys.exit(0)
""")
    stub.chmod(0o755)
    return stub, capture


def _run(tmp_path, env_extra):
    env = {
        "PATH": os.environ["PATH"],
        "ORCHESTRATOR_STEP_DIR": _STEP_DIR,
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path / "repo"),
        "ORCHESTRATOR_STATE_YAML_PATH": str(tmp_path / "state.yaml"),
        "ORCHESTRATOR_CHANGE_ID": "orc-42-demo",
        "ORCHESTRATOR_STEP_ID": "optimize-prompts",
        # Isolate from the real engine skills/ checkout.
        "ORCHESTRATOR_PROMPT_PATH": str(tmp_path / "repo" / "skills"),
        **env_extra,
    }
    return subprocess.run(
        [sys.executable, _SCRIPT], env=env, capture_output=True, text=True
    )


def _captured(capture):
    if not capture.exists():
        return []
    return [json.loads(l) for l in capture.read_text().splitlines() if l.strip()]


def _outputs(result):
    return json.loads(result.stdout)["outputs"]["prompt_optimize"]


def test_off_by_default(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [])
    result = _run(tmp_path, {"ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub)})

    assert result.returncode == 0
    assert _captured(capture) == []
    assert json.loads(result.stdout)["status"] == "completed"


def test_happy_path_promotes_after_both_gates(tmp_path):
    _write_state(tmp_path)
    pack = _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [0, 0])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    assert result.returncode == 0, result.stderr
    calls = _captured(capture)
    assert [c["cmd"] for c in calls] == ["gepa", "compare", "compare", "promote"]
    splits = [c["argv"][c["argv"].index("--split") + 1] for c in calls if c["cmd"] == "compare"]
    assert splits == ["dev", "holdout"]
    assert calls[-1]["argv"][1:5] == ["--pack", str(pack.resolve()), "--run-dir",
                                      str(pack.resolve() / "runs" / "run-0")]
    out = _outputs(result)
    assert out["packs"] == [{"pack": "learn", "outcome": "promoted", "attempts": 1,
                             "run_dir": str(pack.resolve() / "runs" / "run-0")}]
    # Correlation parity with eval-prompts: rows stamp the optimized step's id.
    assert {c["step_id"] for c in calls} == {"learn"}


def test_gate_failure_retries_gepa_then_promotes(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [1, 0, 0])  # dev fails once, then both pass
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    calls = _captured(capture)
    assert [c["cmd"] for c in calls] == ["gepa", "compare", "gepa", "compare", "compare", "promote"]
    assert _outputs(result)["packs"][0]["outcome"] == "promoted"
    assert _outputs(result)["packs"][0]["attempts"] == 2


def test_exhausted_retries_reports_gates_failed(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [1, 1])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
        "ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_RETRIES": "1",
    })

    calls = _captured(capture)
    assert [c["cmd"] for c in calls] == ["gepa", "compare", "gepa", "compare"]
    assert "promote" not in [c["cmd"] for c in calls]
    assert result.returncode == 0  # advisory step never fails the workflow
    assert _outputs(result)["packs"][0] == {
        "pack": "learn", "outcome": "gates-failed", "attempts": 2,
    }


def test_compare_hard_error_aborts_without_retry(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [2])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    calls = _captured(capture)
    assert [c["cmd"] for c in calls] == ["gepa", "compare"]
    out = _outputs(result)["packs"][0]
    assert out["outcome"] == "error" and "compare dev exit 2" in out["detail"]
    assert result.returncode == 0


def test_max_metric_calls_passthrough(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [0, 0])
    _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
        "ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS": "10",
    })

    gepa = [c for c in _captured(capture) if c["cmd"] == "gepa"][0]
    assert gepa["argv"][gepa["argv"].index("--max-metric-calls") + 1] == "10"
    # Default external = max(3*mmc, floor); train=1 → floor=11, 3*10=30 → 30
    assert gepa["argv"][gepa["argv"].index("--max-external-calls") + 1] == "30"


def test_underbudget_mmc_refused_without_gepa(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path, train_rows=4)
    stub, capture = _stub(tmp_path, [])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
        "ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS": "4",
    })

    assert _captured(capture) == []
    out = _outputs(result)["packs"][0]
    assert out["outcome"] == "underbudget"
    assert "max_metric_calls 4 < floor 7" in out["detail"]


def test_gepa_noop_skips_compare_and_retry(tmp_path):
    _write_state(tmp_path)
    pack = _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [], gepa_exit=3)
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    calls = _captured(capture)
    assert [c["cmd"] for c in calls] == ["gepa"]
    out = _outputs(result)["packs"][0]
    assert out["outcome"] == "noop"
    assert out["attempts"] == 1
    assert out["run_dir"] == str(pack.resolve() / "runs" / "run-0")
    assert result.returncode == 0


def test_underbudget_external_refused_without_gepa(tmp_path):
    _write_state(tmp_path)
    _write_pack(tmp_path, train_rows=4)
    stub, capture = _stub(tmp_path, [])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
        "ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS": "32",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_EXTERNAL_CALLS": "20",
    })

    assert _captured(capture) == []
    out = _outputs(result)["packs"][0]
    assert out["outcome"] == "underbudget"
    assert "max_external_calls 20 < floor 23" in out["detail"]


def test_shared_pack_optimized_once(tmp_path):
    """Two steps resolving to the same pack must not double-optimize it."""
    _write_state(tmp_path, steps=("learn", "learn"))
    _write_pack(tmp_path)
    stub, capture = _stub(tmp_path, [0, 0])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    assert len([c for c in _captured(capture) if c["cmd"] == "gepa"]) == 1
    assert len(_outputs(result)["packs"]) == 1


def test_optimize_is_standalone_not_in_workflow_tails():
    """Optimization runs when needed (optimize workflow / cron), not per change."""
    for workflow in ("autopilot", "bugfix", "design", "feature", "implement", "patch"):
        path = os.path.join(_REPO_ROOT, "config", "workflows", f"{workflow}.yaml")
        with open(path) as f:
            steps = yaml.safe_load(f)["steps"]
        ids = [s.get("id") or s.get("prompt") if isinstance(s, dict) else s for s in steps]
        assert "optimize-prompts" not in ids, workflow

    with open(os.path.join(_REPO_ROOT, "config", "workflows", "optimize.yaml")) as f:
        assert yaml.safe_load(f)["steps"] == ["optimize-prompts"]


def test_standalone_mode_sweeps_all_scenario_packs(tmp_path):
    """A state with no completed prompt steps falls back to every pack."""
    (tmp_path / "state.yaml").write_text(
        yaml.safe_dump({"change_id": "prompts-maint", "step_history": []})
    )
    _write_pack(tmp_path, "learn")
    _write_pack(tmp_path, "review")
    stub, capture = _stub(tmp_path, [0, 0, 0, 0])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    assert result.returncode == 0, result.stderr
    gepa_packs = sorted(
        os.path.basename(c["argv"][c["argv"].index("--pack") + 1])
        for c in _captured(capture) if c["cmd"] == "gepa"
    )
    assert gepa_packs == ["learn", "review"]


def test_fresh_scenarios_gate_skips_already_optimized_pack(tmp_path):
    """A pack whose train.jsonl predates its newest run dir is skipped."""
    import time
    _write_state(tmp_path)
    pack = _write_pack(tmp_path)
    run_dir = pack / "runs" / "old-run"
    run_dir.mkdir(parents=True)
    # train.jsonl older than the run dir -> nothing new to learn from
    old = time.time() - 3600
    os.utime(pack / "scenarios" / "train.jsonl", (old, old))
    stub, capture = _stub(tmp_path, [0, 0])
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
    })

    assert _captured(capture) == []
    assert "already optimized" in json.loads(result.stdout)["evidence"]["summary"]

    # FORCE overrides the gate
    result = _run(tmp_path, {
        "ORCHESTRATOR_PROMPT_OPTIMIZE": "1",
        "ORCHESTRATOR_PROMPT_OPTIMIZE_BIN": str(stub),
        "ORCHESTRATOR_PROMPT_OPTIMIZE_FORCE": "1",
    })
    assert [c["cmd"] for c in _captured(capture)][:1] == ["gepa"]
