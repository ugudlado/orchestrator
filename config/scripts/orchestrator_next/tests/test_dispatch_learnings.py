"""
Tests for dispatch.py learnings helpers (ORC-96).

T-1: _load_learnings — project.yaml loader edge cases (UC-E1 / UC-E2).
T-2: _relevant_learnings — informational exclusion + tag matching (UC-2, AC-4).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from orchestrator_next.dispatch import _load_learnings, _relevant_learnings


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
