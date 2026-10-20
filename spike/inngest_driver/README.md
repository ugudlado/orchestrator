# Spike: Inngest as the durable driver

Question: can Inngest stand in for the driver half of the system (run state,
retries, gates, intake, run dashboard) while `orchestrator next` keeps deciding
every step? This folder is a throwaway spike, not part of the engine wheel.

## What it is

`driver.py` is one generic Inngest function, `Drive workflow`, triggered by
`orchestrator/run.requested`. It loops:

1. `step.run("next after <step>#<attempt>")` calls `orchestrator next`
2. `exec` / `judgment` run as `step.run("<step>#<attempt>")`
3. `gate` becomes `step.wait_for_event("orchestrator/gate.decided")`, matched on slug + gate
4. `needs_you` waits on `orchestrator/input.provided` (answer or `reset_to`)

Attempts, gate tokens and history live in the function body. Inngest replays it
from memoized step results; the driver writes them out as the run's `state.yaml`
before each step only because the pack's scripts read that file. The worker connects out
over Connect (WebSocket), so the machine needs no inbound port. Concurrency is
one step at a time; runs parked at a gate hold no slot.

`claude_worker.py` runs each judgment step as one fresh `claude -p` process,
following the orchestrate skill: charter inlined (frontmatter stripped,
`{in.x}`/`{out.x}` substituted, `learnings.md` appended), model from the pack's
`models.yaml` tier, cwd in the run's worktree, `--add-dir` for the artifacts.
It reports model, cost and turns, which land in the step's output on the dashboard.
Select it with `ORCH_JUDGMENT_CMD="python /abs/claude_worker.py"`; `ORCH_CLAUDE_ARGS`
sets permissions (default `--permission-mode acceptEdits`), `ORCH_MODEL_OVERRIDE`
forces one model.

`stub_worker.py` is the default: it writes every `out` artifact and picks enum
values (`stub_choices` in the start event overrides them per step). Exec steps
run the pack's real scripts unless `ORCH_STUB_EXEC=1`.

Each step runs in its own process group, and its group id sits in
`results/<step>.lock` while it runs. When Inngest re-runs a step after the
worker died, the driver first kills the group the dead worker left behind.

## Run it

```bash
# server: single binary, SQLite + external Redis (see findings for why Redis)
npx inngest-cli@1.46.0 start --signing-key <hex> --event-key <key> --redis-uri redis://localhost:6379

# worker, from the orchestrator checkout
INNGEST_SIGNING_KEY=<hex> INNGEST_EVENT_KEY=<key> \
INNGEST_API_BASE_URL=http://localhost:8288 INNGEST_EVENT_API_BASE_URL=http://localhost:8288 \
uv run --with 'inngest[connect]==0.5.19' --with pyyaml python -m spike.inngest_driver.driver

# start a run, approve a gate
curl -X POST localhost:8288/e/<key> -d '{"name":"orchestrator/run.requested","data":{"workflow":"dev","pack":"/path/workflows/config","slug":"demo","repo_root":"/abs/repo","artifacts_dir":"/abs/runs/demo/artifacts","ticket":"<what to build>"}}'
curl -X POST localhost:8288/e/<key> -d '{"name":"orchestrator/gate.decided","data":{"slug":"demo","gate":"design-signoff","decision":"approved"}}'
```

Dashboard: http://localhost:8288.

## Findings (2026-10-20, Inngest server 1.46.0, Python SDK 0.5.19)

| Test                                                                    | Result                                                                                                         |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| dev.yaml feature path, stub steps                                       | Passed: 26 steps, 3 gates approved by event, `status: done`                                                    |
| Hotfix path, gate rejected with `reset_to: diagnose`                    | Passed: routed to diagnose#2, then back to the gate                                                            |
| Server restart while a run waits at a gate, **default in-memory state** | **Failed**: the run still shows Running, but the gate event never resumes it                                   |
| Same, with `--redis-uri` (Redis with AOF)                               | Passed: the event resumed the run after the restart                                                            |
| Worker SIGKILLed mid-step (20 s step)                                   | Recovered, but slowly: the step re-ran about 3 minutes later, and the orphaned child process **also** finished |
| Concurrency 1 + gates                                                   | Runs parked at a gate don't block other runs                                                                   |

### Real Claude Code (`claude_worker.py`, later the same day)

| Test                                                                                    | Result                                                                                                         |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| dev.yaml on a toy repo, ticket "add a `--shout` flag with a test", every step on Sonnet | 19 steps through verify-changes; one commit, tests pass, about $2.17 in total, roughly 10 to 20 cents per step |
| implement failed (the machine had no pytest)                                            | The engine routed back to design (`on_failure`), as configured; the run continued after pytest was installed   |
| Worker restarted while the run waited at a gate                                         | The run continued after the restart                                                                            |
| human-review asked a question (`await_input`)                                           | Parked as `needs_you`; an `input.provided` event re-ran the step with the answer                               |
| Full path with real exec scripts (stub judgment)                                        | Passed: worktree created and removed, state file stamped, workflow-report counts 26 completed                  |
| Worker SIGKILLed mid-step, orphan check                                                 | PASS: the re-run (about 2.5 min later) killed the leftover process group first                                 |

Found on the way:

- **An event sent before the wait opens is dropped.** `wait_for_event` has no lookback, so an approval or answer must come after the gate or question is posted (true for Slack buttons). Twice a scripted answer arrived seconds early and was lost.
- In a Claude Code cloud session, `CLAUDE_CODE_REMOTE` must stay set for `claude` to sign in, but it makes create-worktree a no-op, so the driver clears it for exec scripts only.
- Four pack scripts are committed without the executable bit (the driver falls back to `bash`), and exec scripts need `python3` with PyYAML on PATH.
- **Cancel doesn't stop a step that is already running.** After Cancel, the step's process kept going. A real worker should check for cancellation and kill its group.
- A step that raises (a driver bug, not a failed step) fails the whole run after Inngest's retries; the trace shows the Python stack for it, which made each bug quick to find.

Dashboard gives, with no UI work: a runs list, a per-run trace (every `next`
call and step, with durations, inputs and outputs), the gate wait's match
expression, an event log, Cancel, Rerun and "Rerun from step".

Gaps and sharp edges:

- **Use external Redis.** The default in-memory store lost a parked gate across a restart.
- **Orphaned workers.** Killing the worker doesn't kill the step's subprocess, and Inngest re-runs the step anyway. Fixed here with a process group per step and a lock file holding its id; tested above.
- **"Rerun from step" rewinds Inngest's memo, not the artifacts on disk.** Treat it as unsafe for `state_mutating` steps.
- **No auth on the self-hosted dashboard or API.** Keep it on the tailnet only. Anyone holding the event key can start a run whose ticket text reaches an agent with edit rights: the worker fences ticket text and answers as untrusted data, and the real worker should keep a narrow tool allowlist (`ORCH_CLAUDE_ARGS`).
- The dashboard is a developer trace. Step names and JSON, not plain language, and nothing inside a Claude Code step unless the worker streams it.
- The `/v1/events/{id}/runs` list reported `Completed` for runs that `/v1/runs/{id}` showed `Running`. Don't build on the list endpoint.
- Lost-worker recovery took about 3 minutes (Connect lease plus retry). That's fine for agent steps, but worth knowing.
