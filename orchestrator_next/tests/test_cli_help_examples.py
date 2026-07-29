"""Regression test: --help example line must list patch, design, implement."""
import subprocess
import sys


def test_help_lists_patch_design_implement():
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator_next", "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for schema in ("patch", "design", "implement"):
        assert schema in output, f"--help missing schema: {schema!r}"
