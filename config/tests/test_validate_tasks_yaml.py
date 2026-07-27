"""T-1 tests: validate-tasks-yaml.sh validator and Tasks YAML Format Contract section.

These tests run the validator script and check the Tasks YAML Format Contract in
design/prompt.md (moved from artifact-formats.md in T-23).
RED phase: tests fail before T-1 implementation.
"""
from __future__ import annotations

import os
import subprocess

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Validator + format contract live with the design skill.
_VALIDATOR = os.path.join(_REPO_ROOT, "skills", "design", "validate-tasks-yaml.sh")
_ARTIFACT_FORMATS = os.path.join(_REPO_ROOT, "skills", "design", "reference", "tasks-format.md")


def _write_tasks_yaml(tmp_path, content: dict) -> str:
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump(content, sort_keys=False, default_flow_style=False))
    return str(p)


def _write_tasks_yaml_raw(tmp_path, text: str) -> str:
    p = tmp_path / "tasks.yaml"
    p.write_text(text)
    return str(p)


def _run_validator(tasks_yaml_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", _VALIDATOR, tasks_yaml_path],
        capture_output=True,
        text=True,
    )


VALID_TASKS_YAML = {
    "version": 1,
    "tasks": [
        {
            "id": "T-1",
            "title": "Wire X to Y",
            "files": ["path/to/file.py"],
            "verify": ["pytest tests/test_x.py::test_wire"],
            "depends_on": [],
        },
        {
            "id": "T-2",
            "title": "Add regression test",
            "files": ["tests/test_x.py"],
            "verify": ["pytest tests/test_x.py"],
            "depends_on": ["T-1"],
        },
    ],
}


# ---------------------------------------------------------------------------
# Tasks YAML Format Contract section (now in pack/reference/tasks-format.md)
# ---------------------------------------------------------------------------

class TestArtifactFormatsTasksYamlSection:

    def test_artifact_formats_has_tasks_yaml_section(self):
        """tasks-format.md must contain the 'Tasks YAML Format Contract' section."""
        with open(_ARTIFACT_FORMATS, "r") as f:
            content = f.read()
        assert "# Tasks YAML Format Contract" in content, (
            "tasks-format.md missing '# Tasks YAML Format Contract' section"
        )

    def test_tasks_yaml_section_documents_required_fields(self):
        """Tasks YAML Format Contract must document id, title, files, verify fields."""
        with open(_ARTIFACT_FORMATS, "r") as f:
            content = f.read()
        # Find the section
        idx = content.find("# Tasks YAML Format Contract")
        assert idx >= 0
        section = content[idx:]
        for field in ("id", "title", "files", "verify", "depends_on"):
            assert field in section, (
                f"Tasks YAML Format Contract missing field '{field}'"
            )

    def test_tasks_yaml_section_documents_version(self):
        """Tasks YAML Format Contract must document 'version: 1'."""
        with open(_ARTIFACT_FORMATS, "r") as f:
            content = f.read()
        idx = content.find("# Tasks YAML Format Contract")
        assert idx >= 0
        section = content[idx:]
        assert "version" in section, (
            "Tasks YAML Format Contract missing 'version' field"
        )


# ---------------------------------------------------------------------------
# Validator script tests
# ---------------------------------------------------------------------------

