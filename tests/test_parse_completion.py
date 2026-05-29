"""Tests for scripts/parse-completion.py.

These tests must FAIL until T-5 (the implementation) lands.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "parse-completion.py"


def run_parse(input_text: str, *, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run parse-completion.py with the given stdin input."""
    args = [sys.executable, str(SCRIPT)] + (extra_args or [])
    return subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
    )


class TestValidCompletionBlock:
    def test_valid_block_produces_json(self):
        """Valid COMPLETION block with required fields produces JSON to stdout."""
        input_text = """\
Some agent output here.

COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-1
      status: completed
  artifacts: [config/tools.yaml]
"""
        result = run_parse(input_text)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}. stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["status"] == "completed"
        assert "outputs" in data
        assert data["outputs"]["task_execution_result"]["task_id"] == "T-1"

    def test_artifacts_in_output(self):
        """Artifacts list is preserved in the JSON output."""
        input_text = """\
COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-2
      status: completed
  artifacts: [scripts/run-workflow.sh, config/tools.yaml]
"""
        result = run_parse(input_text)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "artifacts" in data
        assert "scripts/run-workflow.sh" in data["artifacts"]

    def test_recovered_status_accepted(self):
        """status: recovered is a valid enum value."""
        input_text = """\
COMPLETION:
  status: recovered
  outputs:
    task_execution_result:
      task_id: T-3
      status: completed
"""
        result = run_parse(input_text)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "recovered"

    def test_abandoned_status_accepted(self):
        """status: abandoned is a valid enum value."""
        input_text = """\
COMPLETION:
  status: abandoned
  outputs:
    task_execution_result:
      task_id: T-4
      status: completed
"""
        result = run_parse(input_text)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "abandoned"


class TestInvalidCompletionBlock:
    def test_missing_completion_block_exits_nonzero(self):
        """When no COMPLETION block is present, exit non-zero with diagnostic."""
        input_text = "This is some output with no COMPLETION block.\n"
        result = run_parse(input_text)
        assert result.returncode != 0, f"Expected non-zero exit, got {result.returncode}"
        # Should emit a diagnostic to stderr
        assert len(result.stderr) > 0 or "COMPLETION" in result.stdout.upper()

    def test_invalid_status_rejected(self):
        """Status not in {completed, recovered, abandoned} is rejected."""
        input_text = """\
COMPLETION:
  status: failed
  outputs:
    task_execution_result: {}
"""
        result = run_parse(input_text)
        assert result.returncode != 0, f"Expected non-zero exit for invalid status 'failed', got {result.returncode}"

    def test_invalid_status_passed_exits_nonzero(self):
        """Status 'blocked' is not in the valid enum."""
        input_text = """\
COMPLETION:
  status: blocked
  outputs: {}
"""
        result = run_parse(input_text)
        assert result.returncode != 0

    def test_malformed_yaml_exits_nonzero(self):
        """Malformed YAML in the COMPLETION block exits non-zero."""
        input_text = """\
COMPLETION:
  status: completed
  outputs: {unclosed: [brace
"""
        result = run_parse(input_text)
        assert result.returncode != 0


class TestEmbeddedCompletion:
    def test_fenced_yaml_after_completion_header(self):
        """COMPLETION: followed by ```yaml fence is parsed (agents often fence stdout)."""
        input_text = """\
Some agent output.

COMPLETION:
```yaml
status: abandoned
reason: "No bug report"
outputs: {}
artifacts: []
```
"""
        result = run_parse(input_text)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["status"] == "abandoned"
        assert data["outputs"] == {}

    def test_completion_block_mid_stdout_extracted(self):
        """COMPLETION block embedded mid-stdout (with trailing text) is still extracted."""
        input_text = """\
Starting task implementation...
Running tests...

COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-5
      status: completed

Some trailing text after completion.
More output here.
"""
        result = run_parse(input_text)
        assert result.returncode == 0, f"Expected exit 0. stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["status"] == "completed"
        assert data["outputs"]["task_execution_result"]["task_id"] == "T-5"

    def test_completion_with_indented_block(self):
        """COMPLETION block with multi-level indented YAML is parsed correctly."""
        input_text = """\
Agent output.

COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-6
      status: completed
  evidence:
    commands:
      - cmd: "pytest -q"
        exit_code: 0
    counts:
      tasks_marked: 1
"""
        result = run_parse(input_text)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["evidence"]["counts"]["tasks_marked"] == 1


class TestFileInput:
    def test_file_argument_works(self, tmp_path):
        """parse-completion.py accepts a file path as argument."""
        content = """\
COMPLETION:
  status: completed
  outputs:
    task_execution_result:
      task_id: T-7
      status: completed
"""
        input_file = tmp_path / "agent_output.txt"
        input_file.write_text(content)

        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(input_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Expected exit 0. stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["status"] == "completed"
