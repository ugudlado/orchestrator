<!--
BELONGS UPSTREAM IN THE WORKFLOWS PACK AS `DRIVER.md`.

This file documents what THIS PACK's scripts need from a driver. It is not
engine documentation — the engine (`orchestrator next`) knows none of it. It
lives here only because the pack is developed in another repo; copy it to the
pack root as DRIVER.md and delete this copy once it lands there.
-->

# Driving this pack

The `orchestrate` skill (ugudlado/skills, `workflow/orchestrate`) covers the protocol. This file covers what **these scripts**
need. Read it before driving a workflow from this pack.

## The run state file

Scripts read a YAML state file whose absolute path you pass as
`STATE_YAML_PATH`. Keep ONE per run at `<repo>/.orchestrator/<slug>/state.yaml`
— never copy it into the worktree.

```yaml
change_id: <slug> # check-rerun, load-ticket-context
slug: <slug>
schema: <workflow> # create-worktree (branch name: <schema>/<slug>)
status: active
repo_root: <abs repo path> # check-rerun (archive lookup)
user_input: | # load-ticket-context: brief text, or a ticket id
  <ticket text>
ticket_id: "" # set = ticket steps sync; "" = they no-op
workflow_plan: { main: { nodes: [] } } # check-rerun iterates it; must exist
tokens: [] # yours: gate approvals
step_history: [] # yours: [{step, status, attempt, route, tier, model}]
```

| key                       | read by                                                 |
| ------------------------- | ------------------------------------------------------- |
| `change_id` / `slug`      | check-rerun, load-ticket-context                        |
| `schema`                  | create-worktree (branch name)                           |
| `repo_root`               | check-rerun                                             |
| `user_input`              | load-ticket-context                                     |
| `ticket_id`               | ticket-sync (start/review/qa), ticket-done, check-rerun |
| `workflow_plan`           | check-rerun (crashes if the key is absent)              |
| `worktree_path`, `branch` | merged from `recorded.state_patch`; become env vars     |

## Environment

The engine sets `ORCHESTRATOR_STEP_ID`, `ORCHESTRATOR_STEP_DIR`,
`ORCHESTRATOR_CHANGE_ID`, `CHANGE_ID`, `ORCHESTRATOR_PROMPT_DIRS`,
`ORCHESTRATOR_PROMPT_PATH`, plus each contract's `params:`. **You supply the
rest.**

### Path roots — `REPO_ROOT` is never the worktree

`create-worktree` needs the original repo to create a worktree from. The
worktree is named by its own variables, and every artifact for a run must land
in it:

| var                                   | before create-worktree | after                     |
| ------------------------------------- | ---------------------- | ------------------------- |
| `REPO_ROOT`, `ORCHESTRATOR_REPO_ROOT` | repo                   | **repo** (unchanged)      |
| `ORCHESTRATOR_WORKFLOW_DIR`           | repo                   | worktree                  |
| `WORKTREE_PATH`, `WORKTREE_ROOT`      | unset                  | worktree                  |
| `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR`  | `<repo>/spec/changes`  | `<worktree>/spec/changes` |
| cwd for the CLI and for scripts       | repo                   | worktree                  |

### Everything else you set

| var                                               | needed by                               | notes                                                      |
| ------------------------------------------------- | --------------------------------------- | ---------------------------------------------------------- |
| `STATE_YAML_PATH`, `ORCHESTRATOR_STATE_YAML_PATH` | most scripts                            | absolute                                                   |
| `ORCHESTRATOR_ATTEMPT`                            | scripts that log it                     | your count                                                 |
| `ORCHESTRATOR_PYTHON`                             | workflow-report, persist-learnings      | an interpreter that can import `pyyaml`                    |
| `BRANCH`                                          | merge-to-main, remove-worktree, archive | from `state_patch`                                         |
| `ARCHIVE_PATH`                                    | archive-completed-change, ticket-done   | from mark-change-completed's `state_patch`                 |
| `WORKTREE_BASE_DIR`                               | create-worktree                         | where worktrees go; default `$HOME/code/feature_worktrees` |
| `BACKLOG_URL`, `BACKLOG_TOKEN`                    | ticket steps                            | **leave unset to run offline**                             |
| `CLAUDE_CODE_REMOTE`                              | create-worktree                         | `true` makes it a no-op                                    |

## Offline ticketing

With `BACKLOG_URL`/`BACKLOG_TOKEN` unset, `backlog_api_ticketing` returns
empty: every ticket step (`ticket-start`, `ticket-review`, `ticket-qa`,
`ticket-done`) prints a completed JSON line and exits 0, and
`load-ticket-context` writes `user_input` to `ticket-context.md` as the brief.
That is the supported offline path — no backlog is needed.

## Per-step quirks

**check-rerun** — requires `STATE_YAML_PATH` to point at an existing file and
`workflow_plan` to be present. If an archive dir matching the slug exists under
`<repo>/spec/changes/archive/`, it declares the run already complete.

**create-worktree / remove-worktree** — both shell out to a bare `python3` to
read state, so a `python3` **on PATH** must have `pyyaml` importable;
`ORCHESTRATOR_PYTHON` is not consulted here. create-worktree's branch is
`<schema>/<slug>`. Known pack bug: it reads
state with `IFS=$'\n' read -r a b <<< "$(...)"`, which captures only the first
line, so `schema` is always empty and the branch is always `feature/<slug>`
whatever the workflow. Harmless (nothing downstream parses the branch name),
but do not be surprised by it.

