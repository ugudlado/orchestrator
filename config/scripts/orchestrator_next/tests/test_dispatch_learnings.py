"""
Tests for dispatch.py _load_learnings (ORC-96): project.yaml loader edge cases.

Pins UC-E1 / UC-E2 — missing file, absent key, non-list, malformed YAML,
YAML date flattening, non-dict item filtering.
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

from orchestrator_next.dispatch import _load_learnings


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
