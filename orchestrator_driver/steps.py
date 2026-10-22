"""Engine-calling and step-running helpers: call `orchestrator next`, run an exec script or hand a
judgment step to the configured agent worker, each in its own process group."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import sys
from pathlib import Path

import yaml

from orchestrator_driver.config import Config

# How to call the engine; argv tokens, never one quoted word. $ORCH replaces it (a wrapper, a pinned install).
ORCH = shlex.split(os.environ.get("ORCH", f"{sys.executable} -m orchestrator_next"))
PKG_ROOT = str(Path(__file__).resolve().parents[1])


def pythonpath() -> str:
    """Steps run in the project's worktree, so the driver's packages must be importable from there."""
    return os.pathsep.join(p for p in (PKG_ROOT, os.environ.get("PYTHONPATH")) if p)


def worker_cmd(cfg: Config, step_id: str) -> list[str]:
    """A judgment worker is called as `<cmd> <payload.json> <result.json>` and must write the result
    JSON ({status, outputs..., summary}). Which one: the step's entry in `agents`, else `agent`."""
    return [sys.executable, "-m", f"orchestrator_driver.workers.{cfg.agents.get(step_id, cfg.agent)}"]


def worker_env(cfg: Config) -> dict:
    """What the workers read from the environment, taken from the resolved config."""
    return {
        "ORCH_MODEL_OVERRIDE": cfg.model_override,
        "ORCH_SANDBOX_DOMAINS": ",".join(cfg.sandbox_domains),
        "ORCH_STEP_ENV_KEEP": ",".join(cfg.step_env_keep),
        "PYTHONPATH": pythonpath(),
    }


def kill_lock(lock: Path) -> None:
    """Kill the process group a dead worker left in `lock`, if any."""
    if lock.exists():
        try:
            os.killpg(int(lock.read_text()), signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError):
            pass


async def _sh(
    argv: list[str], env: dict | None = None, cwd: str | None = None, lock: Path | None = None
) -> tuple[int, str, str]:
    """Run argv in its own process group. With `lock`, the group id is kept in
    that file while it runs: a re-run of the same step (after the worker died) first kills the group the dead worker left behind, so two
    agents never share a worktree."""
    if lock:
        kill_lock(lock)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    if lock:
        lock.write_text(str(proc.pid))
    try:
        out, err = await proc.communicate()
    except BaseException:
        os.killpg(proc.pid, signal.SIGKILL)
        raise
    if lock:
        lock.unlink(missing_ok=True)
    return proc.returncode, out.decode(), err.decode()


async def call_next(run: dict, after: dict | None) -> dict:
    argv = [
        *ORCH,
        "next",
        run["workflow"],
        "--config",
        run["pack"],
        "--slug",
        run["slug"],
        "--artifacts-dir",
        run["artifacts_dir"],
    ]
    if after:
        argv += ["--after", after["step"], "--attempt", str(after["attempt"])]
        if "exit_code" in after:
            argv += ["--exit-code", str(after["exit_code"]), "--stdout-file", after["stdout_file"]]
        else:
            argv += ["--status", after["status"]]
            if after.get("out"):
                argv += ["--out", json.dumps(after["out"])]
    code, out, err = await _sh(argv, env={"PYTHONPATH": pythonpath()})
    if code != 0:
        # exit 3: usage/infra error. Raise so the caller retries.
        raise RuntimeError(f"orchestrator next exited {code}: {err.strip() or out.strip()}")
    return json.loads(out)


def _results_dir(run: dict) -> Path:
    d = Path(run["artifacts_dir"]).parent / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path(run: dict) -> Path:
    # JSON is valid YAML; the pack's scripts read it as STATE_YAML_PATH.
    return Path(run["artifacts_dir"]).parent / "state.yaml"


