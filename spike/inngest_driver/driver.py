"""Spike: an Inngest function that drives `orchestrator next` end to end.

One generic function runs any workflow in any pack. The engine still decides
every step; Inngest only holds what the driver skill keeps in state.yaml today
(attempts, gate tokens, history) and makes it durable: each `next` call and
each step is a memoized `step.run`, and gates are `step.wait_for_event`.

Run (from the orchestrator checkout):

    uv run --with inngest==0.5.19 --with pyyaml python -m spike.inngest_driver.driver

Events:
    orchestrator/run.requested  {workflow, pack, slug, artifacts_dir, repo_root?, ticket?, stub_choices?}
    orchestrator/gate.decided   {slug, gate, decision: approved|rejected, reset_to?, note?}
    orchestrator/input.provided {slug, step_id, answer?, reset_to?}
Sends, for a chat bridge (slack_bridge.py):
    orchestrator/attention      {slug, kind: gate|question|finished, step_id, ...} before each wait and at the end
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shlex
import signal
import sys
from pathlib import Path

import inngest
import yaml
from inngest.connect import connect

HERE = Path(__file__).resolve().parent
# How to call the engine; argv tokens, never one quoted word.
ORCH = shlex.split(os.environ.get("ORCH", f"{sys.executable} -m orchestrator_next"))
STUB_CMD = [sys.executable, str(HERE / "stub_worker.py")]
# Judgment worker: called as `<cmd> <payload.json> <result.json>`; must write the
# result JSON ({status, outputs..., summary}). Default is the stub; set
# ORCH_JUDGMENT_CMD="python spike/inngest_driver/claude_worker.py" for Claude Code.
JUDGMENT_CMD = shlex.split(os.environ["ORCH_JUDGMENT_CMD"]) if os.environ.get("ORCH_JUDGMENT_CMD") else STUB_CMD
# Exec steps run their real script unless stubbed (the dev pack's scripts want a repo).
STUB_EXEC = os.environ.get("ORCH_STUB_EXEC", "1") == "1"
GATE_TIMEOUT = datetime.timedelta(days=7)
MAX_LOOPS = 200  # guard against a routing loop the engine's max_retries didn't catch

client = inngest.Inngest(
    app_id="orchestrator-driver",
    is_production=os.environ.get("INNGEST_DEV") != "1",
)


async def _sh(
    argv: list[str], env: dict | None = None, cwd: str | None = None, lock: Path | None = None
) -> tuple[int, str, str]:
    """Run argv in its own process group. With `lock`, the group id is kept in
    that file while it runs: a re-run of the same step (Inngest retrying after
    the worker died) first kills the group the dead worker left behind, so two
    agents never share a worktree."""
    if lock and lock.exists():
        try:
            os.killpg(int(lock.read_text()), signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
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
    code, out, err = await _sh(argv)
    if code != 0:
        # exit 3: usage/infra error. Raise so Inngest retries the step.
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


async def run_exec(run: dict, ans: dict, attempt: int, state: dict, extra_env: dict) -> dict:
    p, step_id = ans["payload"], ans["step_id"]
    res = _results_dir(run)
    stdout_file = res / f"{step_id}-{attempt}.stdout"
    env = _spawn_env(run, ans, attempt, state, extra_env)
    # A cloud session sets CLAUDE_CODE_REMOTE, which makes create-worktree a no-op.
    # Clear it for scripts only: Claude Code itself needs it to sign in there.
    env["CLAUDE_CODE_REMOTE"] = ""
    _state_path(run).write_text(json.dumps(state, indent=2))
    if STUB_EXEC:
        argv = [*STUB_CMD, "--exec", "-"]
        env["ORCH_PAYLOAD"] = json.dumps(p)
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


async def run_judgment(run: dict, ans: dict, attempt: int, state: dict, extra_env: dict) -> dict:
    p, step_id = ans["payload"], ans["step_id"]
    res_dir = _results_dir(run)
    payload_file = res_dir / f"{step_id}-{attempt}.payload.json"
    result_file = res_dir / f"{step_id}-{attempt}.json"
    payload_file.write_text(json.dumps(p))
    result_file.unlink(missing_ok=True)  # a stale file from a killed attempt is not a result
    env = _spawn_env(run, ans, attempt, state, extra_env)
    _state_path(run).write_text(json.dumps(state, indent=2))
    code, out, err = await _sh(
        [*JUDGMENT_CMD, str(payload_file), str(result_file)],
        env=env,
        cwd=env["WORKTREE_PATH"] or None,
        lock=res_dir / f"{step_id}.lock",
    )
    if not result_file.exists():
        # A missing result file means the step did not finish: let Inngest retry it.
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
        "worker": _last_json_line(out),  # model, cost, turns: shows on the dashboard
    }


@client.create_function(
    fn_id="drive-workflow",
    name="Drive workflow",
    trigger=inngest.TriggerEvent(event="orchestrator/run.requested"),
    # One step at a time on this machine (subscription seat). Waiting runs
    # (gates) hold no slot: Inngest counts running steps, not open runs.
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
)
async def _notify(ctx: inngest.Context, label: str, data: dict) -> None:
    """Tell the chat bridge a person is needed. Memoized like any step, so a replay
    doesn't post twice. It goes out before the wait opens, and a person's click
    comes seconds later at the earliest, so the wait has no lookback problem."""
    await ctx.step.send_event(label, [inngest.Event(name="orchestrator/attention", data=data)])


async def drive_workflow(ctx: inngest.Context) -> dict:
    d = ctx.event.data
    run = {k: d.get(k) for k in ("workflow", "pack", "slug", "artifacts_dir", "repo_root")}
    Path(run["artifacts_dir"]).mkdir(parents=True, exist_ok=True)
    extra_env = {
        "ORCH_STUB_CHOICES": json.dumps(d.get("stub_choices") or {}),
        "ORCH_USER_DIRECTION": str(d.get("ticket") or ""),
        "TICKET_ID": str(d.get("ticket_id") or ""),
    }

    attempts: dict[str, int] = {}
    # The skill's state file, rebuilt on every replay from memoized results.
    state: dict = {"tokens": [], "step_history": []}
    tokens: list[str] = state["tokens"]
    history: list[dict] = state["step_history"]
    after: dict | None = None
    ans: dict = {}
    last_ready: dict[str, dict] = {}
    last_summary: dict[str, str] = {}
    base = {"slug": run["slug"], "workflow": run["workflow"], "artifacts_dir": run["artifacts_dir"]}

    async def finish(n: int, outcome: dict) -> dict:
        await _notify(ctx, f"notify finished#{n}", {**base, "kind": "finished", "status": outcome["status"]})
        return outcome

    for n in range(MAX_LOOPS):
        label = f"next after {after['step']}#{after['attempt']}" if after else "next (start)"
        ans = await ctx.step.run(label, call_next, run, after)
        status = ans["status"]
        patch = (ans.get("recorded") or {}).get("state_patch") or {}
        state.update({k: v for k, v in patch.items() if k not in ("tokens", "step_history")})

        if status == "done":
            return await finish(n, {"status": "done", "history": history})
        if status == "error":
            # Engine rejected what we reported; nothing recorded. Surface it.
            return await finish(n, {"status": "error", "answer": ans, "history": history})

        step_id = ans["step_id"]
        if status == "needs_you":
            # Park the run until a person answers.
            await _notify(
                ctx,
                f"notify needs-you {step_id}#{n}",
                {**base, "kind": "question", "step_id": step_id, "question": last_summary.get(step_id, "")},
            )
            evt = await ctx.step.wait_for_event(
                f"needs-you {step_id}#{n}",
                event="orchestrator/input.provided",
                if_exp=f"async.data.slug == '{run['slug']}' && async.data.step_id == '{step_id}'",
                timeout=GATE_TIMEOUT,
            )
            if evt is None:
                return {"status": "needs_you", "answer": ans, "history": history}
            attempts[step_id] = attempts.get(step_id, 0) + 1
            if evt.data.get("reset_to"):
                after = {
                    "step": step_id,
                    "attempt": attempts[step_id],
                    "status": "failed",
                    "out": {"reset_to": evt.data["reset_to"]},
                }
                continue
            # await_input: re-run the same step with the answer.
            extra_env["ORCHESTRATOR_USER_ANSWER"] = str(evt.data.get("answer", ""))
            # needs_you carries no payload, so re-run from the step's last ready answer.
            if step_id not in last_ready:
                return {"status": "needs_you", "answer": ans, "history": history}
            ans = {**last_ready[step_id], "route": "await_input"}

        last_ready[step_id] = ans
        kind, p = ans["kind"], ans["payload"]
        attempts[step_id] = attempts.get(step_id, 0) + 1
        attempt = attempts[step_id]

        if p.get("requires") and p["requires"] not in tokens:
            # The engine can't check tokens; the driver must refuse.
            return {"status": "refused", "step_id": step_id, "missing_token": p["requires"], "history": history}

        if kind == "gate":
            await _notify(
                ctx,
                f"notify gate {step_id}#{attempt}",
                {**base, "kind": "gate", "step_id": step_id, "attempt": attempt, "show": p.get("show") or {}},
            )
            evt = await ctx.step.wait_for_event(
                f"gate {step_id}#{attempt}",
                event="orchestrator/gate.decided",
                if_exp=f"async.data.slug == '{run['slug']}' && async.data.gate == '{step_id}'",
                timeout=GATE_TIMEOUT,
            )
            if evt is None:
                return {"status": "gate_timeout", "gate": step_id, "history": history}
            if evt.data.get("decision") == "approved":
                tokens.append(p["approve_as"])
                after = {"step": step_id, "attempt": attempt, "status": "completed"}
            else:
                out = {"reset_to": evt.data["reset_to"]} if evt.data.get("reset_to") else None
                after = {"step": step_id, "attempt": attempt, "status": "failed", "out": out}
            history.append(
                {
                    "step": step_id,
                    "attempt": attempt,
                    "status": after["status"],
                    "gate": evt.data.get("decision"),
                    "note": evt.data.get("note"),
                }
            )
            continue

        runner = run_exec if kind == "exec" else run_judgment
        result = await ctx.step.run(f"{step_id}#{attempt}", runner, run, ans, attempt, state, extra_env)
        if kind == "exec":
            state.update({k: v for k, v in result["state"].items() if k not in ("tokens", "step_history")})
        history.append(
            {
                "step": step_id,
                "attempt": attempt,
                "route": ans["route"],
                "kind": kind,
                "status": result.get("status") or ("completed" if result["exit_code"] == 0 else "failed"),
                **({"exit_code": result["exit_code"]} if kind == "exec" else {}),
                **(
                    {"model": result["worker"].get("model"), "cost_usd": result["worker"].get("cost_usd")}
                    if result.get("worker")
                    else {}
                ),
            }
        )
        if result.get("summary"):
            last_summary[step_id] = result["summary"]
        after = {k: result[k] for k in ("step", "attempt", "exit_code", "stdout_file", "status", "out") if k in result}

    return await finish(MAX_LOOPS, {"status": "loop_guard", "last": ans, "history": history})


async def main() -> None:
    conn = connect([(client, [drive_workflow])], max_worker_concurrency=1)
    await conn.start()


if __name__ == "__main__":
    asyncio.run(main())
