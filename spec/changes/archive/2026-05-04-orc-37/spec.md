---
feature-id: orc-37
linear-ticket: ORC-37
---

# Specification: Autopilot cost-report wiring — exit-code semantics + DB pre-init

## Motivation

At the end of `/autopilot ORC-36`, no cost summary was emitted to the operator
even though `scripts/cost-report.sh --change-id orc-36` ran successfully from
the CLI. Diagnosis identified two independent root causes (and disambiguated a
third that turned out to be a non-issue, plus one already shipped):

- **RC-1 (active)**: `orchestrator next` returns exit code 1 to signal
  `complete_workflow`, but `skills/orchestrate/SKILL.md` lines 135–145 do not
  document this. An agent using the Bash tool sees exit 1 and may treat the
  step as a failure, never reaching the `complete_workflow` branch and
  therefore never running `cost-report.sh`.
- **RC-2 (active)**: `install.sh` does not pre-create
  `~/.config/orchestrator/metrics.duckdb`. The DB is created lazily on the
  first `orchestrator next` call, which can hit "Operation not permitted" on
  macOS when the orchestrator runs from a sandboxed host (TCC) before the file
  exists.
- **RC-3 (resolved)**: SKILL.md line 144 already uses the repo-relative path
  `scripts/cost-report.sh` (resolved against the agent's CWD = repo root) and
  has since `d8b4d97`. The script-resolution path through
  `$ORCHESTRATOR_HOME/scripts/` is also now valid via the symlink shipped in
  commit `d048dc0` (AC#5). No further path-resolution change is needed.

Operators silently lose post-run cost visibility today. Cost data still
accumulates in DuckDB via step-event ingestion (when the DB is writable), but
the per-run summary never reaches the user.

## What Changes

1. SKILL.md dispatch loop is amended to document that `orchestrator next` exit
   code 1 is the `complete_workflow` signal — not a command failure. The agent
   must parse stdout JSON regardless of exit code in that branch.
2. SKILL.md is amended so that when `cost-report.sh` itself exits non-zero
   (script missing, DB unwritable, no events), the wrap-up surfaces a
   non-zero failure rather than silently skipping. This replaces the current
   "include the error message but do not block" prose. (See Decisions.)
3. `install.sh` gains a `setup_metrics_db()` function that creates
   `~/.config/orchestrator/metrics.duckdb` in user space and initializes its
   schema by invoking `orchestrator_next.upsert.ensure_schema`. The directory
   is created with the user's ownership (mkdir runs as the invoking user).
4. A regression test asserts the wrap-up surfaces a non-zero failure when
   `cost-report.sh` is missing or the DB is unwritable, and a small unit test
   asserts `install.sh` produces a writable, schema-initialized DB.
5. AC#5 (symlink already shipped in d048dc0) is verified, not re-implemented.

## Requirements

### Functional

1. **FR-1**: `skills/orchestrate/SKILL.md` documents that `orchestrator next`
   exit code 1 means `complete_workflow` (not error), and instructs the agent
   to parse stdout JSON regardless of exit code in the dispatch loop.
2. **FR-2**: When the dispatch loop reaches the `complete_workflow` branch and
   invokes `scripts/cost-report.sh`, a non-zero exit from the script causes
   the workflow's final reported status to be a failure (the agent's final
   message conveys non-zero), instead of the current silent-skip behavior.
3. **FR-3**: `install.sh` pre-creates `~/.config/orchestrator/metrics.duckdb`
   with the canonical schema before any orchestrator process runs, by
   invoking the existing `orchestrator_next.upsert.ensure_schema` function.
4. **FR-4**: `install.sh` ensures `~/.config/orchestrator/` exists with
   ownership matching the invoking user. (No setuid, no sudo within
   `install.sh`; `mkdir -p` under the user's HOME suffices on macOS/Linux.)
5. **FR-5**: A regression test exercises the wrap-up failure path: when
   `scripts/cost-report.sh` is missing or `$METRICS_DB` points at an
   unwritable / nonexistent DB, the test confirms a non-zero failure
   signal is produced (per FR-2).
6. **FR-6**: The symlink `$ORCHESTRATOR_HOME/scripts -> $REPO_ROOT/scripts`
   shipped in commit `d048dc0` is verified by an idempotent test that runs
   `install.sh` and asserts the symlink resolves to a real `cost-report.sh`.

### Non-Functional

1. **NFR-1**: `install.sh` remains idempotent — re-running it on a host that
   already has a populated `metrics.duckdb` must not corrupt or truncate it.
   `ensure_schema` uses `CREATE TABLE IF NOT EXISTS` and is safe to re-run.
2. **NFR-2**: No new runtime dependencies. The DB pre-init reuses the project's
   existing Python venv / system DuckDB import path used by `bin/orchestrator`.
3. **NFR-3**: SKILL.md prose changes are minimal — a comment on the dispatch
   loop call and a short paragraph in the `complete_workflow` branch. No
   structural refactor of the loop.

## Architecture

| File | Change |
|------|--------|
| `skills/orchestrate/SKILL.md` (lines ~135–145) | Add inline note that exit code 1 of `orchestrator next` is the `complete_workflow` signal; require agent to parse stdout JSON regardless of exit code. Replace "do not block" with "surface non-zero exit from cost-report.sh as the wrap-up's final status". |
| `install.sh` | Add `setup_metrics_db()` function; call from main flow after `setup_core()`. Function creates `$ORCHESTRATOR_HOME/metrics.duckdb` if absent and runs a one-line Python invocation of `orchestrator_next.upsert.ensure_schema`. |
| `tests/regression/test_orc37_wrap_up_exit.sh` (new) | Regression test for FR-2/FR-5: simulates the wrap-up step with cost-report.sh missing and with `$METRICS_DB` set to a nonexistent path, asserts the wrap-up's final exit signal is non-zero. |
| `tests/regression/test_orc37_install_metrics_db.sh` (new) | Regression test for FR-3/FR-4/FR-6: runs `install.sh` against a temp `$ORCHESTRATOR_HOME` and asserts (a) `metrics.duckdb` exists with the canonical schema, (b) the `scripts/` symlink resolves to `cost-report.sh`. |

## Test Strategy

### Test File Paths

- `tests/regression/test_orc37_wrap_up_exit.sh` — wrap-up failure surfaces
  non-zero (FR-2/FR-5).
- `tests/regression/test_orc37_install_metrics_db.sh` — install.sh DB pre-init
  + schema + symlink verification (FR-3/FR-4/FR-6).

### Coverage Targets

This change touches shell scripts and prose; coverage is asserted by behavior
tests rather than line coverage. Both regression tests must FAIL on `main` at
the parent commit of the fix and PASS after the fix.

### Key Test Scenarios

1. **Wrap-up with missing script** — temporarily rename
   `scripts/cost-report.sh`, drive a synthetic `complete_workflow` action
   through the wrap-up logic, assert non-zero final exit.
2. **Wrap-up with unwritable DB** — point `$METRICS_DB` at a directory that
   does not exist, assert `cost-report.sh` exits 1 and the wrap-up surfaces
   it as failure.
3. **Install.sh on clean home** — run against a temp `$ORCHESTRATOR_HOME`,
   assert `metrics.duckdb` exists with the `step_events` table.
4. **Install.sh idempotency** — run twice; assert second run does not
   corrupt or recreate the populated DB (NFR-1).
5. **Symlink verification** — assert `$ORCHESTRATOR_HOME/scripts/cost-report.sh`
   resolves to `$REPO_ROOT/scripts/cost-report.sh`.

## Acceptance Criteria

(Traceability convention: there is no `discovery.md` for this bugfix, so AC
items trace to root causes in `diagnose.md` and to the backlog ticket's AC
numbers, written as `[traces: RC-N | ticket-AC#N]`.)

- AC-1: Given an autopilot run that reaches workflow completion, when
  `orchestrator next` returns exit code 1 with `complete_workflow` JSON on
  stdout, then the dispatch loop in SKILL.md treats this as success and
  invokes `scripts/cost-report.sh`. [traces: RC-1 | ticket-AC#1, AC#3]
- AC-2: Given `scripts/cost-report.sh` exits non-zero (script missing, DB
  missing, DB unwritable, or no events), when the wrap-up step completes,
  then the workflow's final reported status reflects a non-zero failure
  (the agent's final message includes the error and is recognizable as a
  failure, not a silent skip). [traces: ticket-AC#4]
- AC-3: Given a fresh `$ORCHESTRATOR_HOME`, when `install.sh` runs, then
  `~/.config/orchestrator/metrics.duckdb` exists, is owned by the invoking
  user, and contains the canonical schema (verified by querying
  `step_events`). [traces: RC-2 | ticket-AC#2, AC#6]
- AC-4: Given `install.sh` is run twice in succession on the same host,
  when the second run completes, then any pre-existing `metrics.duckdb`
  data is preserved unchanged (idempotency). [traces: ticket-AC#6]
- AC-5: Given the regression tests, when run against the parent commit of
  the fix, both tests FAIL; when run against the fix HEAD, both tests
  PASS. [traces: ticket-AC#4]
- AC-6: Given the symlink commit `d048dc0`, when `install.sh` runs, then
  `$ORCHESTRATOR_HOME/scripts/cost-report.sh` resolves to a real file
  under `$REPO_ROOT/scripts/`. [traces: ticket-AC#5]

## Alternatives Considered

**Alternative A: Change `orchestrator next` to exit 0 for `complete_workflow`**.
Rejected. The exit-code convention (0 action / 1 complete / 2 blocked /
3 error) is referenced in `config/steps/contracts/step-dispatch.md` and
in tests of `orchestrator_next/dispatch.py`. Changing it ripples through
multiple consumers and contracts. The diagnosis explicitly defers this as a
separate design decision (Unresolved Question 2). Documenting the existing
convention in SKILL.md is the smaller, safer fix.

**Alternative B: Keep SKILL.md non-blocking on cost-report failure; add a
separate health-check step**. Rejected. Ticket AC#4 explicitly requires
non-zero on missing script or unwritable DB. A separate health-check step
adds workflow surface area without addressing the silent-skip symptom the
operator actually experiences.

**Alternative C: Reinvent the metrics schema in shell inside install.sh**.
Rejected. `orchestrator_next.upsert.ensure_schema` already exists and is
the source of truth used by `bin/orchestrator`. Duplicating it in shell
guarantees drift. A one-line `python3 -c 'from orchestrator_next.upsert
import ensure_schema; import duckdb; ensure_schema(duckdb.connect(...))'`
reuses the canonical implementation.

## Impact

- No breaking changes to public APIs or workflow contracts.
- Behavioral change for autopilot wrap-up: previously silent on
  `cost-report.sh` failure; now surfaces non-zero. Operators who were
  unknowingly missing reports will now see them or see the explicit failure.
- `install.sh` is run interactively by users; the new step adds <1s to install
  time and is idempotent.

## Decisions

- **D-1: Wrap-up fails loudly on cost-report.sh non-zero exit (Option A).**
  The current SKILL.md prose ("include the error message but do not block the
  workflow completion") directly conflicts with ticket AC#4. We choose
  ticket AC#4: silent skips were the original symptom; making failures loud
  is the correct fix. SKILL.md is amended accordingly.
- **D-2: install.sh reuses `orchestrator_next.upsert.ensure_schema`** for DB
  pre-init rather than reimplementing the schema in shell. This avoids
  schema drift and keeps `upsert.py` as the single source of truth.
- **D-3: AC#5 is verified, not re-implemented**, since commit `d048dc0`
  already ships the symlink. The verification is folded into the install.sh
  regression test.
- **D-4: AC#1's stated path-resolution issue does not exist as written** —
  SKILL.md line 144 has used the repo-relative `scripts/cost-report.sh`
  since `d8b4d97`. The user-visible symptom (no cost report) is fully
  explained by RC-1 (exit-code semantics) and RC-2 (DB write failure). We
  treat AC#1 as satisfied by the SKILL.md amendment that documents the
  existing repo-relative path and exit-code semantics.
