"""Spike: an Inngest function that drives `orchestrator next` end to end.

One generic function runs any workflow in any pack. The engine still decides
every step; Inngest only holds what the driver skill keeps in state.yaml today
(attempts, gate tokens, history) and makes it durable: each `next` call and
each step is a memoized `step.run`, and gates are `step.wait_for_event`.

Run (from the orchestrator checkout):

    uv run --with inngest==0.5.19 --with pyyaml python -m spike.inngest_driver.driver

Events:
    orchestrator/run.requested  {workflow, pack, slug, artifacts_dir, ticket?, stub_choices?}
    orchestrator/gate.decided   {slug, gate, decision: approved|rejected, reset_to?, note?}
    orchestrator/input.provided {slug, step_id, answer?, reset_to?}
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shlex
import sys
from pathlib import Path

import inngest
from inngest.connect import connect

HERE = Path(__file__).resolve().parent
# How to call the engine; argv tokens, never one quoted word.
ORCH = shlex.split(os.environ.get("ORCH", f"{sys.executable} -m orchestrator_next"))
# Judgment worker: called as `<cmd> <payload.json> <result.json>`; must write the
# result JSON ({status, outputs..., summary}). Default is the stub.
JUDGMENT_CMD = shlex.split(os.environ.get("ORCH_JUDGMENT_CMD", f"{sys.executable} {HERE / 'stub_worker.py'}"))
# Exec steps run their real script unless stubbed (the dev pack's scripts want a repo).
STUB_EXEC = os.environ.get("ORCH_STUB_EXEC", "1") == "1"
GATE_TIMEOUT = datetime.timedelta(days=7)
MAX_LOOPS = 200  # guard against a routing loop the engine's max_retries didn't catch

client = inngest.Inngest(
    app_id="orchestrator-driver",
    is_production=os.environ.get("INNGEST_DEV") != "1",
)


async def _sh(argv: list[str], env: dict | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
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


async def run_exec(run: dict, ans: dict, attempt: int, extra_env: dict) -> dict:
    p = ans["payload"]
    stdout_file = _results_dir(run) / f"{ans['step_id']}-{attempt}.stdout"
    if STUB_EXEC:
        code, out, err = await _sh(
            [*JUDGMENT_CMD, "--exec", "-"], env={**p["env"], **extra_env, "ORCH_PAYLOAD": json.dumps(p)}
        )
    else:
        code, out, err = await _sh([p["run_path"]], env={**p["env"], **extra_env})
    stdout_file.write_text(out)
    return {
        "step": ans["step_id"],
        "attempt": attempt,
        "exit_code": code,
        "stdout_file": str(stdout_file),
        "stdout_tail": out[-2000:],
        "stderr_tail": err[-2000:],
    }


async def run_judgment(run: dict, ans: dict, attempt: int, extra_env: dict) -> dict:
    p = ans["payload"]
    res_dir = _results_dir(run)
    payload_file = res_dir / f"{ans['step_id']}-{attempt}.payload.json"
    result_file = res_dir / f"{ans['step_id']}-{attempt}.json"
    payload_file.write_text(json.dumps(p))
    code, out, err = await _sh([*JUDGMENT_CMD, str(payload_file), str(result_file)], env={**p["env"], **extra_env})
    if not result_file.exists():
        # A missing result file means the step did not finish: let Inngest retry it.
        raise RuntimeError(f"worker exited {code} without writing {result_file}: {err[-500:]}")
    result = json.loads(result_file.read_text())
    status = result.pop("status", "completed")
    result.pop("summary", None)
    return {
        "step": ans["step_id"],
        "attempt": attempt,
        "status": status,
        "out": result,
        "worker_stdout_tail": out[-2000:],
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
async def drive_workflow(ctx: inngest.Context) -> dict:
    d = ctx.event.data
    run = {k: d[k] for k in ("workflow", "pack", "slug", "artifacts_dir")}
    Path(run["artifacts_dir"]).mkdir(parents=True, exist_ok=True)
    extra_env = {"ORCH_STUB_CHOICES": json.dumps(d.get("stub_choices") or {}), "TICKET_ID": str(d.get("ticket") or "")}

    attempts: dict[str, int] = {}
    tokens: list[str] = []
    history: list[dict] = []
    after: dict | None = None
    ans: dict = {}

    for n in range(MAX_LOOPS):
        label = f"next after {after['step']}#{after['attempt']}" if after else "next (start)"
        ans = await ctx.step.run(label, call_next, run, after)
        status = ans["status"]

        if status == "done":
            return {"status": "done", "history": history}
        if status == "error":
            # Engine rejected what we reported; nothing recorded. Surface it.
            return {"status": "error", "answer": ans, "history": history}

        step_id = ans["step_id"]
        if status == "needs_you":
            # Park the run until a person answers (in Slack, later).
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
            ans = {**ans, "status": "ready", "kind": ans.get("kind", "judgment")}
            if "payload" not in ans:
                return {"status": "needs_you", "answer": ans, "history": history}

        kind, p = ans["kind"], ans["payload"]
        attempts[step_id] = attempts.get(step_id, 0) + 1
        attempt = attempts[step_id]

        if p.get("requires") and p["requires"] not in tokens:
            # The engine can't check tokens; the driver must refuse.
            return {"status": "refused", "step_id": step_id, "missing_token": p["requires"], "history": history}

        if kind == "gate":
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
                {"step": step_id, "attempt": attempt, "gate": evt.data.get("decision"), "note": evt.data.get("note")}
            )
            continue

        runner = run_exec if kind == "exec" else run_judgment
        result = await ctx.step.run(f"{step_id}#{attempt}", runner, run, ans, attempt, extra_env)
        history.append(
            {
                "step": step_id,
                "attempt": attempt,
                "route": ans["route"],
                "kind": kind,
                **({"exit_code": result["exit_code"]} if kind == "exec" else {"status": result["status"]}),
            }
        )
        after = {k: result[k] for k in ("step", "attempt", "exit_code", "stdout_file", "status", "out") if k in result}

    return {"status": "loop_guard", "last": ans, "history": history}


async def main() -> None:
    conn = connect([(client, [drive_workflow])], max_worker_concurrency=1)
    await conn.start()


if __name__ == "__main__":
    asyncio.run(main())
