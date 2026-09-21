"""Shared pytest fixtures for orchestrator_next tests."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pytest
import yaml

from orchestrator_next.state_dir import ENV_STATE_DIR


def pytest_configure(config) -> None:
    """Point the tests at a real workflow pack.

    The pack root is an explicit input now (``--config`` / ORCHESTRATOR_CONFIG),
    so there is nothing to discover: use the checkout's own vendored pack
    unless the caller already named one.
    """
    stale = os.environ.get("ORCHESTRATOR_CONFIG")
    if stale and not (Path(stale) / "workflows").is_dir():
        del os.environ["ORCHESTRATOR_CONFIG"]  # stale export
    if "ORCHESTRATOR_CONFIG" not in os.environ:
        vendored = Path(__file__).resolve().parents[2] / ".orchestrator" / "workflows"
        if (vendored / "workflows").is_dir():
            os.environ["ORCHESTRATOR_CONFIG"] = str(vendored)


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    """Every test gets its own state directory, as `--state` would name it.

    The engine guesses no location, so a test that seeds a run needs one set;
    a test that passes an explicit state path simply ignores this.
    """
    root = tmp_path / ".orchestrator-state"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(ENV_STATE_DIR, str(root))
    return root


def install_step_models(
    monkeypatch,
    tmp_path,
    step_ids: Iterable[str],
    *,
    alias: str = "standard",
    models: dict | None = None,
    tools: dict | None = None,
) -> Path:
    """Point ORCHESTRATOR_MODELS_CONFIG at a models.yaml with step_models.

    Dispatch requires every prompt step in step_models; isolation via the
    models-config layer keeps the pack's ORCHESTRATOR_CONFIG intact.
    """
    path = Path(tmp_path) / "test-models.yaml"
    data: dict = {
        "models": models
        or {
            "standard": {"tool": "claude", "model_id": "claude-sonnet-5"},
            "strong": {"tool": "claude", "model_id": "claude-opus-5"},
            "code": {"tool": "cursor", "model_id": "composer-2.5"},
            "auto": {"tool": "claude", "model_id": "claude-sonnet-5"},
            "opus": {"tool": "claude", "model_id": "claude-opus-4-7"},
        },
        "step_models": {sid: alias for sid in step_ids},
    }
    if tools is not None:
        data["tools"] = tools
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(path))
    return path


def run_exec_step(result: dict, state_path) -> tuple[int, str]:
    """Run an exec step's script the way a driver does, return (exit, stdout).

    The engine hands back `run_path` plus the `env` to apply; it spawns
    nothing itself. Tests that walk a workflow need to play that part.
    """
    import subprocess

    import os

    payload = result["payload"]
    # The payload's env is the engine's contribution; the driver merges it
    # over its own environment (PATH and friends live there).
    env = {**os.environ, **payload["env"]}
    proc = subprocess.run(
        ["bash", payload["run_path"]],
        capture_output=True, text=True,
        env=env, cwd=payload["cwd"],
    )
    return proc.returncode, proc.stdout


def drive(state_path, limit: int = 12, first: dict | None = None) -> dict:
    """`step` until the run is no longer dispatchable, running exec steps.

    This is the driver loop in miniature: ask for the next step, and when it
    is an exec step run the script and report the outcome through `done`.

    ``first`` is a payload another verb already handed back (``resume``
    returns one). Running that payload rather than asking for a fresh one is
    what carries its ORCHESTRATOR_USER_DIRECTION into the script.
    """
    import tempfile

    from orchestrator_next import protocol

    result: dict = {}
    for _ in range(limit):
        if first is not None:
            result, first = first, None
        else:
            result, _ = protocol.step(str(state_path))
        if result.get("status") != "ready":
            return result
        if result.get("kind") != "exec":
            return result
        code, stdout = run_exec_step(result, state_path)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".out", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(stdout)
            stdout_file = fh.name
        protocol.done(
            str(state_path), result["step_id"],
            exit_code=code, stdout_file=stdout_file,
        )
    raise AssertionError("step did not settle")
