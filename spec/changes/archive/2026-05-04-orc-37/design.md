# Design: Autopilot cost-report wiring — exit-code semantics + DB pre-init

## Context

`/autopilot ORC-36` finished without emitting a cost summary. `diagnose.md`
identifies two active root causes:

- **RC-1**: `skills/orchestrate/SKILL.md` does not document that
  `orchestrator next` returns exit code 1 for `complete_workflow`. An agent
  treating non-zero exit as failure never reaches the cost-report branch.
- **RC-2**: `install.sh` does not pre-create `metrics.duckdb`, leaving the
  first write to be performed by a possibly sandboxed orchestrator process,
  where macOS TCC can return "Operation not permitted".

Two further root causes are non-blocking:

- **RC-3** (resolved): `scripts/` symlink shipped in `d048dc0`.
- **AC#1 (ticket)**: SKILL.md already uses the repo-relative path
  `scripts/cost-report.sh`. The ticket text describes a path bug that does
  not exist in current HEAD; the user-visible symptom is fully explained by
  RC-1 + RC-2. We satisfy AC#1 by amending SKILL.md to document the
  existing path + exit-code contract.

The fix lives in three localized changes — SKILL.md prose, `install.sh`,
and two new regression tests under `tests/regression/`.

## Goals / Non-Goals

### Goals

- Make `complete_workflow` reliably trigger `scripts/cost-report.sh` from
  the SKILL.md dispatch loop.
- Make wrap-up failures (missing script, unwritable DB, no events) loud
  rather than silent.
- Pre-create `metrics.duckdb` in user space during `install.sh` so first
  writes never originate from a sandboxed process on a missing file.
- Verify the AC#5 symlink fix shipped in `d048dc0` via the install
  regression test.

### Non-Goals

- Changing the exit-code convention of `orchestrator next` (separate design
  per `diagnose.md` Unresolved Question 2).
- Refactoring the SKILL.md dispatch loop structure.
- Reimplementing the metrics schema in shell or duplicating it in
  `install.sh`.
- Adding a separate health-check workflow step (rejected as Alternative B
  in spec.md).

## Approaches Considered

### Approach 1: Doc + reuse — minimal SKILL.md prose tweak + reuse `ensure_schema` from install.sh

Amend SKILL.md to clarify exit-code-1 semantics and to fail loud on
cost-report.sh non-zero. Add `setup_metrics_db()` to install.sh that
shells out to a one-line Python invocation calling
`orchestrator_next.upsert.ensure_schema`.

- **Pros**: smallest surface area; reuses canonical schema; no new
  dependencies; idempotent by construction (`CREATE TABLE IF NOT EXISTS`).
- **Cons**: SKILL.md is markdown prose — relies on the agent reading the
  amended note. Mitigation: regression test validates the actual behavior
  (FR-2/FR-5), not prose.
- **Complexity**: S.

### Approach 2: Restructure SKILL.md dispatch loop into a thin wrapper script

Replace the inline LOOP prose with `scripts/dispatch-loop.sh` that handles
exit-code semantics and cost-report invocation in shell, called by SKILL.md.

- **Pros**: testable end-to-end without prose interpretation; centralizes
  exit-code handling.
- **Cons**: large refactor of the autopilot entry point; out of scope for
  a bug fix; touches every workflow run; high blast radius. Diverges from
  the principle of minimal targeted fix.
- **Complexity**: L.

### Approach 3: Change `orchestrator next` to exit 0 for complete_workflow + new "no_more_work" signal

Eliminate RC-1 at its source by fixing the exit-code convention.

- **Pros**: removes the prose-interpretation hazard entirely; cleaner
  semantics.
- **Cons**: ripples through `config/steps/contracts/step-dispatch.md`,
  `dispatch.py` tests, and any external caller. `diagnose.md` explicitly
  defers this as a separate design.
- **Complexity**: M.

### Selected Approach

