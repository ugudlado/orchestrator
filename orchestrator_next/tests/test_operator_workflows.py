"""Tests for telemetry and workflow-learner operator workflows."""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrator_next.operator_workflow import (  # noqa: E402
    load_step_params,
    merge_step_env,
    workflow_step_ids,
)


def test_telemetry_workflow_step_list(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    assert workflow_step_ids("telemetry") == ["render-telemetry"]


def test_step_params_from_contract(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    params = load_step_params("render-telemetry")
    assert params["TELEMETRY_SCOPE"] == "recent"
    assert params["TELEMETRY_FEATURES_LIMIT"] == "5"


def test_merge_step_env_os_environ_overrides_contract(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    monkeypatch.setenv("TELEMETRY_SCOPE", "all")
    merged = merge_step_env("render-telemetry", {"REPO_ROOT": "/tmp/repo"})
    assert merged["TELEMETRY_SCOPE"] == "all"
    assert merged["TELEMETRY_FEATURES_LIMIT"] == "5"


def test_gather_learn_metrics_step_contract(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    params = load_step_params("gather-learn-metrics")
    assert params  # contract defines operator defaults


def test_gather_learn_metrics_emits_json(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRATOR_HOME", _REPO_ROOT)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "spec").mkdir()
    (repo / "spec" / "project.yaml").write_text("version: 1\n", encoding="utf-8")

    env = {
        "REPO_ROOT": str(repo),
        "ORCHESTRATOR_REPO_ROOT": str(repo),
        "ORCHESTRATOR_STATE_YAML_PATH": "/dev/null",
        "STATE_YAML_PATH": "/dev/null",
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; "
            "sys.path.insert(0, os.environ['REPO']); "
            "from orchestrator_next.operator_workflow import run_script_step; "
            "raise SystemExit(run_script_step('gather-learn-metrics', "
            "{k: os.environ[k] for k in "
            "('REPO_ROOT','ORCHESTRATOR_REPO_ROOT',"
            "'ORCHESTRATOR_STATE_YAML_PATH','STATE_YAML_PATH')}))",
        ],
        env={**os.environ, "REPO": _REPO_ROOT, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "learn_metrics" in payload
    assert payload["learn_metrics"]["scope"] == "all"


def test_cli_telemetry_no_args(monkeypatch):
    orch = os.path.join(_REPO_ROOT, "bin", "orchestrator")
    proc = subprocess.run(
        [orch, "telemetry"],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env={**os.environ, "ORCHESTRATOR_HOME": _REPO_ROOT},
    )
    assert proc.returncode != 3
    assert "unknown argument" not in proc.stderr.lower()


def test_cli_telemetry_rejects_flags():
    orch = os.path.join(_REPO_ROOT, "bin", "orchestrator")
    proc = subprocess.run(
        [orch, "telemetry", "--scope", "recent"],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env={**os.environ, "ORCHESTRATOR_HOME": _REPO_ROOT},
    )
    assert proc.returncode == 7
