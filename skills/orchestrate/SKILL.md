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

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
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
  RETURN $ORCHESTRATOR_HOME/config/<relative_path>
```

Repo overrides **fully replace** the global file (no YAML merge). The error
recovery and override resolution protocols are universal and NOT subject to
override — always read from `$ORCHESTRATOR_HOME/config/`.

When reading any path written below as `$ORCHESTRATOR_HOME/config/<...>`,
apply `RESOLVE_WORKFLOW_FILE(<...>)` unless it is a universal invariant
contract listed above.

## Execution

### 1. Select workflow

The schema is chosen by the subcommand, not inferred from prose. The entry points are:

- `orchestrator feature <id>` → schema `feature`
- `orchestrator bugfix <id>` → schema `bugfix`
- `orchestrator autopilot <id>` → schema `autopilot`
- `orchestrator complete <id>` → complete phase only (`config/workflows/complete.yaml`); same driver as other workflows (`orchestrator-run.sh --schema complete`), merge + teardown after archive

`feature`, `bugfix`, and `autopilot` are `orchestrator run <id> --schema <name>` under the hood. `complete` uses the same workflow-file discovery but a different driver (no seed; requires existing state).
There is no prose intent-inference step (ORC-108 removed select-workflow + the
flag registry).

Then:

1. Read the schema YAML: `$ORCHESTRATOR_HOME/config/workflows/<schema>.yaml`. Workflow
   files declare `steps:` (and rarely a `defaults:` override block). The `steps:` list
   IS the plan — there is no flag-gating.
2. Any `key=value` arguments passed on the command line are persisted verbatim to
   `state.flags` for schema-specific behavioral reads. There is no global flag registry.
3. Tell the user the schema and the resolved feature id.

### 2. Resume entry point

If an active state.yaml already exists for this id (the ticket is mid-flight), resume it: read its `next_step` (phase + step_id) and persisted `flags`, and enter the dispatch loop at that point. Tell the user: "Resuming <change_id> at <phase>/<step_id>." (orchestrator-run.sh already performs this resume detection — state.yaml presence drives init vs resume — when driving from the CLI.)

Otherwise this is a new workflow — proceed to sub-step 2.1 to initialize state before entering the dispatch loop. This applies equally to full workflow runs and phase-constrained wrapper calls such as `/specify` (`--phase specify`); artifact-producing steps must never run before init has created the worktree/artifact directory.

#### 2.1 Initialize new workflows

Call the init script:

```
bash skills/orchestrate/scripts/seed-state.sh <slug> <schema> [flag=value ...]
```

Arguments:
- `<slug>` is the change_id / feature slug for this workflow (derived from the request or Linear ticket).
- `<schema>` is the schema name from the subcommand (e.g. `bugfix`, `feature`, `autopilot`).
- `[flag=value ...]` are any resolved CLI flag overrides (e.g. `tdd_required=false`).

After the script exits 0, assert that state.yaml exists with a promoted
workflow plan before proceeding:
- `$WORKFLOW_STATE_DIR/<slug>/state.yaml` exists, and its
  `workflow_plan.main.nodes` is a non-empty list.

`generate_plan` promotes the seeded workflow plan into the `nodes` shape in
place inside state.yaml — there is no separate plan file (ORC-63).

If state.yaml is absent or its workflow plan is unpromoted, the seeder printed an error to stderr — surface it to the user and halt. Do NOT proceed to the dispatch loop with a missing state.yaml (that is the exact bug this step was added to prevent).

The script is the executable init contract. Do not duplicate its workflow-plan, worktree, artifact-dir, or state-stamping logic in this prompt or in wrapper skills. It is idempotent: re-running it when state.yaml already exists exits 0 without overwriting.

### 3. Run workflow via shell driver

After init (§2.1) or when resuming with existing state, shell out to the CLI and
let `orchestrator-run.sh` + `run-workflow.sh` drive every step to completion.
No in-chat dispatch loop — the shell driver spawns agent subprocesses, records
steps, and handles retries.

```
orchestrator run $CHANGE_ID --schema $SCHEMA [flag=value ...] [--repo $REPO_ROOT]
```

- `$CHANGE_ID` — resolved slug from `$request` (e.g. `orc-112`, `HL-287`).
- `$SCHEMA` — from §1 (`feature`, `bugfix`, etc.; use `complete` for merge/teardown only).
- Pass any `key=value` overrides from the invocation verbatim (e.g. `tdd_required=false`).
- `orchestrator-run.sh` performs resume detection, seeds when state is absent (idempotent
  with §2.1), and execs `run-workflow.sh` until the workflow exits.

Exit codes match `run-workflow.sh` (1=complete, 2=blocked, 3–7=errors). Surface
stderr to the user on failure. On success (exit 1), read `step_history` for
`cost-report` outputs (`tail_summary`, `cost_summary_path`) and include
`cost-summary.md` in the final message when present.

Wrapper skills (`/specify`, `/implement`) invoke this skill with extra arguments;
forward those arguments unchanged on the `orchestrator run` line so they land in
`state.flags` (same as CLI `flag=value` passthrough today).

## What This Skill Does NOT Do

- No in-chat `orchestrator next` / `orchestrator done` loop and no Task-tool agent spawns.
- Does not duplicate dispatch, retry, or usage recording — `run-workflow.sh` owns that.
- Does not merge — use `orchestrator complete <id>` (`/complete-feature`) after the workflow archives.

## Failure modes

- **Missing or unpromoted state after seed** — halt at §2.1; do not shell out.
- **Workflow blocked (exit 2)** — read `state.yaml` `step_history[-1]` and surface escalation or fix the blocker.
- **Workflow error (exit 3–7)** — surface stderr; no in-skill retry loop.
