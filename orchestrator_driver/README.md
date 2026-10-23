# orchestrator_driver

The self-hosted driver for the engine: one worker process and a Postgres database. It calls
`orchestrator next`, runs the step the engine names (an exec script, or a judgment step handed to
Claude Code or Codex), waits where the engine says a person must decide, and repeats until the
workflow is done. It works for any workflow in a pack, driven by config. No Inngest, no Redis,
no network port of its own. It needs Python 3.12+, pyyaml and psycopg, and a Postgres to point at.

The engine stays a pure function ([`../README.md`](../README.md)); everything stateful lives
here: run history, attempt counts, gate tokens, the process-group record, recovery.

## Install

```bash
uv tool install git+https://github.com/ugudlado/orchestrator.git   # `orchestrator` + `orchestrator-driver`
# or, from a checkout:
pip install -e .
```

The engine and the driver ship in one wheel, but the engine never imports the driver and the
driver's only extra dependency is psycopg. You also need a pack (workflows and steps), for example
[ugudlado/workflows](https://github.com/ugudlado/workflows), and for real work `git`, `claude`
(or `codex`) on the worker's PATH, and `bwrap` and `socat` for the Claude sandbox.

## Config

```bash
cp orchestrator_driver/driver.example.yaml driver.yaml     # commented; edit pack and projects
orchestrator-driver serve --config driver.yaml
```

Every subcommand takes `--config`. Without it: `$ORCH_DRIVER_CONFIG`, then
`~/.orchestrator/driver.yaml` if it exists, then defaults. Unknown keys are an error. Relative
paths in the file are relative to the file. `database_url` is a Postgres URL: for local dev any
server will do, e.g. `postgresql://localhost/orchestrator` (`createdb orchestrator`); to keep run history in one place
across machines, point every worker, CLI and Studio at the same hosted Postgres (Neon, ...). The driver creates its
tables on connect. `db:` from the SQLite days is an error. An environment variable overrides the file.

| key                   | default                                 | env                                      |
| --------------------- | --------------------------------------- | ---------------------------------------- |
| `database_url`        | none (required to open the database)    | `ORCH_DATABASE_URL`                      |
| `runs_root`           | `~/.orchestrator/runs`                  | `ORCH_RUNS_ROOT`                         |
| `pack`                | none (needed by `submit`, `workflows`)  | `ORCH_PACK`, then `ORCHESTRATOR_CONFIG`  |
| `projects`            | none                                    | `ORCH_PROJECTS` (comma-separated)        |
| `projects_root`       | none (every git repo directly under it) | `ORCH_PROJECTS_ROOT`                     |
| `agent`               | `claude` (`claude`, `codex`, `stub`)    | `ORCH_AGENT`                             |
| `agents`              | `{}` (step id to agent)                 | none                                     |
| `model_override`      | none (the pack's `models.yaml`)         | `ORCH_MODEL_OVERRIDE`                    |
| `sandbox_domains`     | `[]` (no network from Bash)             | `ORCH_SANDBOX_DOMAINS` (comma-separated) |
| `step_env_keep`       | `[]`                                    | `ORCH_STEP_ENV_KEEP` (comma-separated)   |
| `notify_cmd`          | none                                    | `ORCH_NOTIFY_CMD`                        |
| `gate_timeout_days`   | `7`                                     | `ORCH_GATE_TIMEOUT_DAYS`                 |
| `max_question_reruns` | `3`                                     | `ORCH_MAX_QUESTION_RERUNS`               |
| `poll_seconds`        | `0.5`                                   | `ORCH_POLL_SECONDS`                      |
| `stub_exec`           | `true` only if `agent` is `stub`        | `ORCH_STUB_EXEC` (`1` or `0`)            |

Other variables: `ORCH_DRIVER_CONFIG` (the config path), `ORCH` (how to call the engine, default
`<python> -m orchestrator_next`), and, for the claude and codex workers only, `ORCH_CLAUDE_ARGS`,
`ORCH_CODEX_ARGS`, `ORCH_CODEX_MODEL` (extra CLI arguments and the Codex model).

`agent: stub` does no work: judgment steps get placeholder outputs and exec scripts are stubbed
too, so a run only exercises the routing, the gates and the bookkeeping. With any other agent,
exec scripts run for real; `stub_exec: false` with `agent: stub` runs the pack's real scripts with
placeholder judgment steps (what the end-to-end test does). `agents:` overrides the agent per judgment
step; its keys must be step ids of the pack (`serve` checks at start).

## Run a workflow

```bash
orchestrator-driver workflows --config driver.yaml        # the pack's workflows with step and gate counts
orchestrator-driver submit --config driver.yaml --workflow feature --project app \
    --slug add-shout --ticket "add a --shout flag with a test"      # or --ticket-file brief.md
orchestrator-driver show add-shout --config driver.yaml   # status, what it waits for, last log lines
orchestrator-driver decide add-shout design-signoff 1 approved --config driver.yaml
```

## CLI

All output is JSON. The CLI writes to the DB only; the worker notices within one poll.

| command                                                                                     | what it does                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `serve`                                                                                     | the worker: one step at a time, oldest run first                                                                                                                                                                                                                                      |
| `workflows`                                                                                 | `[{name, steps, gates, file}]` for the pack                                                                                                                                                                                                                                           |
| `submit --workflow W --project P --slug S (--ticket T \| --ticket-file F) [--ticket-id ID]` | validates the workflow exists in the pack, the project is configured (a path or a folder name), the slug is new (`[A-Za-z0-9_-]`, up to 64); the artifacts dir is `<runs_root>/<slug>/artifacts`. `--stub-choices JSON` (`{"step": {"key": value}}`) sets what the stub agent returns |
| `decide SLUG GATE ATTEMPT approved\|rejected [--reset-to STEP] [--note TEXT]`               | answers a gate. Rejecting with `--reset-to` sends the run back to that step                                                                                                                                                                                                           |
| `answer SLUG STEP WAIT [--answer TEXT] [--reset-to STEP]`                                   | answers a question (`needs_you`); the step re-runs with the answer                                                                                                                                                                                                                    |
| `stop SLUG`                                                                                 | stops the run and kills its running step                                                                                                                                                                                                                                              |
| `show SLUG`, `list`                                                                         | status, `waiting`, current step and pid, outcome, last 10 log rows / every run                                                                                                                                                                                                        |

`ATTEMPT` and `WAIT` come from the run's `waiting` object (`show`, or `runs.waiting` in the DB).

## Run directory

```text
<runs_root>/<slug>/
  state.yaml    the run state file (JSON, which is valid YAML): STATE_YAML_PATH for every step
  artifacts/    the engine's --artifacts-dir: what steps read and write
  results/      per judgment step: <step>-<attempt>.json, the agent's result (the one file it writes)
  driver/       the driver's own files, mode 0700: <step>-<attempt>.payload.json and .stdout; never in an agent's reach
  base.sha      the project's HEAD when the first agent step started (see Security)
```

Every step process gets `STATE_YAML_PATH`, `REPO_ROOT`, `WORKTREE_PATH` (the worktree
`create-worktree` made, else the project), `ORCHESTRATOR_ATTEMPT`, `ORCHESTRATOR_CONFIG` and the
engine's own `payload.env`, and runs in its own process group.

## Run as a service

[`deploy/orchestrator-driver.service`](../deploy/orchestrator-driver.service) runs the worker as a dedicated
user `orch` with home `/home/orch`. One layout for the box: checkouts `/home/orch/orchestrator` (the driver runs
from its venv: `ExecStart=/home/orch/orchestrator/.venv/bin/orchestrator-driver serve --config
/etc/orchestrator/driver.yaml`), `/home/orch/orchestrator-studio` and `/home/orch/workflows` (the pack); the
database is any Postgres (`database_url:` in `driver.yaml`), runs go in `/home/orch/runs` (`runs_root:`).
The unit's header lists the install steps. It sets `ProtectSystem=strict`, `ProtectHome=read-only`,
`PrivateTmp=yes` and `ReadWritePaths=/home/orch /tmp`: add the path of any project outside them.
`NoNewPrivileges` is left off with a comment: Claude Code's sandbox runs bubblewrap, which on some hosts needs
a setuid helper. That, and the other settings under bubblewrap, are untested here (no bwrap on the build
machine): check one claude step starts before relying on them.

## Behaviour

- **State is committed after every `next` call and every step.** A crash resumes from the last
  commit. A step the row says was running is recovered at start: the process group in `runs.pid`
  is killed, but only after checking it is still ours (see Security), then the step re-runs
  with the same attempt number. There are no lock files.
- **Events are matched on (slug, kind, step, attempt or wait) and never dropped.** An event that
  arrives before its wait opens stays unconsumed until it does, so an approval sent early is used
  when the gate opens. An event for an earlier attempt or wait is consumed and logged
  `event_stale`. When a run finishes, its remaining events are consumed as stale.
- **Stop** kills the running step's process group within one 0.2 s poll, marks the run `stopped`
  and nothing re-runs, also after a restart. A run not in a step is stopped at the next poll.
- **SIGTERM** (a service stop) kills the child and leaves the step marked running for the next start.
- `next` calls and steps retry twice in process, 1 s apart; then the run fails with
  `outcome.status: error`. Failure outcomes (`refused`, `gate_timeout`, `question_timeout`,
  `question_limit`, `error`, `loop_guard`) set the run status `failed`, write `state.yaml` and send the finished
  notification. A question that times out or can't be re-run ends as `question_timeout`.
- **Question limit.** Each answer re-runs the step that asked. A step that asks again after
  `max_question_reruns` answered re-runs (default 3) ends the run as `question_limit`, with the step and its
  last question in the outcome. The count starts again when the routing reaches the step afresh (say after
  a gate sends the run back). `0` turns questions off: the first one ends the run. Without it, a step that can never succeed (say it can't push, and every answer is
  "go ahead") re-runs, and costs, until `loop_guard` at 200 loops.
- **An engine `error`** (say an agent forgot an output file, so `out` is invalid) is reported back once as
  that step `--status failed` with the reason, so its `on_failure` routing and retry limit apply; the run
  ends `outcome.status: error` only if the engine errors again on that report. The step's history entry is
  marked `failed` with `engine_error`.
- **Answers are per re-run.** `ORCHESTRATOR_USER_ANSWER` is set for the one re-run of the step that asked and
  dropped after it. Attempts are counted once per run of the step, the re-run included.
- **Bad events** (an `attempt` or `wait` that isn't an integer, a `data` that isn't an object) are consumed
  as `event_stale` and never crash the worker. Numeric strings count as numbers.
- `stop` also writes `state.yaml`, sends the finished notification (`status: stopped`) and consumes the
  run's leftover events. `notify_cmd` runs beside the loop (killed after 30 s), so a slow hook never delays a run.
- Initial state (`state.yaml`) is seeded per the pack's driver notes: `slug`, `change_id`, `schema` (the
  workflow), `repo_root`, `ticket_id`, `user_input` (the ticket), `workflow_plan`, `tokens`, `step_history`.
- A step that `requires` a token the run doesn't hold is refused (`outcome.status: refused`):
  the engine can't check tokens, so the driver does.
- Single worker: if it is down nothing advances. A crash in the driver code itself (not in a
  step) kills the worker and repeats on restart; there is no per-run isolation.
- `notify_cmd` gets the attention JSON on stdin before each wait
  (`{slug, workflow, artifacts_dir, kind: gate|question, step_id, attempt|wait, show|question, reason}`)
  and at finish (`kind: finished, status`). A chat bridge hooks in here and answers with `decide`/`answer`.

## The database

Postgres (psycopg 3, autocommit; the worker's one multi-statement transaction is a plain `BEGIN`): any other
process can read it alongside the worker. Studio or a chat bridge reads the tables and writes `events` rows; it does not touch `runs` or `log`.

```sql
CREATE TABLE IF NOT EXISTS runs (
  slug TEXT PRIMARY KEY, request TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|waiting|needs_you|done|failed|stopped
  loop TEXT NOT NULL DEFAULT '{}',        -- attempts, state, history, after, last_ready, last_summary, extra_env, ...
  waiting TEXT,                           -- {kind: gate|question, step_id, attempt|wait, show, question, reason, opened_at}
  current_step TEXT, pid INTEGER,         -- set while a step runs; still set at startup = it was interrupted
  created_at DOUBLE PRECISION, updated_at DOUBLE PRECISION, outcome TEXT,
  started_at DOUBLE PRECISION);           -- when the step process (pid, a process group leader) started
CREATE TABLE IF NOT EXISTS events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, slug TEXT NOT NULL,
  name TEXT NOT NULL,                     -- gate.decided|input.provided|stop
  data TEXT NOT NULL DEFAULT '{}', received_at DOUBLE PRECISION, consumed_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, slug TEXT NOT NULL, ts DOUBLE PRECISION, kind TEXT NOT NULL, data TEXT);
CREATE INDEX IF NOT EXISTS log_slug ON log (slug, id);
CREATE INDEX IF NOT EXISTS events_open ON events (slug) WHERE consumed_at IS NULL;
```

- `runs.pid` / `runs.started_at`: the running step's process group id (it leads its own group) and when it
  started; set while a step runs, null otherwise, written once per process.
- `runs.request`: `{workflow, pack, slug, repo_root, artifacts_dir, ticket, ticket_id, stub_choices}`.
- `runs.loop`: `{n, attempts, after, ans, phase, state, last_ready, last_summary, extra_env}`;
  `loop.state.step_history` is the history (`step`, `attempt`, `route`, `kind`, `status`, `exit_code`,
  `model`, `cost_usd`, and `gate`/`note` for gate decisions).
- `runs.waiting`: `{kind: gate, step_id, attempt, show, opened_at}` or
  `{kind: question, step_id, wait, question, reason, opened_at}`; null unless status is `waiting` or `needs_you`.
- `runs.outcome`: `{status: done|stopped|error|refused|gate_timeout|question_timeout|question_limit|loop_guard, history, ...}`.
- `events` to write (`data` is JSON):
  - `gate.decided`: `{gate, attempt, decision: approved|rejected, reset_to, note}`
  - `input.provided`: `{step_id, wait, answer, reset_to}`
  - `stop`: `{}`
- `log.kind`: `submitted`, `next`, `step_start`, `step_end` (model, cost_usd, duration_s), `step_error`,
  `retry`, `engine_error`, `wait_open`, `wait_close`, `event_stale`, `recovered`, `stopped`, `notify_error`, `finished`.
- Times are Unix seconds. Run list: `runs`; waiting cards: `runs.waiting`; timeline: `log`.

## Security

- **Sandbox, not an allowlist.** The claude worker runs Claude Code with its Bash sandbox
  (bubblewrap + socat): any command runs without a prompt, writes are limited to the worktree,
  the run's artifacts and results, and temp, and the network reaches only `sandbox_domains`.
  It fails closed: with no sandbox available the step errors instead of running bare. Ticket text can
  steer the agent, so sandboxed commands and Read can't open `~/.ssh`, `~/.aws`, `~/.config`, `~/.claude`
  and similar. A path list is never complete: **run the worker as its own user** (the unit file does) with
  no credentials in its home beyond the agent's sign-in. Keep `sandbox_domains` short; a paste site or a git
  host with write access is an exfiltration path. The Codex worker uses Codex's own workspace-write
  sandbox with the network off, which does not hide files from reads: the same advice, harder.
- **The driver's files are out of the agent's reach.** The agent can write only `artifacts/` and `results/`
  (sandbox `allowWrite` and `--add-dir`); payloads and stdout live in `driver/` (0700), which is in neither.
  Every driver write opens with `O_NOFOLLOW` (a planted symlink fails the write instead of redirecting it),
  and the agent's result file is read with `O_NOFOLLOW`, as a regular file, at most 1 MB. The process group
  to kill lives only in the DB, not in a file an agent could edit. Before `killpg` (recovery, stop) the driver
  checks `pid > 1`, not its own group, that the process leads its own group (`/proc/<pid>/stat`), and that
  its environment (`/proc/<pid>/environ`) carries `STATE_YAML_PATH=<this run's state.yaml>`; otherwise it
  signals nothing. A leader that already exited cannot be verified, so its leftover children are not killed.
- **Git hooks and config are not the agent's to write.** Exec scripts and the worker's own `git` calls run
  outside the sandbox, so a hook or `core.hooksPath`/`core.fsmonitor` planted in `.git` would run unsandboxed.
  The claude worker denies writes (sandbox `denyWrite` and the Edit tool) to `config`, `config.worktree` and
  `hooks/` of the worktree's and the repo's git dirs (from `git rev-parse --git-dir --git-common-dir`, given and
  real paths); the rest of `.git` stays writable so agents can commit. It also denies the files that choose
  the git dir: the worktree's `.git` pointer, `commondir`, `gitdir` and `info/`, so git can't be repointed at
  a git dir the agent made. Exec scripts run with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and
  overrides for `core.hooksPath`, `core.fsmonitor`, `core.pager`, `core.sshCommand`, `diff.external` (empty:
  a plain `git diff` errors, use `--no-ext-diff`), `core.editor`, `credential.helper` (cleared: exec steps run
  no network git, the pack pushes from the sandboxed `open-pr` agent step) and `protocol.ext.allow=never`; your
  `user.name`/`user.email` are passed as author/committer variables. Before each exec step the driver checks
  that the worktree's `.git` pointer, its `commondir` and the back-pointer resolve (real paths) into the
  project's `.git`, and fails the step otherwise. The Codex worker has no such
  deny list (Codex's own sandbox decides what it protects in `.git`): treat a Codex step as able to plant hooks.
- **Env allowlist.** The agent process gets only PATH/HOME-style basics, `ORCHESTRATOR_*`, the run's
  paths, the agent's sign-in and proxy/CA settings. Names that look secret (`TOKEN`, `SECRET`, `PASSWORD`,
  `KEY`) are dropped even inside a kept prefix, except the agent's sign-in. `step_env_keep` adds names on
  purpose; the agent can see them. Exec scripts are the pack's code and get the worker's whole environment.
- **Project MCP servers are trusted only as committed.** Claude Code loads the worktree's `.mcp.json`
  without asking, and the worker pre-allows those servers' tools. MCP servers and project settings (hooks)
  run outside the sandbox, so the step only gets them if `.mcp.json` and `.claude/settings*.json` match the
  `base.sha` pinned at the run's first agent step; otherwise it runs with user settings only and no project
  servers. Codex's `.codex/config.toml` must match too, or the step stops. Prefer OAuth servers and sign
  in once as the worker's user (`/mcp`, `codex mcp login`); a key-based server gets its key through
  `${VAR}` and `step_env_keep`. Keep user-level MCP servers empty.
- **Projects are an allowlist.** `submit` rejects any repo the config doesn't list. Anyone who can write to
  the database can start a run, so give the role to the worker's user and Studio only, and keep `database_url`
  (it holds the password) out of world-readable files.
- Commit signing: if the machine signs commits through a service the sandbox can't reach (an SSH agent, a
  signing helper), an agent's commit fails. Make the signer reachable from the sandbox, or turn signing off
  for that project only: `git -C <project> config commit.gpgsign false` (worktrees share it). With the pack's
  current `implement` the step then asks instead of failing (`outcome: await_input`).
- Worktree setup: judgment steps have no network, so a fresh worktree can't install node_modules, a venv or
  sibling checkouts. Commit a `.orchestrator/setup.sh` in the project; the pack's `create-worktree` runs it
  in each new worktree, outside the sandbox (see the pack's `DRIVER.md`).
- `CLAUDE_CODE_REMOTE` is cleared for exec scripts only (it makes `create-worktree` a no-op) and left alone
  for Claude Code, which needs it to sign in in a cloud session.

## Layout

```text
orchestrator_driver/
  config.py    the config file, env overrides, projects, the pack's workflows
  gitenv.py    git settings for processes outside the sandbox (no user config, hooks, fsmonitor, pager, ...; link check)
  db.py        the Postgres store
  driver.py    the worker loop: next, steps, waits, recovery, stop
  steps.py     engine call, exec and judgment runners, the verified process-group kill
  cli.py       the orchestrator-driver command
  workers/     claude.py, codex.py, stub.py: one process per judgment step
```

## Tests

```bash
uv run --with pytest python -m pytest tests/driver -q
```

Needs the pack: `ORCH_TEST_PACK` (a pack's config dir), else `/home/user/workflows/config`; the
tests skip with a reason if it isn't there. They use the stub agent and a throwaway git repo.