def _spawn_env(run: dict, ans: dict, attempt: int, state: dict, extra_env: dict) -> dict:
    """payload.env + the orchestrate skill's driver block."""
    repo = run.get("repo_root") or ""
    # After remove-worktree the recorded path is gone; later steps run from the repo.
    wt = state.get("worktree_path")
    wt = wt if wt and Path(wt).is_dir() else repo
    return {
        "ORCHESTRATOR_PYTHON": sys.executable,  # an interpreter that can import pyyaml (the pack's scripts need one)
        **ans["payload"]["env"],
        **extra_env,
        "ORCHESTRATOR_ATTEMPT": str(attempt),
        "ORCHESTRATOR_CONFIG": run["pack"],
        "REPO_ROOT": repo,
        "WORKTREE_PATH": wt,
        "STATE_YAML_PATH": str(_state_path(run)),
    }


def _last_json_line(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


async def run_exec(cfg: Config, run: dict, ans: dict, attempt: int, state: dict, extra_env: dict) -> dict:
    p, step_id = ans["payload"], ans["step_id"]
    res = _results_dir(run)
    stdout_file = res / f"{step_id}-{attempt}.stdout"
    env = _spawn_env(run, ans, attempt, state, extra_env)
    # A cloud session sets CLAUDE_CODE_REMOTE, which makes create-worktree a no-op.
    # Clear it for scripts only: Claude Code itself needs it to sign in there.
    env["CLAUDE_CODE_REMOTE"] = ""
    _state_path(run).write_text(json.dumps(state, indent=2))
    if cfg.agent == "stub":  # a dry run: exec scripts are stubbed too
        argv = [sys.executable, "-m", "orchestrator_driver.workers.stub", "--exec", "-"]
        env |= {"ORCH_PAYLOAD": json.dumps(p), **worker_env(cfg)}
    else:
        # Some pack scripts are committed without the executable bit.
        argv = [p["run_path"]] if os.access(p["run_path"], os.X_OK) else ["bash", p["run_path"]]
        if step_id == "persist-learnings":  # pack quirk, per its DRIVER.md
            argv += ["--proposed-scenarios", p["in"].get("proposed_scenarios", "")]
    code, out, err = await _sh(argv, env=env, lock=res / f"{step_id}.lock")
    stdout_file.write_text(out)
    return {
        "step": step_id,
        "attempt": attempt,
        "exit_code": code,
        "stdout_file": str(stdout_file),
        "stdout_tail": out[-2000:],
        "stderr_tail": err[-2000:],
        # state_mutating scripts edit the state file themselves; hand it back.
        "state": yaml.safe_load(_state_path(run).read_text()) or {},
    }


async def run_judgment(cfg: Config, run: dict, ans: dict, attempt: int, state: dict, extra_env: dict) -> dict:
    p, step_id = ans["payload"], ans["step_id"]
    res_dir = _results_dir(run)
    payload_file = res_dir / f"{step_id}-{attempt}.payload.json"
    result_file = res_dir / f"{step_id}-{attempt}.json"
    payload_file.write_text(json.dumps(p))
    result_file.unlink(missing_ok=True)  # a stale file from a killed attempt is not a result
    env = _spawn_env(run, ans, attempt, state, extra_env)
    _state_path(run).write_text(json.dumps(state, indent=2))
    code, out, err = await _sh(
        [*worker_cmd(cfg, step_id), str(payload_file), str(result_file)],
        env=env | worker_env(cfg),
        cwd=env["WORKTREE_PATH"] or None,
        lock=res_dir / f"{step_id}.lock",
    )
    if not result_file.exists():
        # A missing result file means the step did not finish: let the caller retry it.
        raise RuntimeError(f"worker exited {code} without writing {result_file}: {(out + err)[-800:]}")
    result = json.loads(result_file.read_text())
    status = result.pop("status", "completed")
    summary = result.pop("summary", None)
    return {
        "step": step_id,
        "attempt": attempt,
        "status": status,
        "out": result,
        "summary": summary,
        "worker": _last_json_line(out),  # model, cost, turns
    }
