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

## Studio on your laptop

One command starts Redis (if installed), Inngest, the worker and Studio, all on 127.0.0.1:

```bash
# workflows pack checked out next to this repo (../workflows), claude signed in
spike/inngest_driver/run_local.sh --repo ~/code/my-repo
# try the UI at no cost: stub agents and scripts
spike/inngest_driver/run_local.sh --stub --repo ~/code/scratch-repo
```

Open http://localhost:8790. Start a run there, review its files (select text to
comment), approve or send feedback at gates, answer questions. "View trace" opens
the Inngest dashboard on :8288. State and generated keys live in
`~/.orchestrator-studio`; logs are `~/.orchestrator-studio/*.log`. Needs uv and
Node 20+. On macOS, `brew install redis` keeps parked runs across restarts.

UI work: `cd studio && npm run dev` serves the UI with hot reload and proxies
`/api` to the running `studio_server` on 8790.

## Projects and their agent tools

A project is a folder (a git repo) that runs work in. Pass them with `--repo`, or
`--projects DIR` for every repo under a folder (on the box: `ORCH_STUDIO_REPOS`,
`ORCH_PROJECTS_ROOT`). The web app has no connectors of its own: a project's
agents get their tools from the project's `.mcp.json`, the file Claude Code
already reads, and the Codex worker gets the same servers as `-c mcp_servers.*`.

```json
{"mcpServers": {"linear": {"command": "npx", "args": ["-y", "<server package>"],
                           "env": {"LINEAR_API_KEY": "${LINEAR_API_KEY}"}}}}
```

`${VAR}` comes from `<project>/.orchestrator/mcp.env` (KEY=value, keep it out
of git), then from the worker's environment. A server with a missing variable is
left out of the run rather than started blank, and Studio's Projects view says
which variable it needs. Step agents never see the values: Claude gets the
resolved servers through a temporary `--mcp-config` file that its Read and Bash
are denied, plus `--allowedTools mcp__<name>` since a headless step can't ask.
Codex has no read sandbox and the `-c` values show in `ps`, so run its worker as
a user of its own.

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

### Real repo, per-step models (2026-10-21)

The workflows repo, ticket "make step scripts executable and add a test", models
from `models.yaml`. The run went triage (fix) → diagnose (hotfix) → hotfix-signoff
→ implement → code-review (pass, 9/10) → verify → human-review (asked, answered)
→ learn, stopping at pr-signoff. The result became ugudlado/workflows#4.

| Step                                   | Model     | Cost           | Time                       |
| -------------------------------------- | --------- | -------------- | -------------------------- |
| load-ticket-context, triage, ticket-\* | haiku-4-5 | $0.04 each     | about 15 s each            |
| diagnose                               | sonnet-5  | $0.84          | 219 s                      |
| implement                              | opus-5    | $2.15          | 205 s                      |
| code-review                            | fable-5-1 | $2.08          | 197 s                      |
| verify-changes                         | sonnet-5  | $0.66          | 145 s                      |
| human-review, learn, ux-critique       | sonnet-5  | $0.24 to $0.49 | 23 to 109 s                |
| **Total**                              |           | **$7.14**      | about 20 min of model time |

- **The tool allowlist was too narrow.** Agents called pytest by full path or as `python3`, so the `Bash(python -m pytest:*)` pattern never matched. Diagnose, implement and code-review all recorded that they couldn't run the tests, and code-review said so plainly. The worker needs an allowlist that matches how agents actually call the project's test command, or a sandbox.
- **The container restarted between runs** (Redis and the server were gone). On a VM they would be services.

### Sandbox instead of an allowlist (2026-10-21)

`claude_worker.py` now passes Claude Code's Bash sandbox settings (bubblewrap +
socat): any command runs without a prompt, writes are limited to the worktree,
the run's artifacts/results and temp, the network is off unless
`ORCH_SANDBOX_DOMAINS` lists hosts, and `failIfUnavailable` makes a step fail
rather than run unsandboxed. `--strict-mcp-config` keeps the machine's MCP
servers out of the step. Because ticket text can steer the agent, sandboxed
commands and Read can't open `~/.ssh`, `~/.aws`, `~/.config`, `~/.claude` and
similar, and the step's env is an allowlist (`ENV_KEEP`), so `INNGEST_*` and
other secrets stay out. Claude's own sign-in has to pass through, and a path
list is never complete, so on the VM run the worker as its own user with no
credentials in its home beyond claude's. Keep `ORCH_SANDBOX_DOMAINS` short: any
general-purpose host there (a paste site, a git host with write access) is an
exfiltration path.

Same ticket again on a fresh clone: the agents ran `pytest config/tests` (191 passed),
proved the new test fails on a 100644 script, and found pre-existing shell-test
failures. A write to `/home` from inside the sandbox was refused. But the first
implement failed because this machine signs commits through a local service the
sandbox can't reach; the run routed back to design (Fable, $4.31) and the second
implement committed unsigned. Total $14.57. On the VM, make the commit signer
reachable from the sandbox, or have agent commits skip signing on purpose.

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
