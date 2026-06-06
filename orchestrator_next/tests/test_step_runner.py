"""Tests for orchestrator_next.step_runner."""

from __future__ import annotations

import os

from orchestrator_next.parser import ScriptStepContract
from orchestrator_next.step_runner import (
    apply_step_paths,
    build_step_command,
    step_directory,
)


def test_step_directory_uses_step_id_when_no_abs_run():
    contract = ScriptStepContract(id="capture-test-baseline", run="/some/path/script.sh")
    config_root = "/opt/orchestrator/config"
    # run is abs but parent != _runner, so returns run's parent
    step_dir = step_directory("capture-test-baseline", contract, config_root)
    assert str(step_dir) == "/some/path"


def test_step_directory_uses_config_root_when_run_in_runner(tmp_path):
    runner_dir = tmp_path / "_runner"
    runner_dir.mkdir()
    script = runner_dir / "script.sh"
    script.write_text("", encoding="utf-8")
    contract = ScriptStepContract(id="capture-test-baseline", run=str(script))
    config_root = str(tmp_path / "config")
    step_dir = step_directory("capture-test-baseline", contract, config_root)
    assert str(step_dir) == os.path.join(config_root, "steps", "capture-test-baseline")


def test_step_directory_uses_run_dir(tmp_path):
    script = tmp_path / "steps" / "demo" / "script.sh"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    contract = ScriptStepContract(id="demo", run=str(script))
    assert step_directory("demo", contract, str(tmp_path)) == script.parent


def test_build_step_command_returns_bash_run(tmp_path):
    home = tmp_path / "orch"
    steps = home / "config" / "steps" / "demo"
    steps.mkdir(parents=True)
    script = steps / "script.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    contract = ScriptStepContract(id="demo", run=str(script.resolve()))
    assert build_step_command("demo", contract, str(home)) == ["bash", str(script.resolve())]


def test_apply_step_paths_sets_step_dir(tmp_path):
    home = tmp_path / "orch"
    steps = home / "config" / "steps" / "demo"
    steps.mkdir(parents=True)
    script = steps / "script.sh"
    script.write_text("", encoding="utf-8")
    contract = ScriptStepContract(id="demo", run=str(script))
    env = apply_step_paths(
        {"ORCHESTRATOR_HOME": str(home)},
        step_id="demo",
        contract=contract,
        config_root=str(home / "config"),
    )
    assert env["ORCHESTRATOR_STEP_DIR"] == str(steps)
