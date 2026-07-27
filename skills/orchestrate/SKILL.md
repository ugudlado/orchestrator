---
name: orchestrate
description: "Workflow router — detects intent and loads the right schema. This skill should be used when the user says 'orchestrate', 'start a feature', 'fix a bug', or describes development work that maps to a workflow type (feature, bugfix, autopilot)."
user-invocable: true
args:
  - name: request
    description: >
      What to work on — a description, Linear ticket ID (e.g. HL-170), or a feature ID to resume.
      All flags are passed through as-is to the resolved schema.
    required: false
---

## 0. Remote-session check (do this first)

If `$CLAUDE_CODE_REMOTE == "true"` (cloud/Slack session), **stop reading this skill** and
follow [`DRIVE.md`](../../DRIVE.md) instead. That is a different execution model: no
`orchestrator run` subprocess spawn, no per-step model routing — you drive `next`/`done`
yourself and execute every step with your own model. Everything below (§§1-4) assumes a
local session where the CLI self-drives.

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
# Config root is explicit — no cwd fallback (see paths.config_root).
ORCHESTRATOR_CONFIG=${ORCHESTRATOR_CONFIG:-${ORCHESTRATOR_HOME:+$ORCHESTRATOR_HOME/config}}
ORCHESTRATOR_CONFIG=${ORCHESTRATOR_CONFIG:-$(orchestrator config-path)}
REPO_WORKFLOW_DIR=${REPO_WORKFLOW_DIR:-$REPO_ROOT/.orchestrator}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/.orchestrator}
WORKTREE_ARTIFACT_DIR="${WORKTREE_ARTIFACT_DIR:-${WORKTREE_ROOT:-$REPO_ROOT}/spec/changes}"
```

## Workflow file resolution

Every read of a workflow file (schema, step contract, template, included
phase) MUST use this resolver:

```
RESOLVE_WORKFLOW_FILE(relative_path):
  repo_override = $REPO_WORKFLOW_DIR/<relative_path>
  IF exists(repo_override):
    RETURN repo_override
  RETURN $ORCHESTRATOR_CONFIG/<relative_path>
```

Repo overrides **fully replace** the global file (no YAML merge). The error
recovery and override resolution protocols are universal and NOT subject to
override — always read from `$ORCHESTRATOR_CONFIG/`.

When reading any path written below as `$ORCHESTRATOR_CONFIG/<...>`,
apply `RESOLVE_WORKFLOW_FILE(<...>)` unless it is a universal invariant
contract listed above.

## Execution

### 1. Select workflow

The schema is chosen by the subcommand, not inferred from prose. The entry points are:

- `orchestrator feature <id>` → schema `feature`
- `orchestrator bugfix <id>` → schema `bugfix`
- `orchestrator autopilot <id>` → schema `autopilot`
- `orchestrator design <id>` → schema `design` (explore → design → review → learn; stops before implement)
- `orchestrator patch <id>` → schema `patch` (implement → phase-review → learn; no design phase, small-scoped changes)
- `orchestrator implement <id>` → schema `implement` (implement → phase-review → learn; design artifacts already exist, ticket is ready to build)
- `orchestrator complete <id>` → complete phase only (`config/workflows/complete.yaml`); same CLI (`orchestrator complete <id>`), merge + teardown after archive

`feature`, `bugfix`, and `autopilot` are `orchestrator run <id> --schema <name>` under the hood. `complete` uses the same CLI and in-process loop, but **resolves existing (or archived) state instead of seeding** — it requires an existing feature workflow to complete.
There is no prose intent-inference step (ORC-108 removed select-workflow + the
flag registry).

Then:

1. Read the schema YAML: `$ORCHESTRATOR_CONFIG/workflows/<schema>.yaml`. Workflow
   files declare `steps:` (and rarely a `defaults:` override block). The `steps:` list
   IS the plan — there is no flag-gating.
2. Any `key=value` arguments passed on the command line are persisted verbatim to
   `state.flags` for schema-specific behavioral reads. There is no global flag registry.
3. Tell the user the schema and the resolved feature id.

### 2. Resume entry point

If an active state.yaml already exists for this id (the ticket is mid-flight), resume it: read its `next_step` (phase + step_id) and persisted `flags`, and enter the dispatch loop at that point. Tell the user: "Resuming <change_id> at <phase>/<step_id>." (`orchestrator run` already performs this resume detection — state.yaml presence drives init vs resume.)

Otherwise this is a new workflow. Shell out to the CLI — `orchestrator run` seeds state automatically when none exists (idempotent), then drives every step to completion. No manual init step needed.

### 3. Run workflow via the CLI

Shell out to `orchestrator run` and let it drive every step to completion. The dispatch
loop runs **in-process inside the CLI** (`orchestrator_next/run_loop.py`) — it seeds state
when needed, spawns agent subprocesses, records steps, and handles retries. No in-chat
dispatch loop.

```
orchestrator run $CHANGE_ID --schema $SCHEMA [flag=value ...] [--repo $REPO_ROOT]
```

- `$CHANGE_ID` — resolved slug from `$request` (e.g. `orc-112`, `HL-287`).
- `$SCHEMA` — from §1 (`feature`, `bugfix`, etc.; use `complete` for merge/teardown only).
- Pass any `key=value` overrides from the invocation verbatim (e.g. `tdd_required=false`).
- `orchestrator run` performs resume detection, seeds when state is absent (idempotent
  with §2.1), and drives the in-process loop until the workflow exits.

Exit codes: 1=complete, 2=blocked, 3–7=errors. Surface
stderr to the user on failure. After each step completes, the loop emits a
running `[cost so far: $X.XX]` line on stderr (re-derived by summing
`step_history[].usage.cost_usd` from live state) — relay it so the user sees the
mid-run total. On success (exit 1), read `step_history` for `cost-report`
outputs (`tail_summary`, `cost_summary_path`) and include `cost-summary.md` in
the final message when present.

Wrapper skills (`/specify`, `/run-implement`) invoke this skill with extra arguments;
forward those arguments unchanged on the `orchestrator run` line so they land in
`state.flags` (same as CLI `flag=value` passthrough today).

## What This Skill Does NOT Do

- No in-chat `orchestrator next` / `orchestrator done` loop and no Task-tool agent spawns.
- Does not duplicate dispatch, retry, or usage recording — the in-process loop (`run_loop.py`) owns that.
- Does not merge — use `orchestrator complete <id>` (`/complete-feature`) after the workflow archives.

## Failure modes

- **Missing or unpromoted state after seed** — halt at §2.1; do not shell out.
- **Workflow blocked (exit 2)** — read `state.yaml` `step_history[-1]` and surface escalation or fix the blocker.
- **Workflow error (exit 3–7)** — surface stderr; no in-skill retry loop.