**persist-learnings** — looks for `proposed-scenarios.jsonl` beside
`STATE_YAML_PATH` or in `ORCHESTRATOR_WORKFLOW_DIR`, but `learn`'s contract
renders `payload.out.proposed_scenarios` into the artifacts dir. Write it where
`payload.out` says, then **copy it next to state.yaml** before running this
step. It **deletes the staging file** when it finishes, so re-create it before
any retry. `ORCHESTRATOR_PROMPT_PATH` is the _confinement_ root list: the script checks
each charter dir from `ORCHESTRATOR_PROMPT_DIRS` against it and silently drops
any row whose dir falls outside. The engine now emits the pack root (and
`<pack>/../skills` when that exists), so every `steps/<id>` charter is
allowed. It also has a dead branch calling
`orchestrator pack publish-scenarios` — a verb that no longer exists — guarded
by `command -v orchestrator`, so it no-ops unless an unrelated `orchestrator`
binary is on PATH with `ORCHESTRATOR_PACK` set. Leave `ORCHESTRATOR_PACK` unset.

**workflow-report** — in the upstream pack this imports the deleted
`orchestrator_next.report`. The scratch copy is patched to fold the driver's
own `step_history` from state.yaml instead. Upstream needs the same fix.

## Complete-phase steps (feature / complete workflows)

All five work with **no git remote** and never push. Verified by dry run.

| step                       | env it needs                                              | what it does                                                                                                                                                      |
| -------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mark-change-completed`    | `ORCHESTRATOR_STEP_DIR`, `STATE_YAML_PATH`                | stamps completion, emits `state_patch.archive_path`                                                                                                               |
| `archive-completed-change` | `REPO_ROOT`, `CHANGE_ID`, `WORKTREE_ROOT`, `ARCHIVE_PATH` | moves `spec/changes/<slug>/` → `spec/changes/archive/<slug>/`, commits in the worktree                                                                            |
| `merge-to-main`            | `REPO_ROOT`, `BRANCH`, `CHANGE_ID`, `STATE_YAML_PATH`     | detects the default branch (`origin/HEAD`, else local `main`/`master`), checks out, `git merge --no-ff`. **Local only — no push.** Aborts on conflict and exits 1 |
| `remove-worktree`          | `REPO_ROOT`, `BRANCH`, `WORKTREE_PATH`, `STATE_YAML_PATH` | `git worktree remove`; keeps the branch                                                                                                                           |
| `ticket-done`              | `REPO_ROOT`, `STATE_YAML_PATH`                            | ticket → Done; no-ops offline                                                                                                                                     |

`merge-to-main` runs from `REPO_ROOT`, not the worktree, and checks out the
default branch there. Run `remove-worktree` after it, not before.

## `human-review` (feature workflow)

A judgment step that pauses for a person. Its `decision` enum is
`approved | rework | await_input`, with `fail_on: [rework]`.

- **approved** → `--out '{"decision":"approved"}'` → the run advances.
- **rework** → `--out '{"decision":"rework","reset_to":"<step>"}'` → the engine
  derives failure from `fail_on` and routes to `reset_to` (`route: reset_to`).
  Legal targets on `feature`: `explore`, `ux-design`, `design`, `implement`.
  Without `reset_to` it falls back to the static `on_failure: implement`.
- **await_input** → the engine returns `needs_you` with an `await_input`
  payload. Relay it to the user (never self-answer), then re-run the step with
  their answer as User direction and report the resulting `approved`/`rework`.

`intake-research`'s `intake_status: await_input` gets the same handling.

## Upstream fixes this pack needs

Found while driving `patch` and `feature` end to end. None are engine bugs.

1. **`archive-completed-change` uses `mv`, not `git mv`** — the old tracked
   path (e.g. `spec/changes/<slug>/tasks.yaml`) survives the merge as a stale
   tracked file alongside the archived copy. Verified on a real run.
2. **`design-review/eval.sh` references a nonexistent `../architect/`** dir.
3. **Non-JSON on stdout.** `create-worktree` has its redirection backwards
   (`2>&1 >&2` sends stdout to the old stderr, then stderr to the terminal),
   and the archive script leaks git output. The engine parses the _last_ JSON
   line so runs survive it, but a stricter reader would break.
4. **`code-review/SKILL.md` still says "status MUST be failed"** citing the
   BKG-575 gate bypass. The engine derives failure from `fail_on` now, so both
   statuses are correct; the charter's warning is stale and misleads a worker.
5. **`persist-learnings` deletes the staging file** after every run, so a
   retry silently has nothing to persist unless the driver re-creates it.
6. **`implement/SKILL.md`** carries a dangling COMPLETION template, references
   `prompt.md` paths that no longer exist, and asks the worker for
   `tokens_in`/`tokens_out`/`duration_s` it cannot measure.
7. **`ticket-sync` no-op is indistinguishable from success** — offline it
   prints `{"ticket_status_set": "<status>"}` and exits 0 whether or not a
   ticket was touched, so a misconfigured backlog looks like a working one.
8. **`learn`'s charter says the staging file goes in the state dir**, but its
   contract renders `out.proposed_scenarios` into the artifacts dir, and
   `persist-learnings` reads the state dir. Three places, two answers.
