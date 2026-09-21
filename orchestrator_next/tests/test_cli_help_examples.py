"""Regression test: --help must surface the core verbs and the config input."""
import subprocess
import sys


def test_help_lists_the_core_verbs_and_the_config_input():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator_next", "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for token in ("start", "step", "done", "status", "<workflow>", "--config"):
        assert token in output, f"--help missing {token!r}"
