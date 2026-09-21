"""The engine returns exec steps; the driver runs them.

`step` hands back the script path and the environment as data and spawns
nothing. `done --exit-code N --stdout-file PATH` is how the driver reports
what happened, and the engine parses that stdout for the script protocol
(status / outputs / state_patch) and routes it exactly as before.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next import protocol
from orchestrator_next.parser import KIND_EXEC
from orchestrator_next.seed import seed_state_file


def _pack(tmp_path: Path, script_body: str, *, steps=("work", "after")) -> Path:
    """A two-exec-step pack whose first step runs ``script_body``."""
    pack = tmp_path / "pack"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "mini.yaml").write_text(
        yaml.safe_dump({"steps": list(steps)}), encoding="utf-8"
    )
    for step_id in steps:
        d = pack / "steps" / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            yaml.safe_dump({"id": step_id, "kind": "exec", "run": "script.sh"}),
            encoding="utf-8",
        )
        body = script_body if step_id == steps[0] else (
            '#!/usr/bin/env bash\necho \'{"reason": "after ran"}\'\n'
        )
        script = d / "script.sh"
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    return pack


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _seed(tmp_path, pack, repo, monkeypatch, slug="x-run"):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    state = tmp_path / f"{slug}.yaml"
    seed_state_file(state, slug=slug, schema="mini", repo_root=str(repo))
    return state


OK_SCRIPT = '#!/usr/bin/env bash\necho \'{"reason": "work ran"}\'\n'


def test_step_returns_the_exec_step_instead_of_running_it(
    tmp_path, repo, monkeypatch
):
    """The payload names the script; nothing is spawned and nothing recorded."""
    marker = repo / "ran.txt"
    pack = _pack(
        tmp_path,
        f'#!/usr/bin/env bash\ntouch {marker}\necho \'{{"reason": "ok"}}\'\n',
    )
    state = _seed(tmp_path, pack, repo, monkeypatch)

    result, code = protocol.step(str(state))
    assert code == 0
    assert result["status"] == "ready"
    assert result["kind"] == KIND_EXEC
    assert result["step_id"] == "work"

    payload = result["payload"]
    assert payload["run_path"] == str(pack / "steps" / "work" / "script.sh")
    assert payload["step_dir"] == str(pack / "steps" / "work")
    assert payload["env"]["ORCHESTRATOR_STEP_ID"] == "work"
    assert payload["env"]["ORCHESTRATOR_STEP_DIR"] == payload["step_dir"]
    assert payload["cwd"]

    assert not marker.exists(), "the engine ran the script"
    assert yaml.safe_load(state.read_text())["step_history"] == []


def test_done_with_exit_zero_and_a_state_patch_advances_and_patches(
    tmp_path, repo, monkeypatch
):
    """The engine parses the driver-reported stdout and applies state_patch."""
    pack = _pack(tmp_path, OK_SCRIPT)
    state = _seed(tmp_path, pack, repo, monkeypatch)
    protocol.step(str(state))

    stdout = tmp_path / "out.json"
    stdout.write_text(
        '{"status": "completed", "outputs": {"reason": "did the thing"}, '
        '"state_patch": {"branch": "feat/x"}}\n',
        encoding="utf-8",
    )
    result, code = protocol.done(
        str(state), "work", exit_code=0, stdout_file=str(stdout)
    )
    assert code == 0
    assert result["status"] == "ok"

    raw = yaml.safe_load(state.read_text())
    assert raw["branch"] == "feat/x", "state_patch was not applied"
    entry = raw["step_history"][-1]
    assert (entry["step_id"], entry["status"]) == ("work", "completed")
    assert entry["outputs"]["reason"] == "did the thing"
    # The run advanced: the next exec step is handed back, not run.
    assert result["next"]["step_id"] == "after"
    assert result["next"]["kind"] == KIND_EXEC


def test_done_with_a_nonzero_exit_records_failed(tmp_path, repo, monkeypatch):
    """A failing script is recorded failed and the run does not advance."""
    pack = _pack(tmp_path, OK_SCRIPT)
    state = _seed(tmp_path, pack, repo, monkeypatch)
    protocol.step(str(state))

    result, code = protocol.done(str(state), "work", exit_code=3)
    assert code == 0

    raw = yaml.safe_load(state.read_text())
    entry = raw["step_history"][-1]
    assert (entry["step_id"], entry["status"]) == ("work", "failed")
    assert "3" in entry["outputs"]["reason"]
    # `after` depends on nothing, so it is still offered; `work` is not done.
    nodes = {n["id"]: n for n in raw["workflow_plan"]["main"]["nodes"]}
    assert nodes["work"]["status"] != "completed"


def test_done_reporting_await_input_parks_the_run(tmp_path, repo, monkeypatch):
    """A script that asks a question parks the run at needs_you."""
    pack = _pack(tmp_path, OK_SCRIPT)
    state = _seed(tmp_path, pack, repo, monkeypatch)
    protocol.step(str(state))

    stdout = tmp_path / "out.json"
    stdout.write_text(
        '{"status": "await_input", "outputs": {"ask": "Ship it?", '
        '"options": [{"label": "yes"}, {"label": "no"}]}}\n',
        encoding="utf-8",
    )
    result, _ = protocol.done(
        str(state), "work", exit_code=0, stdout_file=str(stdout)
    )
    assert result["next"]["status"] == "needs_you"
    assert result["next"]["payload"]["ask"] == "Ship it?"
    assert yaml.safe_load(state.read_text())["awaiting"]["step_id"] == "work"


def test_terminal_done_carries_the_run_report_and_others_do_not(
    tmp_path, repo, monkeypatch
):
    """`report` appears exactly when the run stops, and equals `status`."""
    pack = _pack(tmp_path, OK_SCRIPT)
    state = _seed(tmp_path, pack, repo, monkeypatch)

    first, _ = protocol.step(str(state))
    mid, _ = protocol.done(str(state), "work", exit_code=0)
    assert mid["next"]["status"] == "ready", "expected another step to follow"
    assert "report" not in mid, "a mid-run done must not carry the full report"

    protocol.step(str(state))
    final, _ = protocol.done(str(state), "after", exit_code=0)
    assert final["next"]["status"] == "done"
    assert "report" in final, "a terminal done must carry the full report"

    status_result, _ = protocol.status(str(state))
    assert final["report"] == status_result
    assert status_result["run_status"] == "completed"


def test_the_engine_never_spawns_a_process(tmp_path, repo, monkeypatch):
    """A guard: no verb may shell out. The driver owns every subprocess."""
    pack = _pack(tmp_path, OK_SCRIPT)
    state = _seed(tmp_path, pack, repo, monkeypatch)

    def boom(*args, **kwargs):
        raise AssertionError(f"the engine spawned a process: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)

    result, _ = protocol.step(str(state))
    protocol.done(str(state), "work", exit_code=0)
    protocol.status(str(state))
    assert result["kind"] == KIND_EXEC


def test_the_exec_payload_never_leaks_the_engines_environment(
    tmp_path, repo, monkeypatch
):
    """`step` prints its payload, so the env block must carry no ambient secrets."""
    pack = _pack(tmp_path, OK_SCRIPT)
    state = _seed(tmp_path, pack, repo, monkeypatch)
    monkeypatch.setenv("MY_API_TOKEN", "super-secret-value")

    result, _ = protocol.step(str(state))
    env = result["payload"]["env"]
    assert "MY_API_TOKEN" not in env
    assert "super-secret-value" not in str(result)
    # What it DOES carry is what the engine knows about the run.
    assert env["ORCHESTRATOR_STEP_ID"] == "work"
    assert env["ORCHESTRATOR_CHANGE_ID"] == "x-run"
