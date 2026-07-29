"""Tests for the eval-prompts step (the T11 correlation call site).

The step must stay free by default and, when explicitly enabled, must feed
prompt-optimizer the (ticket_id, change_id, step_id) key. No test invokes a
real evaluation — `prompt-eval` is always a stub that records its environment.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_STEP_DIR = os.path.join(_REPO_ROOT, "config", "steps", "eval-prompts")
_SCRIPT = os.path.join(_STEP_DIR, "eval_prompts.py")


def _write_state(tmp_path, ticket_id="ORC-42", steps=("learn",)):
    state = {
        "change_id": "orc-42-demo",
        "ticket_id": ticket_id,
        "step_history": [
            {"step_id": s, "phase": "main", "status": "completed", "attempt": 1}
            for s in steps
        ],
    }
    path = tmp_path / "state.yaml"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return path


def _write_pack(tmp_path, name="learn"):
    """A skills/<name>/ dir shaped like a real prompt pack."""
    pack = tmp_path / "repo" / "skills" / name
    (pack / "scenarios").mkdir(parents=True)
    (pack / "SKILL.md").write_text("# skill\n")
    (pack / "scenarios" / "train.jsonl").write_text("")
    return pack


def _stub_eval(tmp_path):
    """A fake `prompt-eval` that appends its argv + correlation env as JSON."""
    capture = tmp_path / "captured.jsonl"
    stub = tmp_path / "prompt-eval-stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"with open({str(capture)!r}, 'a') as f:\n"
        "    f.write(json.dumps({\n"
        "        'argv': sys.argv[1:],\n"
        "        'ticket_id': os.environ.get('ORCHESTRATOR_TICKET_ID'),\n"
        "        'change_id': os.environ.get('ORCHESTRATOR_CHANGE_ID'),\n"
        "        'step_id': os.environ.get('ORCHESTRATOR_STEP_ID'),\n"
        "    }) + '\\n')\n"
    )
    stub.chmod(0o755)
    return stub, capture


def _run(tmp_path, env_extra):
    """Run the step with a minimal engine-provided env block."""
    env = {
        "PATH": os.environ["PATH"],
        # ORCHESTRATOR_STEP_DIR only — the engine guarantees it, but not
        # ORCHESTRATOR_CONFIG, so the step must work without the latter.
        "ORCHESTRATOR_STEP_DIR": _STEP_DIR,
        "ORCHESTRATOR_REPO_ROOT": str(tmp_path / "repo"),
        "ORCHESTRATOR_STATE_YAML_PATH": str(tmp_path / "state.yaml"),
        "ORCHESTRATOR_CHANGE_ID": "orc-42-demo",
        # The engine sets this to the step's OWN id; the step must override it
        # per pack so rows are not all stamped `eval-prompts`.
        "ORCHESTRATOR_STEP_ID": "eval-prompts",
        **env_extra,
    }
    return subprocess.run(
        [sys.executable, _SCRIPT], env=env, capture_output=True, text=True
    )


def _captured(capture):
    return [json.loads(line) for line in capture.read_text().splitlines() if line.strip()]


class TestSkips:
    def test_skips_when_flag_off(self, tmp_path):
        _write_state(tmp_path)
        _write_pack(tmp_path)
        stub, capture = _stub_eval(tmp_path)
        # Fully configured except the opt-in flag.
        result = _run(tmp_path, {"ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub)})

        assert result.returncode == 0
        assert not capture.exists(), "no evaluation may run while the flag is off"
        assert json.loads(result.stdout)["status"] == "completed"

    def test_skips_when_optimizer_unconfigured(self, tmp_path):
        _write_state(tmp_path)
        _write_pack(tmp_path)
        result = _run(tmp_path, {"ORCHESTRATOR_PROMPT_EVAL": "1"})

        assert result.returncode == 0
        assert "not configured" in json.loads(result.stdout)["evidence"]["summary"]

    def test_skips_when_optimizer_dir_missing(self, tmp_path):
        _write_state(tmp_path)
        _write_pack(tmp_path)
        result = _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_OPTIMIZER_DIR": str(tmp_path / "nope"),
        })

        assert result.returncode == 0
        assert "not configured" in json.loads(result.stdout)["evidence"]["summary"]

    def test_skips_pack_without_scenarios(self, tmp_path):
        _write_state(tmp_path)
        pack = tmp_path / "repo" / "skills" / "learn"
        pack.mkdir(parents=True)
        (pack / "SKILL.md").write_text("# skill\n")  # no scenarios/
        stub, capture = _stub_eval(tmp_path)
        result = _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        assert result.returncode == 0
        assert not capture.exists()
        assert "no evaluable packs" in json.loads(result.stdout)["evidence"]["summary"]


class TestInvocation:
    def test_feeds_correlation_key(self, tmp_path):
        _write_state(tmp_path, ticket_id="ORC-42")
        pack = _write_pack(tmp_path)
        stub, capture = _stub_eval(tmp_path)
        result = _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        assert result.returncode == 0, result.stderr
        rows = _captured(capture)
        assert len(rows) == 1
        assert rows[0]["ticket_id"] == "ORC-42"
        assert rows[0]["change_id"] == "orc-42-demo"
        assert rows[0]["argv"] == ["run", "--pack", str(pack.resolve())]

    def test_step_id_is_the_evaluated_step_not_this_one(self, tmp_path):
        """The whole point of the key: rows must name the step that ran."""
        _write_state(tmp_path, steps=("learn", "review"))
        _write_pack(tmp_path, "learn")
        _write_pack(tmp_path, "review")
        stub, capture = _stub_eval(tmp_path)
        result = _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        assert result.returncode == 0, result.stderr
        by_step = {r["step_id"]: r for r in _captured(capture)}
        assert set(by_step) == {"learn", "review"}
        assert "eval-prompts" not in by_step

    def test_ticket_id_is_uppercased(self, tmp_path):
        _write_state(tmp_path, ticket_id="orc-42")
        _write_pack(tmp_path)
        stub, capture = _stub_eval(tmp_path)
        _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        assert _captured(capture)[0]["ticket_id"] == "ORC-42"

    def test_only_completed_steps_are_evaluated(self, tmp_path):
        state = {
            "change_id": "orc-42-demo",
            "ticket_id": "ORC-42",
            "step_history": [
                {"step_id": "learn", "phase": "main", "status": "completed"},
                {"step_id": "review", "phase": "main", "status": "failed"},
            ],
        }
        (tmp_path / "state.yaml").write_text(yaml.safe_dump(state, sort_keys=False))
        _write_pack(tmp_path, "learn")
        _write_pack(tmp_path, "review")
        stub, capture = _stub_eval(tmp_path)
        _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        assert [r["step_id"] for r in _captured(capture)] == ["learn"]

    def test_eval_failure_does_not_fail_the_step(self, tmp_path):
        """A judge/network failure must not sink an otherwise-good workflow."""
        _write_state(tmp_path)
        _write_pack(tmp_path)
        stub = tmp_path / "failing-stub"
        stub.write_text("#!/bin/sh\nexit 3\n")
        stub.chmod(0o755)
        result = _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        assert result.returncode == 0
        assert json.loads(result.stdout)["outputs"]["prompt_eval"]["packs"] == []


class TestBudget:
    def test_split_and_cap_passthrough(self, tmp_path):
        _write_state(tmp_path)
        pack = _write_pack(tmp_path)
        stub, capture = _stub_eval(tmp_path)
        _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
            "ORCHESTRATOR_PROMPT_EVAL_SPLIT": "dev",
            "ORCHESTRATOR_PROMPT_EVAL_MAX_CALLS": "4",
        })

        assert _captured(capture)[0]["argv"] == [
            "run", "--split", "dev", "--max-external-calls", "4",
            "--pack", str(pack.resolve()),
        ]

    def test_no_budget_flags_when_unset(self, tmp_path):
        _write_state(tmp_path)
        _write_pack(tmp_path)
        stub, capture = _stub_eval(tmp_path)
        _run(tmp_path, {
            "ORCHESTRATOR_PROMPT_EVAL": "1",
            "ORCHESTRATOR_PROMPT_EVAL_BIN": str(stub),
        })

        argv = _captured(capture)[0]["argv"]
        assert "--split" not in argv and "--max-external-calls" not in argv


class TestRealStateShape:
    """Pin the two state.yaml assumptions the fixtures would otherwise beg."""

    def _archived_states(self):
        root = os.path.join(_REPO_ROOT, "spec", "changes", "archive")
        if not os.path.isdir(root):
            return []
        found = []
        for entry in sorted(os.listdir(root)):
            path = os.path.join(root, entry, "state.yaml")
            if os.path.isfile(path):
                found.append(path)
        return found

    def test_completed_is_the_terminal_status_literal(self):
        """If the engine ever renames this, the step silently evaluates nothing."""
        states = self._archived_states()
        if not states:
            pytest.skip("no archived state.yaml in this checkout")

        seen = set()
        for path in states:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            for entry in data.get("step_history") or []:
                if isinstance(entry, dict) and entry.get("status"):
                    seen.add(entry["status"])

        assert "completed" in seen, f"terminal status literal changed; saw {sorted(seen)}"

    def test_ticket_id_is_read_from_real_archives(self):
        """Real archives carry mixed case, which is why the step uppercases."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("eval_prompts", _SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for path in self._archived_states():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            raw = data.get("ticket_id")
            if raw:
                assert module.ticket_id_from_state(data) == str(raw).upper()


class TestWiring:
    @pytest.mark.parametrize(
        "workflow", ["autopilot", "bugfix", "design", "feature", "implement", "patch"]
    )
    def test_runs_after_learn(self, workflow):
        """Evaluation follows learn, with persist-learnings in between.

        persist-learnings is what actually lands learn's proposed scenarios in
        the packs, so it must run first or this step evaluates the old banks.
        """
        path = os.path.join(_REPO_ROOT, "config", "workflows", f"{workflow}.yaml")
        with open(path) as f:
            steps = yaml.safe_load(f)["steps"]
        ids = [s.get("id") or s.get("prompt") if isinstance(s, dict) else s for s in steps]

        assert "eval-prompts" in ids
        assert ids.index("eval-prompts") == ids.index("learn") + 2
        assert ids.index("persist-learnings") == ids.index("learn") + 1

    def test_complete_workflow_does_not_eval(self):
        """`complete` has no learn step, so it has nothing to evaluate."""
        path = os.path.join(_REPO_ROOT, "config", "workflows", "complete.yaml")
        with open(path) as f:
            steps = yaml.safe_load(f)["steps"]

        assert "eval-prompts" not in steps
