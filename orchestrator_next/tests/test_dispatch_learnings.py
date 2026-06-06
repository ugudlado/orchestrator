"""
Tests for dispatch.py learnings helpers and injection sites (ORC-96).

T-1: _load_learnings — project.yaml loader edge cases (UC-E1 / UC-E2).
T-2: _relevant_learnings — informational exclusion + tag matching (UC-2, AC-4).
T-3: dispatch() — agent path injects, fresh run: omits, resume injects (UC-1, UC-E3).
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.dispatch import _load_learnings, _relevant_learnings, dispatch
from orchestrator_next.parser import load_state


def _make_state_raw(repo_root: Path, **extra: object) -> dict:
    """Minimal state_raw dict with repo_root for _project_yaml_path."""
    state: dict = {"repo_root": str(repo_root), "change_id": "learnings-test"}
    state.update(extra)
    return state


def _write_project_yaml(repo_root: Path, content: dict | str) -> Path:
    spec_dir = repo_root / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / "project.yaml"
    if isinstance(content, str):
        path.write_text(content)
    else:
        path.write_text(yaml.safe_dump(content, sort_keys=False))
    return path


class TestLoadLearningsMissingOrEmpty:
    def test_no_project_yaml_returns_empty_list(self, tmp_path: Path) -> None:
        """No resolvable spec/project.yaml → []."""
        result = _load_learnings(_make_state_raw(tmp_path))
        assert result == []

    def test_learnings_key_absent_returns_empty_list(self, tmp_path: Path) -> None:
        """project.yaml present but learnings: absent → []."""
        _write_project_yaml(tmp_path, {"version": 1, "rules": []})
        result = _load_learnings(_make_state_raw(tmp_path))
        assert result == []

    def test_learnings_scalar_returns_empty_list(self, tmp_path: Path) -> None:
        """learnings: scalar (not a list) → []."""
        _write_project_yaml(tmp_path, {"learnings": "not-a-list"})
        result = _load_learnings(_make_state_raw(tmp_path))
        assert result == []

    def test_learnings_dict_returns_empty_list(self, tmp_path: Path) -> None:
        """learnings: mapping (not a list) → []."""
        _write_project_yaml(tmp_path, {"learnings": {"id": "x", "rule": "y"}})
        result = _load_learnings(_make_state_raw(tmp_path))
        assert result == []


class TestLoadLearningsMalformed:
    def test_malformed_yaml_returns_empty_list(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unreadable YAML → [] (stderr warning allowed)."""
        _write_project_yaml(tmp_path, "learnings:\n  - id: broken\n    rule: [\n")
        result = _load_learnings(_make_state_raw(tmp_path))
        assert result == []
        err = capsys.readouterr().err
        assert "[dispatch] warning:" in err or err == ""


class TestLoadLearningsHappyPath:
    def test_yaml_date_coerced_to_string(self, tmp_path: Path) -> None:
        """learned: 2026-04-09 (YAML date) → JSON-serializable string."""
        _write_project_yaml(
            tmp_path,
            {
                "learnings": [
                    {
                        "id": "date-rule",
                        "rule": "Always flatten dates.",
                        "learned": date(2026, 4, 9),
                    }
                ]
            },
        )
        result = _load_learnings(_make_state_raw(tmp_path))
        json.dumps(result)
        assert len(result) == 1
        assert result[0]["learned"] == "2026-04-09"
        assert isinstance(result[0]["learned"], str)

    def test_non_dict_items_dropped(self, tmp_path: Path) -> None:
        """Non-dict entries in learnings list are filtered out."""
        _write_project_yaml(
            tmp_path,
            {
                "learnings": [
                    "bare-string",
                    42,
                    None,
                    {"id": "keep-me", "rule": "valid dict entry"},
                ]
            },
        )
        result = _load_learnings(_make_state_raw(tmp_path))
        assert result == [{"id": "keep-me", "rule": "valid dict entry"}]


# --- _relevant_learnings (T-2) ------------------------------------------------


def _learning(id_suffix: str, **fields: object) -> dict:
    entry: dict = {"id": f"rule-{id_suffix}", "rule": f"Rule {id_suffix}."}
    entry.update(fields)
    return entry


class TestRelevantLearningsInformational:
    def test_informational_excluded_behavioral_retained(self) -> None:
        """kind: informational is skipped; other entries kept."""
        learnings = [
            _learning("info", kind="informational", rule="Benchmark refs only."),
            _learning("behavior", rule="Always run tests before commit."),
        ]
        result = _relevant_learnings(learnings, "developer", "implement")
        assert result == [learnings[1]]


class TestRelevantLearningsUniversal:
    @pytest.mark.parametrize(
        ("agent_name", "phase"),
        [
            ("developer", "implement"),
            ("reviewer", "specify"),
            ("discoverer", "complete"),
        ],
    )
    def test_untagged_included_for_every_agent_and_phase(
        self, agent_name: str, phase: str
    ) -> None:
        """No agents:/phases: → universal for any (agent, phase)."""
        entry = _learning("universal")
        result = _relevant_learnings([entry], agent_name, phase)
        assert result == [entry]


