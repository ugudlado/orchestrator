"""Regression test: --help must surface public workflows and doctor."""
import subprocess
import sys


def test_help_lists_workflows_and_doctor():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator_next", "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for token in ("feature", "bugfix", "doctor", "--models-config"):
        assert token in output, f"--help missing {token!r}"
