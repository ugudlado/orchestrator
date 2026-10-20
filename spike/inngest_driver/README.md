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
from memoized step results, so there is no state.yaml. The worker connects out
over Connect (WebSocket), so the machine needs no inbound port. Concurrency is
one step at a time; runs parked at a gate hold no slot.

`stub_worker.py` stands in for Claude Code: it writes every `out` artifact and
picks enum values (`stub_choices` in the start event overrides them per step).
Swap it with `ORCH_JUDGMENT_CMD` (called as `<cmd> <payload.json> <result.json>`).

## Run it

```bash
# server: single binary, SQLite + external Redis (see findings for why Redis)
npx inngest-cli@1.46.0 start --signing-key <hex> --event-key <key> --redis-uri redis://localhost:6379

# worker, from the orchestrator checkout
INNGEST_SIGNING_KEY=<hex> INNGEST_EVENT_KEY=<key> \
INNGEST_API_BASE_URL=http://localhost:8288 INNGEST_EVENT_API_BASE_URL=http://localhost:8288 \
uv run --with 'inngest[connect]==0.5.19' --with pyyaml python -m spike.inngest_driver.driver

# start a run, approve a gate
curl -X POST localhost:8288/e/<key> -d '{"name":"orchestrator/run.requested","data":{"workflow":"dev","pack":"/path/workflows/config","slug":"demo","artifacts_dir":"/abs/runs/demo/artifacts"}}'
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

Dashboard gives, with no UI work: a runs list, a per-run trace (every `next`
call and step, with durations, inputs and outputs), the gate wait's match
expression, an event log, Cancel, Rerun and "Rerun from step".

Gaps and sharp edges:

- **Use external Redis.** The default in-memory store lost a parked gate across a restart.
- **Orphaned workers.** Killing the worker doesn't kill the step's subprocess, and Inngest re-runs the step anyway. With Claude Code that means two agents in one worktree. A real worker needs a process group and a per-step lock.
- **"Rerun from step" rewinds Inngest's memo, not the artifacts on disk.** Treat it as unsafe for `state_mutating` steps.
- **No auth on the self-hosted dashboard or API.** Keep it on the tailnet only.
- The dashboard is a developer trace. Step names and JSON, not plain language, and nothing inside a Claude Code step unless the worker streams it.
- The `/v1/events/{id}/runs` list reported `Completed` for runs that `/v1/runs/{id}` showed `Running`. Don't build on the list endpoint.
- Lost-worker recovery took about 3 minutes (Connect lease plus retry). That's fine for agent steps, but worth knowing.
