"""The driver: one worker process and a SQLite file, around `orchestrator next`.

The engine decides every step; this loop calls it, runs what it names, and waits where it says to. The
state is a row: after every `next` call and every step the run's loop state is committed, so a crash
resumes from the last commit. Waits are rows too: an event matching an open wait is consumed when it is
found, and an event that arrives before its wait opens just stays unconsumed until it does.

    orchestrator-driver serve --config driver.yaml     # the single worker: one step at a time
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import subprocess
import time

from orchestrator_driver.config import Config
from orchestrator_driver.db import DB
from orchestrator_driver.steps import _results_dir, _state_path, call_next, kill_lock, run_exec, run_judgment

MAX_LOOPS = 200  # guard against a routing loop the engine's max_retries didn't catch
RETRIES = 2  # a step that raises is retried twice, 1 s apart
SKIP = ("tokens", "step_history")  # kept by the driver, never overwritten from a patch or a script


class Stopped(Exception):
    pass


async def _retry(db: DB, slug: str, fn, *args):
    for i in range(RETRIES + 1):
        try:
            return await fn(*args)
        except Exception as e:
            db.log(slug, "retry", {"try": i + 1, "error": str(e)[-500:]})
            if i == RETRIES:
                raise
            await asyncio.sleep(1)


class Worker:
    def __init__(self, db: DB, cfg: Config):
        self.db, self.cfg = db, cfg

    # --- loop -----------------------------------------------------------------

    async def serve(self) -> None:
        self.recover()
        while True:
            self.apply_stops()
            run = self.pick()
            if run is None:
                await asyncio.sleep(self.cfg.poll_seconds)
            else:
                await self.advance(run)

    def recover(self) -> None:
        """A run still marked as running a step lost its worker: kill what the dead
        worker left in the step's lock. The step itself re-runs from the saved
        state, with the same attempt number."""
        for r in self.db.active_runs():
            if r["current_step"]:
                kill_lock(_results_dir(r["request"]) / f"{r['current_step']}.lock")
                self.db.log(r["slug"], "recovered", {"step": r["current_step"], "pid": r["pid"]})
                self.db.update(r["slug"], current_step=None, pid=None)

    def pick(self) -> dict | None:
        """The oldest run with something to do."""
        for r in self.db.active_runs():
            if r["status"] in ("queued", "running"):
                return r
            if self.find_event(r) or time.time() - r["waiting"]["opened_at"] > self.cfg.gate_timeout_days * 86400:
                return r
        return None

    def find_event(self, run: dict) -> dict | None:
        """The unconsumed event answering run's open wait. Events for an earlier
        attempt/wait of the same step can never match again: consume them as stale."""
        w = run["waiting"]
        gate = w["kind"] == "gate"
        name, key, num = ("gate.decided", "gate", "attempt") if gate else ("input.provided", "step_id", "wait")
        for e in self.db.open_events(run["slug"], name):
            d = e["data"]
            if d.get(key) != w["step_id"]:
                continue
            if d.get(num) == w[num]:
                return e
            if (d.get(num) or 0) < w[num]:
                self.db.consume(e["id"])
                self.db.log(
                    run["slug"], "event_stale", {"event": e["id"], **d, "note": f"stale: now at {num} {w[num]}"}
                )
        return None

    def apply_stops(self, running: str | None = None) -> bool:
        """Consume stop events. True if one is for `running` (the caller kills its own child)."""
        hit = False
        for e in self.db.open_events(name="stop"):
            self.db.consume(e["id"])
            run = self.db.run(e["slug"])
            if not run or run["status"] in ("done", "failed", "stopped"):
                continue
            if e["slug"] == running:
                hit = True
            else:
                self.stop(run)
        return hit

    def stop(self, run: dict) -> None:
        if run["current_step"]:
            kill_lock(_results_dir(run["request"]) / f"{run['current_step']}.lock")
        self.db.log(run["slug"], "stopped", {})
        self.db.update(
            run["slug"], status="stopped", waiting=None, current_step=None, pid=None, outcome={"status": "stopped"}
        )

    # --- one unit of work -----------------------------------------------------

    async def advance(self, run: dict) -> None:
        slug, req, loop = run["slug"], run["request"], run["loop"]
        if run["status"] == "queued":
            os.makedirs(req["artifacts_dir"], exist_ok=True)
            loop = {
                "n": 0,
                "attempts": {},
                "after": None,
                "ans": {},
                "phase": "next",
                "state": {"tokens": [], "step_history": []},  # the skill's state file
                "last_ready": {},
                "last_summary": {},
                "extra_env": {
                    "ORCH_STUB_CHOICES": json.dumps(req.get("stub_choices") or {}),
                    "ORCH_USER_DIRECTION": str(req.get("ticket") or ""),
                    "TICKET_ID": str(req.get("ticket_id") or ""),
                },
            }
            self.db.update(slug, status="running", loop=loop)
        if run["status"] in ("waiting", "needs_you"):
            await self.resume(run)
        elif loop["phase"] == "step":
            await self.do_step(slug, req, loop)
        else:
            await self.do_next(slug, req, loop)

    async def do_next(self, slug: str, req: dict, loop: dict) -> None:
        if loop["n"] >= MAX_LOOPS:
            return self.finish(slug, req, loop, {"status": "loop_guard", "last": loop["ans"]})
        state, after = loop["state"], loop["after"]
        try:
            ans = await _retry(self.db, slug, call_next, req, after)
        except Exception as e:  # noqa: BLE001 - a step or engine failure must not kill the worker loop
            return self.finish(slug, req, loop, {"status": "error", "error": str(e)})
        self.db.log(
            slug,
            "next",
            {"after": after and f"{after['step']}#{after['attempt']}"}
            | {k: ans.get(k) for k in ("status", "step_id", "kind", "route", "reason")},
        )
        patch = (ans.get("recorded") or {}).get("state_patch") or {}
        state.update({k: v for k, v in patch.items() if k not in SKIP})
        if ans["status"] == "done":
            return self.finish(slug, req, loop, {"status": "done"})
        if ans["status"] == "error":  # engine rejected what we reported; nothing recorded
            return self.finish(slug, req, loop, {"status": "error", "answer": ans})
        loop["ans"] = ans
        step_id = ans["step_id"]
        if ans["status"] == "needs_you":
            q = {
                "kind": "question",
                "step_id": step_id,
                "wait": loop["n"],
                "question": loop["last_summary"].get(step_id, ""),
                "reason": ans.get("reason"),
            }
            return self.open_wait(slug, req, loop, q, "needs_you")
        self.dispatch(slug, req, loop, ans)

    def dispatch(self, slug: str, req: dict, loop: dict, ans: dict) -> None:
        """A ready step: count the attempt, then wait at it (gate) or queue it to run."""
        step_id, p = ans["step_id"], ans["payload"]
        loop["ans"] = loop["last_ready"][step_id] = ans
        attempt = loop["attempts"][step_id] = loop["attempts"].get(step_id, 0) + 1
        if p.get("requires") and p["requires"] not in loop["state"]["tokens"]:
            # The engine can't check tokens; the driver must refuse.
            return self.finish(
                slug, req, loop, {"status": "refused", "step_id": step_id, "missing_token": p["requires"]}
            )
        if ans["kind"] == "gate":
            g = {"kind": "gate", "step_id": step_id, "attempt": attempt, "show": p.get("show") or {}}
            return self.open_wait(slug, req, loop, g, "waiting")
        loop.update(phase="step", attempt=attempt)
        self.db.update(slug, status="running", loop=loop)

    async def do_step(self, slug: str, req: dict, loop: dict) -> None:
        ans, attempt = loop["ans"], loop["attempt"]
        step_id, kind = ans["step_id"], ans["kind"]
        runner = run_exec if kind == "exec" else run_judgment
        self.db.update(slug, current_step=step_id, pid=None)
        self.db.log(slug, "step_start", {"step": step_id, "attempt": attempt, "kind": kind, "route": ans["route"]})
        t0 = time.time()
        try:
            result = await self.watch(
                slug,
                req,
                step_id,
                _retry(self.db, slug, runner, self.cfg, req, ans, attempt, loop["state"], loop["extra_env"]),
            )
        except Stopped:
            return self.stop(self.db.run(slug))
        except Exception as e:  # noqa: BLE001 - a step or engine failure must not kill the worker loop
            self.db.log(slug, "step_error", {"step": step_id, "attempt": attempt, "error": str(e)[-500:]})
            return self.finish(slug, req, loop, {"status": "error", "step_id": step_id, "error": str(e)[-500:]})
        state, worker = loop["state"], result.get("worker") or {}
        if kind == "exec":
            state.update({k: v for k, v in result["state"].items() if k not in SKIP})
        status = result.get("status") or ("completed" if result["exit_code"] == 0 else "failed")
        state["step_history"].append(
            {
                "step": step_id,
                "attempt": attempt,
                "route": ans["route"],
                "kind": kind,
                "status": status,
                **({"exit_code": result["exit_code"]} if kind == "exec" else {}),
                **({"model": worker.get("model"), "cost_usd": worker.get("cost_usd")} if worker else {}),
            }
        )
        if result.get("summary"):
            loop["last_summary"][step_id] = result["summary"]
        loop["after"] = {
            k: result[k] for k in ("step", "attempt", "exit_code", "stdout_file", "status", "out") if k in result
        }
        loop.update(phase="next", n=loop["n"] + 1)
        self.db.log(
            slug,
            "step_end",
            {
                "step": step_id,
                "attempt": attempt,
                "status": status,
                "exit_code": result.get("exit_code"),
                "model": worker.get("model"),
                "cost_usd": worker.get("cost_usd"),
                "duration_s": round(time.time() - t0, 2),
            },
        )
        self.db.update(slug, current_step=None, pid=None, loop=loop)

    async def watch(self, slug: str, req: dict, step_id: str, coro):
        """Await a step while polling for its pid and for a stop. The worker owns the
        child, so cancelling the task makes `_sh` kill the child's process group."""
        task = asyncio.ensure_future(coro)
        lock = _results_dir(req) / f"{step_id}.lock"
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.2)
                if done:
                    return task.result()
                if lock.exists():
                    self.db.update(slug, pid=int(lock.read_text() or 0))
                if self.apply_stops(running=slug):
                    raise Stopped
        finally:  # also on SIGTERM: the child dies, the row keeps saying it was running
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    # --- waits ----------------------------------------------------------------

    def open_wait(self, slug: str, req: dict, loop: dict, waiting: dict, status: str) -> None:
        # Exec and judgment steps write the state file before they run; writing it
        # before each wait and at the end too lets Studio show the full history.
        _state_path(req).write_text(json.dumps(loop["state"], indent=2))
        base = {"slug": slug, "workflow": req["workflow"], "artifacts_dir": req["artifacts_dir"]}
        self.notify({**base, **{k: v for k, v in waiting.items()}})
        waiting["opened_at"] = time.time()
        self.db.log(slug, "wait_open", waiting)
        self.db.update(slug, status=status, waiting=waiting, loop=loop)

    async def resume(self, run: dict) -> None:
        slug, req, loop, w = run["slug"], run["request"], run["loop"], run["waiting"]
        state, step_id, ev = loop["state"], w["step_id"], self.find_event(run)
        if ev is None:  # picked for the timeout
            if w["kind"] == "gate":
                return self.finish(slug, req, loop, {"status": "gate_timeout", "gate": step_id})
            return self.finish(slug, req, loop, {"status": "question_timeout", "step_id": step_id})
        d, rerun = ev["data"], None
        self.db.log(slug, "wait_close", {"event": ev["id"], **w, "data": d})
        if w["kind"] == "gate":
            if d.get("decision") == "approved":
                state["tokens"].append(loop["ans"]["payload"]["approve_as"])
                after = {"step": step_id, "attempt": w["attempt"], "status": "completed"}
            else:
                out = {"reset_to": d["reset_to"]} if d.get("reset_to") else None
                after = {"step": step_id, "attempt": w["attempt"], "status": "failed", "out": out}
            state["step_history"].append(
                {
                    "step": step_id,
                    "attempt": w["attempt"],
                    "status": after["status"],
                    "gate": d.get("decision"),
                    "note": d.get("note"),
                }
            )
            loop["n"] += 1
        else:
            attempt = loop["attempts"][step_id] = loop["attempts"].get(step_id, 0) + 1
            after = None
            if d.get("reset_to"):
                after = {"step": step_id, "attempt": attempt, "status": "failed", "out": {"reset_to": d["reset_to"]}}
                loop["n"] += 1
            elif step_id in loop["last_ready"]:
                # await_input: re-run the same step with the answer, from its last ready answer
                # (needs_you carries no payload of its own).
                loop["extra_env"]["ORCHESTRATOR_USER_ANSWER"] = str(d.get("answer", ""))
                rerun = {**loop["last_ready"][step_id], "route": "await_input"}
            else:
                return self.finish(slug, req, loop, {"status": "question_timeout", "step_id": step_id})
        loop.update(after=after, phase="next")
        with self.db.tx():  # closing the wait and starting what follows commit together
            self.db.consume(ev["id"])
            self.db.update(slug, status="running", waiting=None, loop=loop)
            if rerun:
                self.dispatch(slug, req, loop, rerun)

    def finish(self, slug: str, req: dict, loop: dict, outcome: dict) -> None:
        outcome["history"] = loop["state"]["step_history"]
        _state_path(req).write_text(json.dumps(loop["state"], indent=2))
        self.notify(
            {
                "slug": slug,
                "workflow": req["workflow"],
                "artifacts_dir": req["artifacts_dir"],
                "kind": "finished",
                "status": outcome["status"],
            }
        )
        for e in self.db.open_events(slug):
            self.db.consume(e["id"])
            self.db.log(slug, "event_stale", {"event": e["id"], "note": "stale: run finished"})
        self.db.log(slug, "finished", {"status": outcome["status"]})
        self.db.update(
            slug,
            status="done" if outcome["status"] == "done" else "failed",
            waiting=None,
            current_step=None,
            pid=None,
            loop=loop,
            outcome=outcome,
        )

    def notify(self, data: dict) -> None:
        """Optional: `notify_cmd` gets the attention JSON on stdin before each wait and at finish."""
        if not self.cfg.notify_cmd:
            return
        try:
            subprocess.run(shlex.split(self.cfg.notify_cmd), input=json.dumps(data), text=True, timeout=30, check=True)
        except Exception as e:  # noqa: BLE001 - a step or engine failure must not kill the worker loop
            self.db.log(data["slug"], "notify_error", {"error": str(e)[-300:]})


async def serve(cfg: Config) -> None:
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await Worker(DB(cfg.db), cfg).serve()
    except asyncio.CancelledError:
        pass  # SIGTERM: the child is dead; the step stays marked running for the next start
