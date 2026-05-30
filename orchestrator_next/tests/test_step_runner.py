"""Tests for orchestrator_next.step_runner."""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator_next.parser import StepContract
from orchestrator_next.step_runner import (
    apply_step_paths,
    build_step_command,
    step_directory,
)


def test_step_directory_uses_step_id_when_no_run():
    contract = StepContract(
        id="capture-test-baseline",
        agent=None,
        run=None,
        instruction="",
        rules=[],
        kind="script",
        main="capture_test_baseline.py",
    )
    home = "/opt/orchestrator"
    step_dir = step_directory("capture-test-baseline", contract, home)
    assert str(step_dir) == os.path.join(home, "config", "steps", "capture-test-baseline")


def test_step_directory_uses_run_dir(tmp_path):
    script = tmp_path / "steps" / "demo" / "script.sh"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    contract = StepContract(
        id="demo",
        agent=None,
        run=str(script),
        instruction="",
        rules=[],
        kind="script",
        main="demo.py",
    )
    assert step_directory("demo", contract, str(tmp_path)) == script.parent


def test_build_step_command_prefers_bash_run(tmp_path):
    home = tmp_path / "orch"
    steps = home / "config" / "steps" / "demo"
    steps.mkdir(parents=True)
    script = steps / "script.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    (steps / "demo.py").write_text("", encoding="utf-8")
    contract = StepContract(
        id="demo",
        agent=None,
        run=str(script.resolve()),
        instruction="",
        rules=[],
        kind="script",
        main="demo.py",
    )
    assert build_step_command("demo", contract, str(home)) == ["bash", str(script.resolve())]


def test_build_step_command_python_when_no_run(tmp_path):
    home = tmp_path / "orch"
    steps = home / "config" / "steps" / "demo"
    steps.mkdir(parents=True)
    main_py = steps / "demo.py"
    main_py.write_text("raise SystemExit(0)\n", encoding="utf-8")
    contract = StepContract(
        id="demo",
        agent=None,
        run=None,
        instruction="",
        rules=[],
        kind="script",
        main="demo.py",
    )
    cmd = build_step_command("demo", contract, str(home))
    assert cmd[1] == str(main_py.resolve())


def test_apply_step_paths_sets_step_dir(tmp_path):
    home = tmp_path / "orch"
    steps = home / "config" / "steps" / "demo"
    steps.mkdir(parents=True)
    contract = StepContract(
        id="demo",
        agent=None,
        run=None,
        instruction="",
        rules=[],
        kind="script",
        main="demo.py",
    )
    env = apply_step_paths(
        {"ORCHESTRATOR_HOME": str(home)},
        step_id="demo",
        contract=contract,
        orchestrator_home=str(home),
    )
    assert env["ORCHESTRATOR_STEP_DIR"] == str(steps)


def test_capture_test_baseline_script_uses_step_dir_env():
    script = Path(__file__).resolve().parents[2] / "config/steps/capture-test-baseline/script.sh"
    body = script.read_text(encoding="utf-8")
    assert "ORCHESTRATOR_STEP_DIR" in body
    assert "BASH_SOURCE" not in body