class TestRelevantLearningsAgentFilter:
    def test_agents_tag_includes_matching_agent_excludes_other(self) -> None:
        """agents: [developer] → developer yes, reviewer no."""
        entry = _learning("dev-only", agents=["developer"])
        assert _relevant_learnings([entry], "developer", "implement") == [entry]
        assert _relevant_learnings([entry], "reviewer", "implement") == []


class TestRelevantLearningsPhaseFilter:
    def test_phases_tag_includes_matching_phase_excludes_other(self) -> None:
        """phases: [implement] → implement yes, specify no."""
        entry = _learning("impl-only", phases=["implement"])
        assert _relevant_learnings([entry], "developer", "implement") == [entry]
        assert _relevant_learnings([entry], "developer", "specify") == []


class TestRelevantLearningsCombinedTags:
    def test_both_agents_and_phases_must_match(self) -> None:
        """agents: + phases: → both required."""
        entry = _learning("both", agents=["developer"], phases=["implement"])
        assert _relevant_learnings([entry], "developer", "implement") == [entry]
        assert _relevant_learnings([entry], "reviewer", "implement") == []
        assert _relevant_learnings([entry], "developer", "specify") == []


class TestRelevantLearningsOrder:
    def test_order_preserved_in_returned_list(self) -> None:
        """Selection preserves input order."""
        learnings = [
            _learning("first"),
            _learning("info", kind="informational"),
            _learning("second", agents=["developer"]),
            _learning("third", phases=["implement"]),
            _learning("fourth", agents=["developer"], phases=["implement"]),
        ]
        result = _relevant_learnings(learnings, "developer", "implement")
        assert [item["id"] for item in result] == [
            "rule-first",
            "rule-second",
            "rule-third",
            "rule-fourth",
        ]


# --- dispatch() injection sites (T-3) -----------------------------------------


def _sample_learnings_project() -> dict:
    """Behavioral + informational + agent-tagged entries for filter assertions."""
    return {
        "learnings": [
            {"id": "behavior-1", "rule": "Always run tests before commit."},
            {
                "id": "info-1",
                "kind": "informational",
                "rule": "Benchmark refs only.",
            },
            {
                "id": "dev-only",
                "rule": "Developer-specific rule.",
                "agents": ["developer"],
            },
        ]
    }


def _informational_only_project() -> dict:
    return {
        "learnings": [
            {
                "id": "info-only",
                "kind": "informational",
                "rule": "Reference data only.",
            },
        ]
    }


def _write_agent_contract(steps_dir: Path, step_id: str, *, agent: str = "developer") -> None:
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(
        textwrap.dedent(
            f"""\
            id: {step_id}
            agent: {agent}
            instruction: Run {step_id}.
            rules: []
            inputs: []
            outputs: []
            """
        )
    )
    (step_dir / "prompt.md").write_text(f"Run {step_id}.")


def _write_run_contract(steps_dir: Path, step_id: str, script_path: Path) -> None:
    step_dir = steps_dir / step_id
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "contract.yaml").write_text(
        textwrap.dedent(
            f"""\
            id: {step_id}
            run: {script_path}
            inputs: []
            outputs: []
            rules: []
            """
        )
    )


def _pending_node(step_id: str, *, agent: str = "developer") -> dict:
    return {
        "id": step_id,
        "status": "pending",
        "agent": agent,
        "goal": f"Run {step_id}",
        "inputs": [],
        "outputs": [],
        "rules": [],
        "depends_on": [],
    }


def _in_progress_history_row(
    step_id: str,
    *,
    phase: str = "main",
    agent: str = "developer",
    attempt: int = 1,
) -> dict:
    return {
        "step_id": step_id,
        "phase": phase,
        "status": "in_progress",
        "agent": agent,
        "attempt": attempt,
        "started_at": "2026-01-01T00:00:00Z",
    }