class TestValidatorScript:

    def test_validator_exists(self):
        assert os.path.isfile(_VALIDATOR), f"validate-tasks-yaml.sh not found at {_VALIDATOR}"

    def test_exits_0_on_valid_file(self, tmp_path):
        path = _write_tasks_yaml(tmp_path, VALID_TASKS_YAML)
        result = _run_validator(path)
        assert result.returncode == 0, (
            f"Validator should exit 0 on valid file. stderr: {result.stderr}"
        )

    def test_exits_nonzero_on_duplicate_ids(self, tmp_path):
        bad = {
            "version": 1,
            "tasks": [
                {"id": "T-1", "title": "First", "files": ["a.py"], "verify": ["echo ok"]},
                {"id": "T-1", "title": "Duplicate", "files": ["b.py"], "verify": ["echo ok"]},
            ],
        }
        path = _write_tasks_yaml(tmp_path, bad)
        result = _run_validator(path)
        assert result.returncode != 0, (
            f"Validator should exit non-zero on duplicate ids. stdout: {result.stdout}"
        )

    def test_exits_nonzero_on_unknown_depends_on(self, tmp_path):
        bad = {
            "version": 1,
            "tasks": [
                {
                    "id": "T-1",
                    "title": "Wire X",
                    "files": ["a.py"],
                    "verify": ["echo ok"],
                    "depends_on": ["T-99"],
                },
            ],
        }
        path = _write_tasks_yaml(tmp_path, bad)
        result = _run_validator(path)
        assert result.returncode != 0, (
            f"Validator should exit non-zero on unknown depends_on. stdout: {result.stdout}"
        )

    def test_exits_nonzero_on_missing_required_field_id(self, tmp_path):
        bad = {
            "version": 1,
            "tasks": [
                {"title": "No id field", "files": ["a.py"], "verify": ["echo ok"]},
            ],
        }
        path = _write_tasks_yaml(tmp_path, bad)
        result = _run_validator(path)
        assert result.returncode != 0, (
            f"Validator should exit non-zero when 'id' is missing. stdout: {result.stdout}"
        )

    def test_exits_nonzero_on_missing_required_field_title(self, tmp_path):
        bad = {
            "version": 1,
            "tasks": [
                {"id": "T-1", "files": ["a.py"], "verify": ["echo ok"]},
            ],
        }
        path = _write_tasks_yaml(tmp_path, bad)
        result = _run_validator(path)
        assert result.returncode != 0, (
            f"Validator should exit non-zero when 'title' is missing. stdout: {result.stdout}"
        )

    def test_exits_nonzero_on_missing_required_field_files(self, tmp_path):
        bad = {
            "version": 1,
            "tasks": [
                {"id": "T-1", "title": "Wire X", "verify": ["echo ok"]},
            ],
        }
        path = _write_tasks_yaml(tmp_path, bad)
        result = _run_validator(path)
        assert result.returncode != 0, (
            f"Validator should exit non-zero when 'files' is missing. stdout: {result.stdout}"
        )

    def test_exits_nonzero_on_missing_required_field_verify(self, tmp_path):
        bad = {
            "version": 1,
            "tasks": [
                {"id": "T-1", "title": "Wire X", "files": ["a.py"]},
            ],
        }
        path = _write_tasks_yaml(tmp_path, bad)
        result = _run_validator(path)
        assert result.returncode != 0, (
            f"Validator should exit non-zero when 'verify' is missing. stdout: {result.stdout}"
        )

    def test_exits_nonzero_on_no_arg(self):
        result = subprocess.run(
            ["bash", _VALIDATOR],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "Validator should exit non-zero when called without arguments."
        )

    def test_exits_nonzero_on_missing_file(self, tmp_path):
        result = _run_validator(str(tmp_path / "nonexistent.yaml"))
        assert result.returncode != 0, (
            "Validator should exit non-zero when file does not exist."
        )

    def test_valid_file_with_optional_fields(self, tmp_path):
        """Optional fields (why, change, test_scenarios) should not cause failure."""
        full = {
            "version": 1,
            "tasks": [
                {
                    "id": "T-1",
                    "title": "Wire X to Y",
                    "why": "AC-3",
                    "change": "edit file.py:42",
                    "files": ["path/to/file.py"],
                    "verify": ["pytest tests/test_x.py"],
                    "test_scenarios": ["Y observes X emission"],
                    "depends_on": [],
                },
            ],
        }
        path = _write_tasks_yaml(tmp_path, full)
        result = _run_validator(path)
        assert result.returncode == 0, (
            f"Validator should exit 0 on valid file with optional fields. stderr: {result.stderr}"
        )
