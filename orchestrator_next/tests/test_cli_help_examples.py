"""Regression test: --help must surface workflow usage and doctor."""
import subprocess
import sys


def test_help_lists_workflows_and_doctor():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator_next", "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for token in ("<workflow>", "doctor", "--models-config"):
        assert token in output, f"--help missing {token!r}"
