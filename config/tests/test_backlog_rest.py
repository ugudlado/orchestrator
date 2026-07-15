"""Backlog REST integration: ticket context, project resolution, and API helpers.

Covers config/steps/lib/backlog-api.sh and the load-ticket-context step — all
config-owned bash, exercised via subprocess. Engine-boundary assertions (that
orchestrator_next has no ticket-fetching logic of its own) live in
orchestrator_next/tests/test_run_loop_ticket_agnostic.py instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_REPO_PATH = Path(_REPO)
_LOAD_SCRIPT = _REPO_PATH / "steps" / "load-ticket-context" / "script.sh"
_API_SH = _REPO_PATH / "steps" / "lib" / "backlog-api.sh"


def test_load_ticket_context_success(tmp_path, monkeypatch):
    state_dir = tmp_path / "st"
    state_dir.mkdir()
    state_yaml = state_dir / "state.yaml"
    state_yaml.write_text(yaml.safe_dump({
        "ticket_id": "orc-125",
        "change_id": "orc-125",
    }))
    (tmp_path / "spec").mkdir(exist_ok=True)
    (tmp_path / "spec" / "project.yaml").write_text(yaml.safe_dump({"ticketing": "backlog"}))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    payload = {
        "id": "ORC-125",
        "title": "Replace CLI with REST",
        "status": "To Do",
        "priority": "high",
        "labels": ["bug"],
        "description": "Use BACKLOG_URL",
        "acceptanceCriteriaItems": [
            {"index": 1, "checked": False, "text": "REST fetch works"},
        ],
    }
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "echo '" + json.dumps(payload) + "'\n"
    )
    curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
    env["BACKLOG_URL"] = "https://example.test"
    env["BACKLOG_TOKEN"] = "tok"
    env["BACKLOG_PROJECT_ID"] = "orc"
    env["REPO_ROOT"] = str(tmp_path)
    env["CHANGE_ID"] = "orc-125"
    env["ORCHESTRATOR_CHANGE_ID"] = "orc-125"
    env["ORCHESTRATOR_STATE_YAML_PATH"] = str(state_yaml)
    env["ORCHESTRATOR_WORKTREE_ARTIFACT_DIR"] = str(tmp_path / "spec" / "changes")

    proc = subprocess.run(
        ["bash", str(_LOAD_SCRIPT)],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["outputs"]["ticket_context"] == "ok"
    assert out["outputs"]["path"] == "spec/changes/orc-125/ticket-context.md"
    body = (tmp_path / "spec" / "changes" / "orc-125" / "ticket-context.md").read_text()
    assert "Replace CLI with REST" in body
    assert "REST fetch works" in body
    assert "Use BACKLOG_URL" in body


def test_load_ticket_context_missing_env_aborts_workflow(tmp_path):
    """Missing BACKLOG_URL/TOKEN must exit 1 so the run loop aborts."""
    state_dir = tmp_path / "st"
    state_dir.mkdir()
    state_yaml = state_dir / "state.yaml"
    state_yaml.write_text(yaml.safe_dump({
        "ticket_id": "ORC-125",
        "change_id": "orc-125",
    }))
    (tmp_path / "spec").mkdir(exist_ok=True)
    (tmp_path / "spec" / "project.yaml").write_text(yaml.safe_dump({"ticketing": "backlog"}))
    env = os.environ.copy()
    env.pop("BACKLOG_URL", None)
    env.pop("BACKLOG_TOKEN", None)
    env["REPO_ROOT"] = str(tmp_path)
    env["CHANGE_ID"] = "orc-125"
    env["ORCHESTRATOR_STATE_YAML_PATH"] = str(state_yaml)

    proc = subprocess.run(
        ["bash", str(_LOAD_SCRIPT)],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc.returncode == 1, proc.stderr
    body = (tmp_path / "spec" / "changes" / "orc-125" / "ticket-context.md").read_text()
    assert "TICKET FETCH FAILED" in body
    assert "do not invent scope" in body
    assert "ERROR" in proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["status"] == "failed"
    assert out["outputs"]["ticket_context"] == "failed"


def _resolve_project(env_overrides: dict, repo_root: str | None) -> str:
    """Run backlog_api_project() under a controlled env; return its stdout."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("BACKLOG_PROJECT", "BACKLOG_PROJECT_ID")}
    if repo_root is not None:
        env["REPO_ROOT"] = repo_root
    else:
        env.pop("REPO_ROOT", None)
    env.update(env_overrides)
    proc = subprocess.run(
        ["bash", "-c", f"source '{_API_SH}' && backlog_api_project"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _write_project_yaml(tmp_path: Path, project_id) -> str:
    spec = tmp_path / "spec"
    spec.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "ticketing": "backlog"}
    if project_id is not None:
        doc["project_id"] = project_id
    (spec / "project.yaml").write_text(yaml.safe_dump(doc))
    return str(tmp_path)


def test_backlog_api_project_env_id_wins(tmp_path):
    """BACKLOG_PROJECT_ID env takes precedence over spec/project.yaml."""
    repo = _write_project_yaml(tmp_path, "from-config")
    assert _resolve_project({"BACKLOG_PROJECT_ID": "from-env-id"}, repo) == "from-env-id"


def test_backlog_api_project_env_name_beats_id(tmp_path):
    """BACKLOG_PROJECT (name alias) beats BACKLOG_PROJECT_ID."""
    repo = _write_project_yaml(tmp_path, "from-config")
    got = _resolve_project(
        {"BACKLOG_PROJECT": "from-env-name", "BACKLOG_PROJECT_ID": "from-env-id"}, repo)
    assert got == "from-env-name"


def test_backlog_api_project_empty_when_no_env_even_with_config(tmp_path):
    """project_id resolution is env-only — spec/project.yaml is never read,
    even if it has a project_id key and REPO_ROOT points at it."""
    repo = _write_project_yaml(tmp_path, "orchestrator")
    assert _resolve_project({}, repo) == ""


def test_backlog_api_project_empty_when_no_repo_root():
    """No env project and no REPO_ROOT → empty (no crash)."""
    assert _resolve_project({}, None) == ""


def test_backlog_api_format_plain_roundtrip():
    """format helper produces readable AC lines from JSON."""
    payload = {
        "id": "ORC-1",
        "title": "T",
        "status": "To Do",
        "priority": "low",
        "labels": ["a"],
        "description": "desc",
        "acceptanceCriteriaItems": [{"index": 1, "checked": True, "text": "done item"}],
    }
    env = os.environ.copy()
    env["PAYLOAD"] = json.dumps(payload)
    proc = subprocess.run(
        ["bash", "-c", f"source '{_API_SH}' && printf '%s' \"$PAYLOAD\" | backlog_api_format_plain"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Task ORC-1 - T" in proc.stdout
    assert "[x] #1 done item" in proc.stdout
