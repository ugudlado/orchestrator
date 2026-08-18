"""Parallel-safety check in validate-tasks-yaml.sh.

The rule this codifies (see .orchestrator/workflows/steps/design/reference/
parallel-safety.md): two tasks with no depends_on path between them may be
dispatched in the same parallel batch, so their `files` lists must be
disjoint. The engine serializes shared singletons (git index, tasks.yaml
itself) but has no way to arbitrate two edits to the same file — that has to
be caught before the plan ships.

This is advisory, not a hard gate: exit code stays 0. A workflow author who
deliberately wants two tasks racing the same file (e.g. one script bumps a
version, another appends a changelog line, both idempotent) should not be
blocked by tooling for a choice that is theirs to make.
"""
from __future__ import annotations

import os
import subprocess

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_VALIDATOR = os.path.join(
    _REPO_ROOT, ".orchestrator", "workflows", "steps", "design",
    "validate-tasks-yaml.sh",
)


def _write(tmp_path, content: dict) -> str:
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump(content, sort_keys=False, default_flow_style=False))
    return str(p)


def _run(path: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", _VALIDATOR, path], capture_output=True, text=True)


def test_validator_script_exists():
    assert os.path.isfile(_VALIDATOR), _VALIDATOR


def test_disjoint_unordered_tasks_are_silent(tmp_path):
    """No depends_on edge, no shared files: nothing to warn about."""
    doc = {
        "version": 1,
        "tasks": [
            {"id": "T-1", "title": "A", "files": ["a.py"], "verify": ["echo ok"], "depends_on": []},
            {"id": "T-2", "title": "B", "files": ["b.py"], "verify": ["echo ok"], "depends_on": []},
        ],
    }
    result = _run(_write(tmp_path, doc))
    assert result.returncode == 0
    assert "parallel-safety" not in result.stderr


def test_ordered_tasks_sharing_a_file_are_not_flagged(tmp_path):
    """A depends_on B => never concurrent => sharing a file is fine."""
    doc = {
        "version": 1,
        "tasks": [
            {"id": "T-1", "title": "A", "files": ["shared.py"], "verify": ["echo ok"], "depends_on": []},
            {"id": "T-2", "title": "B", "files": ["shared.py"], "verify": ["echo ok"], "depends_on": ["T-1"]},
        ],
    }
    result = _run(_write(tmp_path, doc))
    assert result.returncode == 0
    assert "parallel-safety" not in result.stderr


def test_unordered_tasks_sharing_a_file_are_flagged_but_still_exit_0(tmp_path):
    doc = {
        "version": 1,
        "tasks": [
            {"id": "T-1", "title": "A", "files": ["shared.py", "a.py"], "verify": ["echo ok"], "depends_on": []},
            {"id": "T-2", "title": "B", "files": ["shared.py", "b.py"], "verify": ["echo ok"], "depends_on": []},
        ],
    }
    result = _run(_write(tmp_path, doc))
    assert result.returncode == 0, result.stderr
    assert "Warning (parallel-safety)" in result.stderr
    assert "'T-1' and 'T-2'" in result.stderr
    assert "shared.py" in result.stderr
    # non-shared files must not appear in the reported overlap
    assert "['shared.py']" in result.stderr


def test_transitively_ordered_tasks_are_not_flagged(tmp_path):
    """T-1 -> T-2 -> T-3: T-1 and T-3 are ordered (via T-2), not concurrent."""
    doc = {
        "version": 1,
        "tasks": [
            {"id": "T-1", "title": "A", "files": ["shared.py"], "verify": ["echo ok"], "depends_on": []},
            {"id": "T-2", "title": "B", "files": ["mid.py"], "verify": ["echo ok"], "depends_on": ["T-1"]},
            {"id": "T-3", "title": "C", "files": ["shared.py"], "verify": ["echo ok"], "depends_on": ["T-2"]},
        ],
    }
    result = _run(_write(tmp_path, doc))
    assert result.returncode == 0
    assert "parallel-safety" not in result.stderr


def test_sibling_branches_off_a_common_ancestor_are_flagged(tmp_path):
    """T-2 and T-3 both depend on T-1 but not on each other: they can run together."""
    doc = {
        "version": 1,
        "tasks": [
            {"id": "T-1", "title": "setup", "files": ["setup.py"], "verify": ["echo ok"], "depends_on": []},
            {"id": "T-2", "title": "A", "files": ["shared.py"], "verify": ["echo ok"], "depends_on": ["T-1"]},
            {"id": "T-3", "title": "B", "files": ["shared.py"], "verify": ["echo ok"], "depends_on": ["T-1"]},
        ],
    }
    result = _run(_write(tmp_path, doc))
    assert result.returncode == 0
    assert "Warning (parallel-safety)" in result.stderr
    assert "'T-2' and 'T-3'" in result.stderr


def test_existing_hard_errors_still_win_over_warnings(tmp_path):
    """A structural error (duplicate id) must still exit non-zero, unaffected."""
    doc = {
        "version": 1,
        "tasks": [
            {"id": "T-1", "title": "A", "files": ["a.py"], "verify": ["echo ok"]},
            {"id": "T-1", "title": "B", "files": ["a.py"], "verify": ["echo ok"]},
        ],
    }
    result = _run(_write(tmp_path, doc))
    assert result.returncode != 0