def _setup_dispatch_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict,
    *,
    project_yaml: dict | None = None,
    steps: list[tuple[str, str]] | None = None,
    run_scripts: dict[str, Path] | None = None,
) -> str:
    """Write contracts, optional project.yaml, state.yaml; return state path."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps_dir))

    run_scripts = run_scripts or {}
    for step_id, kind in steps or []:
        if kind == "agent":
            _write_agent_contract(steps_dir, step_id)
        elif kind == "run":
            script = run_scripts.get(step_id)
            if script is None:
                script = tmp_path / f"{step_id}.sh"
                script.write_text("#!/usr/bin/env bash\nexit 0\n")
                script.chmod(0o755)
            _write_run_contract(steps_dir, step_id, script)

    if project_yaml is not None:
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "project.yaml").write_text(
            yaml.safe_dump(project_yaml, sort_keys=False)
        )

    state.setdefault("repo_root", str(tmp_path))
    state.setdefault("worktree_path", str(tmp_path))
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(state_path)


class TestDispatchLearningsInjection:
    """End-to-end dispatch(load_state(sp), sp) learnings key behavior (T-3)."""

    def test_agent_path_injects_filtered_learnings(self, tmp_path, monkeypatch) -> None:
        """AC-1: agent-path dispatch attaches policy-filtered learnings."""
        step_id = "learnings-agent-step"
        sp = _setup_dispatch_fixture(
            tmp_path,
            monkeypatch,
            {
                "change_id": "orc96-agent",
                "phase": "main",
                "workflow_plan": {
                    "main": {"nodes": [_pending_node(step_id)], "filtered": []}
                },
                "step_history": [],
            },
            project_yaml=_sample_learnings_project(),
            steps=[(step_id, "agent")],
        )
        action, code = dispatch(load_state(sp), sp)

        assert code == 0
        assert "agent" in action
        assert "learnings" in action
        ids = [item["id"] for item in action["learnings"]]
        assert ids == ["behavior-1", "dev-only"]
        assert action["learnings"] == _relevant_learnings(
            _load_learnings({"repo_root": str(tmp_path), "worktree_path": str(tmp_path)}),
            "developer",
            "main",
        )

    def test_agent_path_no_project_yaml_learnings_empty_exit_0(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC-4: missing project.yaml → learnings: [], exit 0."""
        step_id = "learnings-agent-step"
        sp = _setup_dispatch_fixture(
            tmp_path,
            monkeypatch,
            {
                "change_id": "orc96-no-yaml",
                "phase": "main",
                "workflow_plan": {
                    "main": {"nodes": [_pending_node(step_id)], "filtered": []}
                },
                "step_history": [],
            },
            project_yaml=None,
            steps=[(step_id, "agent")],
        )
        action, code = dispatch(load_state(sp), sp)

        assert code == 0
        assert action.get("learnings") == []

    def test_fresh_run_path_omits_learnings_key(self, tmp_path, monkeypatch) -> None:
        """AC-3: fresh inline run: dispatch has no learnings key."""
        step_id = "learnings-run-step"
        sp = _setup_dispatch_fixture(
            tmp_path,
            monkeypatch,
            {
                "change_id": "orc96-run",
                "phase": "main",
                "workflow_plan": {
                    "main": {"nodes": [_pending_node(step_id, agent=None)], "filtered": []}
                },
                "step_history": [],
            },
            project_yaml=_sample_learnings_project(),
            steps=[(step_id, "run")],
        )
        action, code = dispatch(load_state(sp), sp)

        assert code == 0
        assert "run" in action
        assert "agent" not in action
        assert "learnings" not in action

    def test_informational_only_project_yaml_empty_learnings(
        self, tmp_path, monkeypatch
    ) -> None:
        """Informational-only corpus → action['learnings'] == []."""
        step_id = "learnings-agent-step"
        sp = _setup_dispatch_fixture(
            tmp_path,
            monkeypatch,
            {
                "change_id": "orc96-info-only",
                "phase": "main",
                "workflow_plan": {
                    "main": {"nodes": [_pending_node(step_id)], "filtered": []}
                },
                "step_history": [],
            },
            project_yaml=_informational_only_project(),
            steps=[(step_id, "agent")],
        )
        action, code = dispatch(load_state(sp), sp)

        assert code == 0
        assert action.get("learnings") == []

    def test_resume_agent_path_carries_learnings(self, tmp_path, monkeypatch) -> None:
        """Resume of in_progress agent step mirrors fresh agent learnings injection."""
        step_id = "learnings-agent-step"
        sp = _setup_dispatch_fixture(
            tmp_path,
            monkeypatch,
            {
                "change_id": "orc96-resume-agent",
                "phase": "main",
                "workflow_plan": {
                    "main": {"nodes": [_pending_node(step_id)], "filtered": []}
                },
                "step_history": [_in_progress_history_row(step_id)],
            },
            project_yaml=_sample_learnings_project(),
            steps=[(step_id, "agent")],
        )
        action, code = dispatch(load_state(sp), sp)

        assert code == 0
        assert action.get("is_resume") is True
        assert "learnings" in action
        ids = [item["id"] for item in action["learnings"]]
        assert ids == ["behavior-1", "dev-only"]

    def test_resume_inline_run_path_carries_learnings_as_built(
        self, tmp_path, monkeypatch
    ) -> None:
        """OQ-A: resume of in_progress run: step still sets learnings (as-built)."""
        step_id = "learnings-run-step"
        sp = _setup_dispatch_fixture(
            tmp_path,
            monkeypatch,
            {
                "change_id": "orc96-resume-run",
                "phase": "main",
                "workflow_plan": {
                    "main": {
                        "nodes": [_pending_node(step_id, agent=None)],
                        "filtered": [],
                    }
                },
                "step_history": [
                    _in_progress_history_row(step_id, agent=None)
                ],
            },
            project_yaml=_sample_learnings_project(),
            steps=[(step_id, "run")],
        )
        action, code = dispatch(load_state(sp), sp)

        assert code == 0
        assert action.get("is_resume") is True
        assert "run" not in action
        # Resume branch has no agent-vs-run guard; universal learnings still attach.
        assert "learnings" in action
        assert [item["id"] for item in action["learnings"]] == ["behavior-1"]