**Approach 1**. Auto-selection heuristic: complexity ranks
S(1) < M(2) < L(3); Approach 1 wins on lowest complexity. It also wins on
reuse (it reuses the existing `ensure_schema` and existing `cost-report.sh`
exit codes; the other approaches introduce new components or alter
contracts). Approach 1 is also alphabetically first by name (1 < 2 < 3).
All three tie-breakers favor Approach 1.

## High-Level Design

### Architecture Overview

```
install.sh
  └── setup_metrics_db()
        └── python3 -c "from orchestrator_next.upsert import ensure_schema; \
              import duckdb; ensure_schema(duckdb.connect('$ORCHESTRATOR_HOME/metrics.duckdb'))"

skills/orchestrate/SKILL.md (LOOP prose)
  └── action = orchestrator next ...   # exit 1 == complete_workflow, parse stdout regardless
       IF action.action == "complete_workflow":
           cost_report = run scripts/cost-report.sh ...
           IF cost-report exit != 0: surface non-zero failure in final message
           ELSE: include cost_report in final message

tests/regression/
  ├── test_orc37_wrap_up_exit.sh       # FR-2/FR-5
  └── test_orc37_install_metrics_db.sh  # FR-3/FR-4/FR-6
```

### Key Abstractions

- `setup_metrics_db()` — new shell function in `install.sh`. Idempotent
  pre-init of `$ORCHESTRATOR_HOME/metrics.duckdb`. Calls into Python to
  reuse `ensure_schema` from `orchestrator_next.upsert`.
- The SKILL.md dispatch-loop prose continues to be the contract; the
  amendment adds two prose elements: an inline note on the
  `orchestrator next` call, and a fail-loud rule in the
  `complete_workflow` branch.

## Low-Level Design

### Components

#### 1. `install.sh::setup_metrics_db()`

```bash
setup_metrics_db() {
  echo "Initializing metrics DB..."
  local db_path="$ORCHESTRATOR_HOME/metrics.duckdb"

  # Idempotent: ensure_schema uses CREATE TABLE IF NOT EXISTS, so
  # re-running on a populated DB is a no-op.
  PYTHONPATH="$ORCHESTRATOR_DIR/config/scripts" \
    python3 -c "
import duckdb
from orchestrator_next.upsert import ensure_schema
db = duckdb.connect('$db_path')
ensure_schema(db)
db.close()
" || {
    echo "  warning: failed to initialize metrics.duckdb at $db_path" >&2
    return 1
  }

  echo "  Metrics DB: $db_path (schema initialized)"
}
```

Called from the main flow after `setup_core()` (so the symlinks exist when
the Python import resolves).

#### 2. `skills/orchestrate/SKILL.md` amendment (lines ~135–145)

The amended prose makes two contractual additions:

- A line above `action = orchestrator next ...` stating that exit code 1
  is the `complete_workflow` signal, not a failure, and that the agent
  must parse stdout JSON regardless of exit code in this dispatch loop.
- In the `complete_workflow` branch: replace "include the error message
  but do not block the workflow completion" with: "if `cost-report.sh`
  exits non-zero, the wrap-up's final message must convey a non-zero
  failure (include script stderr verbatim) — do not silently skip."

#### 3. `tests/regression/test_orc37_wrap_up_exit.sh`

A bash test that:

1. Creates a temp `$REPO_ROOT_FAKE` with `scripts/cost-report.sh` removed
   and a synthetic `state.yaml` whose dispatch resolves to
   `complete_workflow`.
2. Drives the dispatch logic via a test harness that mirrors the SKILL.md
   prose (the test does not depend on the agent — it validates the
   behavior the agent is required to perform). The harness reads the
   amended SKILL.md and asserts a documented "fail-loud" sentinel is
   present, AND runs `cost-report.sh` directly with `$METRICS_DB` set to
   a nonexistent path to confirm it exits non-zero. The combination
   (prose + script behavior) is the regression contract for FR-2.
3. Asserts overall non-zero exit.

The test must FAIL on the parent commit and PASS on the fix commit
(SKILL.md amendment is the differentiator).

#### 4. `tests/regression/test_orc37_install_metrics_db.sh`

A bash test that:

1. Creates a temp `$ORCHESTRATOR_HOME` (e.g., under `mktemp -d`).
2. Runs `ORCHESTRATOR_HOME=$tmp ./install.sh`.
3. Asserts `$tmp/metrics.duckdb` exists and is non-empty.
4. Runs `duckdb $tmp/metrics.duckdb 'SELECT count(*) FROM step_events;'`
   and asserts it returns `0` (schema present, table empty).
5. Asserts `$tmp/scripts/cost-report.sh` resolves to
   `$REPO_ROOT/scripts/cost-report.sh` (FR-6, AC-6).
6. Runs `install.sh` a second time and asserts the DB file's mtime/size
   reflects no destructive recreation (idempotency / NFR-1).

Both tests live under `tests/regression/` and are wired into the existing
test entrypoint (whatever `make test` or the project's test runner uses;
the implementation task confirms the wiring).

### Data Flow

`/autopilot` → SKILL.md LOOP → `orchestrator next` (exit 1, JSON on stdout)
→ agent recognizes `complete_workflow` → invokes `scripts/cost-report.sh`
→ if non-zero, final message conveys failure; if zero, final message
includes the markdown report.

`install.sh` → `setup_metrics_db()` → Python reuses
`orchestrator_next.upsert.ensure_schema` → `metrics.duckdb` created with
`step_events` table → subsequent `bin/orchestrator` calls find a writable,
schema-populated file (no first-write under sandbox).

### State Management

No new state. `metrics.duckdb` is the same file `bin/orchestrator` already
manages; the change is who creates it first (install.sh in user space vs.
orchestrator runtime which may be sandboxed).

### Error Handling

- `setup_metrics_db()` failures (e.g., DuckDB import missing): print a
  warning to stderr and return 1, but do NOT abort the install — other
  install steps remain useful. The regression test
  (`test_orc37_install_metrics_db.sh`) will catch a regression if a future
  change breaks the success path.
- Wrap-up failures (cost-report.sh non-zero): per D-1, propagate as
  non-zero in the agent's final reported status with stderr verbatim.

## Constraints

- macOS-first (TCC sandbox is the originating environment); Linux must
  also work since `install.sh` is the canonical install path.
- Python 3 + DuckDB module already required by `bin/orchestrator`; no new
  install-time dependencies.
- SKILL.md is markdown prose interpreted by the agent — behavior cannot
  be 100% asserted in unit tests. Regression tests assert (a) the
  behavioral primitive (`cost-report.sh` exit codes) and (b) presence of
  the documented contract sentinel in SKILL.md.

## Trade-offs

- **Prose contract over code contract.** SKILL.md amendments rely on
  agent compliance. We accept this because restructuring the dispatch
  loop into a shell wrapper (Approach 2) is a much larger change for a
  bug fix. The regression test mitigates by failing if the documented
  sentinel disappears from SKILL.md.
- **Loud failure breaks "happy completion despite cost-report failure"**.
  An autopilot run that finished its real work but failed to emit a cost
  report will now report failure. We accept this — silent failures were
  the original bug, and operators want visibility into wrap-up issues.

## Decisions

- **D-1**: Wrap-up fails loud on `cost-report.sh` non-zero exit →
  resolves contradiction with the existing "do not block" SKILL.md prose
  → ticket AC#4 satisfied; some previously-passing runs will now report
  failure (a desirable, intentional regression in silence).
- **D-2**: `install.sh` reuses `orchestrator_next.upsert.ensure_schema`
  via inline `python3 -c` → schema stays single-sourced → install.sh
  acquires a soft dependency on the Python venv being usable, which is
  already true for any host that runs `bin/orchestrator`.
- **D-3**: AC#5 verified via test, not re-implemented → faster delivery,
  no risk of regressing the already-shipped fix.
- **D-4**: AC#1 satisfied by SKILL.md amendment documenting the existing
  repo-relative path → no path-resolution code change because none is
  needed.

## Open Questions

None blocking. Diagnose.md's Unresolved Question 2 (should
`orchestrator next` exit 0 for `complete_workflow`?) is explicitly
deferred per diagnose.md and Approach 3 above.
