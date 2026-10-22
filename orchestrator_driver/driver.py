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
import tempfile
import time

from orchestrator_driver.config import Config
from orchestrator_driver.db import DB
from orchestrator_driver.steps import _state_path, call_next, kill_group, run_exec, run_judgment, write_private

MAX_LOOPS = 200  # guard against a routing loop the engine's max_retries didn't catch
RETRIES = 2  # a step that raises is retried twice, 1 s apart
SKIP = ("tokens", "step_history")  # kept by the driver, never overwritten from a patch or a script


NOTIFY_TIMEOUT = 30  # seconds a notify_cmd may run before it is killed


class Stopped(Exception):
    pass


def _num(v) -> int | None:
    """An event's attempt or wait as an int, or None if it is not one (events are written by other programs)."""
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        return None
    try:
        return int(v)
    except ValueError:
        return None


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
        self.notifying: list[tuple[subprocess.Popen, float, str]] = []

    # --- loop -----------------------------------------------------------------

    async def serve(self) -> None:
        self.recover()
        while True:
            self.reap_notifies()
            self.apply_stops()
            run = self.pick()
            if run is None:
                await asyncio.sleep(self.cfg.poll_seconds)
            else:
                await self.advance(run)

    def recover(self) -> None:
        """A run still marked as running a step lost its worker: kill the process group the dead
        worker recorded (if it is still that step's). The step itself re-runs from the saved
        state, with the same attempt number."""
        for r in self.db.active_runs():
            if r["current_step"]:
                killed = kill_group(r["pid"], _state_path(r["request"]))
                self.db.log(r["slug"], "recovered", {"step": r["current_step"], "pid": r["pid"], "killed": killed})
                self.db.update(r["slug"], current_step=None, pid=None, started_at=None)

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
            d = e["data"] if isinstance(e["data"], dict) else {}
            if d.get(key) != w["step_id"]:  # another step's event: not ours to judge
                continue
            n = _num(d.get(num))
            if n is None:  # garbage in attempt/wait can never match: consume it, don't crash on it
                self.db.consume(e["id"])
                self.db.log(run["slug"], "event_stale", {"event": e["id"], "note": f"stale: bad {num}", "data": d})
            elif n == w[num]:
                return e
            elif n < w[num]:
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
            kill_group(run["pid"], _state_path(run["request"]))
        outcome = {"status": "stopped", "history": (run["loop"].get("state") or {}).get("step_history", [])}
        self.close(run["slug"], run["request"], run["loop"], outcome)
        self.db.update(run["slug"], status="stopped", waiting=None, current_step=None, pid=None, outcome=outcome)
        self.db.log(run["slug"], "stopped", {})

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
                "state": {  # the state file the pack's scripts read (docs/pack-driver-notes.md)
                    "change_id": slug,
                    "slug": slug,
                    "schema": req["workflow"],
                    "status": "active",
                    "repo_root": req["repo_root"],
                    "user_input": str(req.get("ticket") or ""),
                    "ticket_id": str(req.get("ticket_id") or ""),
                    "workflow_plan": {"main": {"nodes": []}},
                    "tokens": [],
                    "step_history": [],
                },
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
            if after is None or after.get("reported_error"):
                return self.finish(slug, req, loop, {"status": "error", "answer": ans})
            # Report the step as failed once, so its on_failure routing applies (e.g. an agent forgot a file).
            self.db.log(slug, "engine_error", {"step": after["step"], "attempt": after["attempt"], "error": ans})
            for h in reversed(state["step_history"]):
                if h["step"] == after["step"] and h["attempt"] == after["attempt"]:
                    h.update(status="failed", engine_error=str(ans.get("error"))[:300])
                    break
            loop.update(
                after={
                    "step": after["step"],
                    "attempt": after["attempt"],
                    "status": "failed",
                    "out": {"reason": str(ans.get("error"))[:300]},
                    "reported_error": True,
                },
                n=loop["n"] + 1,
            )
            return self.db.update(slug, loop=loop)
        loop["ans"] = ans
        step_id = ans["step_id"]
        if ans["status"] == "needs_you":
            # Each answer re-runs the step. A step that keeps asking (say it can't push, and every answer is
            # "go ahead") would loop until MAX_LOOPS, paying for every re-run: end the run instead.
            asks = loop.setdefault("asks", {})
            asks[step_id] = asks.get(step_id, 0) + 1
            if asks[step_id] > self.cfg.max_question_reruns:  # this ask comes after that many answered re-runs
                question = loop["last_summary"].get(step_id, "")
                return self.finish(
                    slug, req, loop, {"status": "question_limit", "step_id": step_id, "question": question}
                )
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
        self.db.update(slug, current_step=step_id, pid=None, started_at=None)
        self.db.log(slug, "step_start", {"step": step_id, "attempt": attempt, "kind": kind, "route": ans["route"]})
        t0 = time.time()
        try:
            result = await self.watch(
                slug,
                _retry(
                    self.db,
                    slug,
                    runner,
                    self.cfg,
                    req,
                    ans,
                    attempt,
                    loop["state"],
                    loop["extra_env"],
                    self.on_pid(slug),
                ),
            )
        except Stopped:
            return self.stop(self.db.run(slug))
        except Exception as e:  # noqa: BLE001 - a step or engine failure must not kill the worker loop
            self.db.log(slug, "step_error", {"step": step_id, "attempt": attempt, "error": str(e)[-500:]})
            return self.finish(slug, req, loop, {"status": "error", "step_id": step_id, "error": str(e)[-500:]})
        finally:  # the group is gone (or being killed) whichever way the step ended
            self.db.update(slug, pid=None, started_at=None)
        if loop.get("answer_step") == step_id:  # the answer belongs to this one re-run
            loop["extra_env"].pop("ORCHESTRATOR_USER_ANSWER", None)
            loop.pop("answer_step")
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

    def on_pid(self, slug: str):
        """Called with the step's process group id as soon as it exists. Written once per process, not polled."""
        return lambda pid: self.db.update(slug, pid=pid, started_at=time.time())

    async def watch(self, slug: str, coro):
        """Await a step while polling for a stop. The worker owns the child, so cancelling
        the task makes `_sh` kill the child's process group."""
        task = asyncio.ensure_future(coro)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.2)
                if done:
                    return task.result()
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
        write_private(_state_path(req), json.dumps(loop["state"], indent=2), 0o644)
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
            attempt = loop["attempts"].get(step_id, 0)  # the attempt that asked; dispatch counts the re-run
            after = None
            if d.get("reset_to"):
                after = {"step": step_id, "attempt": attempt, "status": "failed", "out": {"reset_to": d["reset_to"]}}
                loop["n"] += 1
            elif step_id in loop["last_ready"]:
                # await_input: re-run the same step with the answer, from its last ready answer
                # (needs_you carries no payload of its own).
                loop["extra_env"]["ORCHESTRATOR_USER_ANSWER"] = str(d.get("answer", ""))
                loop["answer_step"] = step_id
                rerun = {**loop["last_ready"][step_id], "route": "await_input"}
            else:
                return self.finish(slug, req, loop, {"status": "question_timeout", "step_id": step_id})
        loop.update(after=after, phase="next")
        with self.db.tx():  # closing the wait and starting what follows commit together
            self.db.consume(ev["id"])
            self.db.update(slug, status="running", waiting=None, loop=loop)
            if rerun:
                self.dispatch(slug, req, loop, rerun)

    def close(self, slug: str, req: dict, loop: dict, outcome: dict) -> None:
        """What every ending has in common: the state file, the finished notification, no events left open."""
        if loop.get("state"):
            write_private(_state_path(req), json.dumps(loop["state"], indent=2), 0o644)
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

    def finish(self, slug: str, req: dict, loop: dict, outcome: dict) -> None:
        outcome["history"] = loop["state"]["step_history"]
        self.close(slug, req, loop, outcome)
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
        """Optional: `notify_cmd` gets the attention JSON on stdin before each wait and at finish. It runs beside
        the loop (reaped in `reap_notifies`, killed after NOTIFY_TIMEOUT), so a slow hook never stalls a run."""
        if not self.cfg.notify_cmd:
            return
        try:
            with tempfile.TemporaryFile("w+") as f:
                f.write(json.dumps(data))
                f.seek(0)
                proc = subprocess.Popen(
                    shlex.split(self.cfg.notify_cmd),
                    stdin=f,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            self.notifying.append((proc, time.time(), data["slug"]))
        except Exception as e:  # noqa: BLE001 - a step or engine failure must not kill the worker loop
            self.db.log(data["slug"], "notify_error", {"error": str(e)[-300:]})

    def reap_notifies(self) -> None:
        for item in list(self.notifying):
            proc, t0, slug = item
            code = proc.poll()
            if code is None and time.time() - t0 > NOTIFY_TIMEOUT:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                code = "timeout"
            if code is not None:
                self.notifying.remove(item)
                if code != 0:
                    self.db.log(slug, "notify_error", {"error": f"notify_cmd exited {code}"})


async def serve(cfg: Config) -> None:
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await Worker(DB(cfg.db), cfg).serve()
    except asyncio.CancelledError:
        pass  # SIGTERM: the child is dead; the step stays marked running for the next start
