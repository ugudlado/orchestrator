# Design: Absorb ingest-feature-metrics into `done` (Phase 5)

## Context

Phase 4 established `done` as the single CLI verb for metrics writes and absorbed `ingest-driver-auto` and `ingest-subagents-auto` into the `record()` boundary path. The last hold-out is `scripts/inline/ingest-feature-metrics.py`: a 440-line standalone script that runs as its own `_complete-phase.yaml` step between `mark-change-completed` and `compute-swe-metrics`. The script reads `tasks.md`, `state.yaml`, and `git log`, then writes one row to `feature_metrics`. Its 6 computation functions (`parse_tasks`, `compute_retries`, `compute_resolution`, `run_git_churn`, `extract_review_scores`, `wall_clock_minutes`) are pure data transforms with no caller-side dependencies. The DuckDB write target `upsert_feature_metrics(db, repo_root, change_id, **fields)` already exists in `upsert.py:393` and is the correct interface.

Two constraints shape the design:

1. **Trigger point ≠ feature boundary.** Phase 4 fires its boundary path at `remove-worktree` (the workflow's last step). But `compute-swe-metrics` (position 5 in `_complete-phase.yaml`) reads `feature_metrics` via a LEFT JOIN in the `feature_report` view (`migrations/0002_report_views.sql:216`). If we fire at `remove-worktree` (position 7), the join sees NULL — semantically wrong. The `feature_metrics` row must exist by the time `compute-swe-metrics` runs, so the trigger fires at `mark-change-completed` (position 3). `mark-change-completed` already requires `state.completed_at` and `state.started_at` to be set, which `_resolve_feature_metrics` needs for `wall_clock_minutes`.

2. **Self-bootstrapping.** The workflow running this feature uses `_complete-phase.yaml` to complete itself. If we delete the inline `ingest-feature-metrics` step in the same commit that lands the absorbed path, the cycle that runs Phase 5's own complete phase loses its `feature_metrics` row if anything goes wrong with the new path. Phase 4 solved this with a "delete last" pattern: the absorbed code lands first, the deletion lands as the final task. We mirror that pattern.

DuckDB schema is unchanged in Phase 5: `DESCRIBE feature_metrics` against the live DB confirms all 25 columns (including `computed_at`) already exist. No migration is authored. The 24 non-audit columns are the parity-test target; `source` is the one column that legitimately differs (legacy script writes `"ingest-feature-metrics@<utcnow>"`, absorbed path writes `"done@<utcnow>"`).

## Goals / Non-Goals

### Goals

- One verb (`done`) writes the `feature_metrics` row at `mark-change-completed` completion, atomically with the `step_events` upsert for that step.
- Six computation functions move from inline script into `record.py` with zero signature changes.
- Helper layout mirrors Phase 4's `_resolve_*` (pure parsing, outside transaction) / `_write_*` (DB calls, inside transaction) split.
- Parity-test the absorbed path against the legacy script using a real archived fixture (cycle-20 rule).
- Bootstrap-safe two-stage rollout: Stage A (additive) keeps the inline script alive; Stage B (deletion) lands last.

### Non-Goals

- Renaming `record.py` → `done.py` (deferred from Phase 4; same posture).
- Changing the `feature_metrics` schema or column set.
- Touching `mark-change-completed` step contract, `compute-swe-metrics`, or any other complete-phase step.
- Backfilling historical `feature_metrics` rows (INSERT OR REPLACE keyed on `(repo_root, change_id)` is idempotent going forward).
- Per-subagent attribution writes (Phase 4 owned that surface).
- Renaming the `done` verb or changing status dispatch (Phase 4 owned that).

## Approaches Considered

### Approach A: Mirror Phase 4 helpers (S complexity, recommended)

Add `_resolve_feature_metrics(state, change_id)` (pure compute, runs outside the transaction) and `_write_feature_metrics(db, repo_root, change_id, data)` (calls `upsert_feature_metrics`, runs inside the transaction). Move the 6 computation functions from `ingest-feature-metrics.py` into `record.py` verbatim. Special-case `step_id == "mark-change-completed"` inside the existing `record()` boundary block: when matched, run resolve outside `BEGIN`, write `step_events` + `feature_metrics` inside one BEGIN/COMMIT, ROLLBACK fatal on error. Stage A leaves the inline script + step entry alive; Stage B deletes them.

Pros: Pattern-match with Phase 4 lowers cognitive load. Helper split keeps the transaction window short (resolve does git-log + tasks.md parse outside BEGIN). Six computation functions move with no signature change so existing logic is preserved exactly. Two-stage rollout neutralizes the bootstrap hazard.
Cons: `record.py` grows by ~250 lines (six computation helpers). Acceptable — the file already houses analogous helpers (`_resolve_driver_session`, `_resolve_subagent_rows`, `_compute_cost_usd`).

Module reuse: 2 (extends `record.py` and reuses `upsert_feature_metrics`).
Complexity: **S** (numeric 2).

### Approach B: Extend `_resolve_driver_session` to also resolve feature metrics

Expand the existing Phase 4 feature-boundary helper to additionally produce the `feature_metrics` dict, and write the row inside the existing feature-boundary BEGIN/COMMIT at `remove-worktree`.

Pros: Reuses one helper and one transaction window. No new trigger logic.
Cons: Wrong trigger point — fires at `remove-worktree` (position 7), but `compute-swe-metrics` (position 5) reads the row via LEFT JOIN. The view returns NULL columns for the feature, which `compute-swe-metrics` then propagates downstream. This is exactly the bug the discovery brief's "Critical Constraint" section flags. To make it work, `compute-swe-metrics` would have to be reordered after `remove-worktree`, but `compute-swe-metrics` runs `git churn` against the worktree that `remove-worktree` deletes. Rejected on correctness grounds.

Module reuse: 2.
Complexity: **M** (numeric 3) once you account for the cascade required to fix the join timing.

### Approach C: Keep `ingest-feature-metrics.py` as a wrapper that calls the new helpers

Move the 6 computation functions and `_resolve_feature_metrics` / `_write_feature_metrics` into `record.py` (same as Approach A), but instead of deleting the inline script, rewrite it as a 20-line shim that imports and calls the new helpers. Keep the `_complete-phase.yaml` step.

Pros: Smallest immediate behavior change — the standalone CLI invocation surface stays alive.
Cons: Defeats the point of Phase 5 (removing the standalone script as a step). Leaves dead surface area for drivers to think about and contradicts the parent backlog goal of "absorb all metrics writes into `done`". The wrapper also keeps `bin/orchestrator`'s dispatch table polluted with a verb that just calls the absorbed code.

Module reuse: 2.
Complexity: **S** (numeric 2).

### Selected Approach

**Approach A: Mirror Phase 4 helpers.**

Auto-selection heuristic application:

| Approach | Complexity (numeric) | Module reuse | Goal-aligned? |
|----------|----------------------|--------------|---------------|
| A        | 2 (S)                | 2            | YES |
| C        | 2 (S)                | 2            | NO (keeps the standalone step alive) |
| B        | 3 (M)                | 2            | NO (forces wrong trigger point) |

Lowest numeric complexity goes to Approaches A and C tied at S=2. Module-reuse counts are equal. Approach C is goal-disqualified (it does not satisfy FR-7 — deleting the inline script is the whole point of Phase 5). Approach A wins. Approach B is correctness-disqualified (wrong trigger timing).

## High-Level Design

### Architecture Overview

```
                ┌────────────────────────────────────────┐
                │  Driver completes mark-change-completed│
                └──────────────┬─────────────────────────┘
                               │ orchestrator done state.yaml <<< {payload}
                               ▼
                ┌────────────────────────────────────────┐
                │  bin/orchestrator → record_main(argv)  │
                └──────────────┬─────────────────────────┘
                               ▼
       ┌───────────────────────┴────────────────────────────────┐
       │  record(state_yaml_path, payload, db)                  │
       │                                                          │
       │   1. existing path: parse, validate, write step_history │
       │   2. existing path: _detect_boundary (Phase 4)          │
       │                                                          │
       │   3. NEW: if step_id == "mark-change-completed"          │
       │           AND status == "completed":                     │
       │       data = _resolve_feature_metrics(state, change_id) │
       │           (calls parse_tasks, compute_retries,           │
       │            compute_resolution, run_git_churn,            │
       │            extract_review_scores, wall_clock_minutes)    │
       │           — runs OUTSIDE BEGIN                           │
       │                                                          │
       │   4. NEW: BEGIN                                          │
       │           upsert_step_event(...)                         │
       │           _write_feature_metrics(db, ..., data)          │
       │       COMMIT  (ROLLBACK + non-zero exit on any error)    │
       │                                                          │
       │   5. existing path: Phase 4 phase/feature boundaries     │
       │                     fire at remove-worktree (unchanged)  │
       └──────────────┬───────────────────────────────────────────┘
                      ▼
            ┌──────────────────────────────┐
            │  DuckDB metrics.duckdb        │
            │   step_events (existing)      │
            │   feature_metrics (existing)  │
            │   phase_events / drv_sessions │
            └───────────────────────────────┘
```

### Key Abstractions

- **`_resolve_feature_metrics(state: dict, change_id: str) → dict`**: pure function. Reads `tasks.md` (or returns NULL columns for spike), `state.step_history`, `state.started_at`/`completed_at`, and runs `git log/diff` against `state.worktree_path`. Returns a dict whose keys are exactly the kwargs `upsert_feature_metrics` accepts (`schema_name`, `tasks_total`, ..., `review_score_avg`, `wall_clock_minutes`, `source`). Raises on missing required state fields for `feature`/`bugfix` schemas. Does NOT open a DuckDB connection.

- **`_write_feature_metrics(db, repo_root: str, change_id: str, data: dict) → None`**: calls `upsert_feature_metrics(db, repo_root=..., change_id=..., **data)`. Caller controls the transaction. No retry, no swallowing — exceptions propagate to the BEGIN/COMMIT block.

- **`mark-change-completed` trigger**: a 1-line predicate inside `record()` that activates the absorbed path. Lives next to (and before) the existing Phase 4 boundary detection so the two paths don't tangle.

- **Six computation helpers** (verbatim move): `parse_tasks(tasks_md: Path) → dict`, `compute_retries(state: dict) → dict`, `compute_resolution(tasks_total, tasks_completed, retries_total, step_history, quarantine_events) → dict`, `run_git_churn(worktree: str, change_id: str) → dict`, `extract_review_scores(state: dict) → dict`, `wall_clock_minutes(state: dict) → float | None`. Signatures and bodies copy-paste from `ingest-feature-metrics.py`. Internal `_SLUG_RE` is dropped (the slug guard inside `upsert_feature_metrics` already enforces it).

## Low-Level Design

### Components

**1. `record.py` — six computation functions (verbatim move)**

Lifted with no logic change from `ingest-feature-metrics.py:67-314`. Module-private (no leading underscore in source for parity tests; Phase 4 left similar helpers public-by-default). Imports needed: `re`, `subprocess`, `datetime`, `json`, `pathlib.Path`. All already present in `record.py`.

Per cycle-12 rule: once the functions land in `record.py`, the parity test (T-3) is the live-DB validation that the move did not drift the column values.

**2. `record.py` — `_resolve_feature_metrics`**

```python
def _resolve_feature_metrics(state: dict, change_id: str) -> dict:
    """Pure compute. Returns kwargs dict for upsert_feature_metrics.

    Raises:
        FileNotFoundError: tasks.md missing for feature/bugfix schemas.
        RuntimeError:      started_at or completed_at missing on feature/bugfix.
    """
    repo_root = str(state.get("repo_root") or "")
    schema = str(state.get("schema") or "feature")
    worktree = str(state.get("worktree_path") or repo_root)

    if schema in ("feature", "bugfix"):
        if not state.get("started_at") or not state.get("completed_at"):
            raise RuntimeError(
                f"_resolve_feature_metrics: state missing started_at/completed_at "
                f"for schema={schema}"
            )

    tasks_md = _resolve_feature_metrics_tasks_path(state)
    if schema in ("feature", "bugfix") and not tasks_md.is_file():
        raise FileNotFoundError(
            f"_resolve_feature_metrics: tasks.md not found at {tasks_md} "
            f"(required for schema={schema})"
        )

    if tasks_md.is_file():
        task_counts = parse_tasks(tasks_md)
    else:
        task_counts = {
            "tasks_total": None, "tasks_planned": None, "tasks_added": None,
            "tasks_completed": None, "tasks_failed": None, "resolve_rate": None,
        }

    retries = compute_retries(state)
    resolution = compute_resolution(
        tasks_total=task_counts.get("tasks_total"),
        tasks_completed=task_counts.get("tasks_completed"),
        retries_total=retries["retries_total"],
        step_history=state.get("step_history") or [],
        quarantine_events=state.get("quarantine_events"),
    )
    churn = run_git_churn(worktree, change_id)
    reviews = extract_review_scores(state)
    wc = wall_clock_minutes(state)

    return {
        "schema_name": schema,
        **task_counts,
        **retries,
        **resolution,
        **churn,
        "review_scores_json": json.dumps(reviews["scores_list"]),
        "review_score_avg": reviews["avg"],
        "wall_clock_minutes": wc,
        "source": f"done@{_utcnow_iso()}",
    }
```

The path resolver `_resolve_feature_metrics_tasks_path` reuses the same logic the script has at lines 360-365: prefer `state.tasks_path`, fall back to `<repo_root>/.state/<change_id>/tasks.md`. (The existing `_resolve_tasks_md` helper at `record.py:545` uses `<worktree>/spec/changes/<change_id>/tasks.md` — different fallback. We add a new helper rather than reusing the wrong one.)

**3. `record.py` — `_write_feature_metrics`**

```python
def _write_feature_metrics(db, repo_root: str, change_id: str, data: dict) -> None:
    """Caller controls transaction. Exceptions propagate."""
    from orchestrator_next.upsert import upsert_feature_metrics
    upsert_feature_metrics(db, repo_root=repo_root, change_id=change_id, **data)
```

Three lines. The function exists for symmetry with Phase 4 and as a single test seam for the trigger ROLLBACK test (mockable to raise).

**4. `record.py` — trigger inside `record()`**

The trigger lives inside the existing `if db is not None:` block at `record.py:917`. New branch added BEFORE the `_detect_boundary` call so the two paths are disjoint:

```python
# Phase 5 (FR-3): absorbed feature_metrics write.
if step_id == "mark-change-completed" and status == "completed":
    # Resolve OUTSIDE the transaction (git-log + tasks.md parsing).
    try:
        fm_data = _resolve_feature_metrics(state_raw, change_id_val)
    except Exception as exc:
        sys.stderr.write(f"[done] feature_metrics resolution failed: {exc}\n")
        return (
            {"action": "error", "reason": "feature_metrics_resolution_failed",
             "detail": str(exc)},
            5,
        )
    db.execute("BEGIN")
    try:
        upsert_step_event(db, _step_entry, ctx)
        _write_feature_metrics(db, repo_root_val, change_id_val, fm_data)
        db.execute("COMMIT")
    except Exception as exc:
        db.execute("ROLLBACK")
        sys.stderr.write(f"[done] feature_metrics write failed: {exc}\n")
        return (
            {"action": "error", "reason": "feature_metrics_write_failed",
             "detail": str(exc)},
            5,
        )
    # Skip the legacy non-boundary `upsert_step_event` below — we just did it.
    # Boundary detection still runs after the trigger if the step happens to
    # also be a phase boundary; on the current `_complete-phase.yaml`,
    # mark-change-completed is NOT phase-last, so _detect_boundary returns NONE.
    _phase5_handled = True
else:
    _phase5_handled = False

if not _phase5_handled:
    # existing Phase 4 boundary path: NONE / PHASE / FEATURE
    boundary = _detect_boundary(workflow_plan, phase, step_id, status)
    ...
```

The `_phase5_handled` flag is local to the function; the existing boundary branch is unchanged. After Phase 5 lands, `mark-change-completed` is not the last entry in any `workflow_plan[phase].active`, so its Phase 4 boundary kind would have been `NONE` anyway — the trigger replaces a fail-soft step write with a fatal-on-failure transactional one for that one step.

Caller-site claim verified by grep (cycle-16): the existing `record.py` `record()` function:
- Has access to `state_raw`, `change_id_val`, `repo_root_val`, `step_id`, `phase`, `status`, `db`, and `_step_entry` at the boundary block (`record.py:920-927`). Confirmed by reading the function body.
- Already imports `sys`, `json`, `os`, `pathlib.Path`, `subprocess` (the latter via `_sp` alias in retro-append). Confirmed by reading lines 9-23.
- Does NOT currently import `re` at module top. `re` is imported at module top (`import re` at line 14). Confirmed.

**5. `_complete-phase.yaml` — Stage B deletion**

Single-line edit: remove `  - ingest-feature-metrics` from line 20. The result is a 6-step list:

```yaml
steps:
  - compute-prediction-accuracy
  - run-learn-cycle
  - mark-change-completed
  - compute-swe-metrics
  - archive-completed-change
  - remove-worktree
```

**6. `test-complete-phase-order.sh` — rewrite**

Drop:
- The `ingest-feature-metrics` entry from `REQUIRED_ORDER` array (currently line ~63).
- The `mark-change-completed → ingest-feature-metrics` ordering check (currently lines ~98-102).
- The `ingest-feature-metrics → compute-swe-metrics` ordering check (currently lines ~104-108).

Add:
- `mark-change-completed → compute-swe-metrics` ordering check (the new surviving invariant).
- Absence assertion: `echo "$STEPS" | grep -q "ingest-feature-metrics"` MUST return non-zero.

Keep:
- `compute-prediction-accuracy → run-learn-cycle → mark-change-completed` ordering.
- `compute-swe-metrics → archive-completed-change` ordering.
- The Stage 4 absence assertions for `ingest-driver-auto` and `ingest-subagents-auto`.

**7. Parity test `test_feature_metrics_parity.py`**

Concrete plan:

- Fixture: `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/state.yaml` (+ companion `tasks.md` in the same archive dir).
- Setup: copy fixture state.yaml + tasks.md to a tmp dir; create two tmp DuckDBs (legacy and absorbed); run `ensure_schema()` on both.
- Path A (legacy): `subprocess.run([sys.executable, str(ORCHESTRATOR_HOME / "scripts/inline/ingest-feature-metrics.py"), str(tmp_state)])` with `METRICS_DB=<tmp_legacy.duckdb>`.
- Path B (absorbed): load `state.yaml` → call `_resolve_feature_metrics(state, change_id)` → connect to `<tmp_new.duckdb>` → `_write_feature_metrics(db, repo_root, change_id, data)`.
- Assertion: `SELECT * FROM feature_metrics` on each DB; build dicts column→value; assert equal across these 24 columns:
  - `schema_name`, `tasks_total`, `tasks_planned`, `tasks_added`, `tasks_completed`, `tasks_failed`, `resolve_rate`, `pass_at_1`, `pass_at_2`, `regressions`, `regression_rate`, `retries_total`, `human_interventions`, `files_changed`, `insertions`, `deletions`, `total_commits`, `rework_commits`, `rework_rate`, `review_scores_json`, `review_score_avg`, `wall_clock_minutes`, `repo_root`, `change_id`.
- Excluded: `source` (legitimately differs by FR-6), `computed_at` (audit timestamp).
- Pre-Stage-A behavior: import of `_resolve_feature_metrics` fails → test fails RED.
- Post-Stage-A behavior: column dicts equal → test passes GREEN.

If `run_git_churn` produces different values across two runs (e.g., a commit lands between the two subprocess calls), the test sets `git -C <fixture_repo>` to a fixed snapshot or compares only non-churn columns. Practical safeguard: the fixture's `worktree_path` is the archived feature's worktree which no longer exists, so `run_git_churn` returns the all-zeros default for both runs → identical churn columns trivially.

### Data Flow

1. Driver completes `mark-change-completed` → calls `orchestrator done state.yaml <<< {payload}`.
2. `record_main` opens DuckDB and calls `record(state_yaml_path, payload, db)`.
3. `record()` writes `step_history` to state.yaml as today.
4. `record()` enters the `if db is not None:` block. New branch matches `step_id == "mark-change-completed"` AND `status == "completed"`.
5. `_resolve_feature_metrics(state, change_id)` runs OUTSIDE BEGIN: reads tasks.md, parses state, runs `git log/diff`, returns dict.
6. `BEGIN; upsert_step_event(...); _write_feature_metrics(...); COMMIT`. ROLLBACK + non-zero exit on any error.
7. Phase 4 `_detect_boundary` path runs only when the trigger did NOT match (skipped otherwise via `_phase5_handled` flag).
8. `record()` returns response + exit code.

### State Management

- `state.yaml.step_history` — appended on every call (existing behavior preserved).
- `state.yaml.status` — unchanged by this feature (only `payload.status == "abandoned"` mutates it; that path is unaffected).
- DuckDB `feature_metrics` — INSERT OR REPLACE keyed on `(repo_root, change_id)` from the `mark-change-completed` trigger. No other path writes to `feature_metrics` after Stage B.
- Stage transition: between Stage A landing and Stage B deletion, BOTH the absorbed path and the inline `ingest-feature-metrics` step write to `feature_metrics`. INSERT OR REPLACE makes the second write win idempotently. The order in `_complete-phase.yaml` ensures the absorbed write (at `mark-change-completed`) lands first and the inline write (next step) overwrites with the legacy `source`. After Stage B the inline path is gone and the absorbed `source` (`done@...`) is the only writer.

### Error Handling

| Error | Behavior |
|-------|----------|
| `mark-change-completed` payload + `tasks.md` missing on `feature` schema | `_resolve_feature_metrics` raises `FileNotFoundError` BEFORE `BEGIN`; no DB writes; exit 5. |
| `mark-change-completed` payload + `started_at`/`completed_at` missing on `feature` schema | `_resolve_feature_metrics` raises `RuntimeError` BEFORE `BEGIN`; no DB writes; exit 5. |
| `mark-change-completed` on `spike` schema (no `tasks.md`) | `_resolve_feature_metrics` returns dict with NULL task columns; transaction commits; exit 0. |
| `git log` subprocess timeout / non-zero | `run_git_churn` returns zeros (existing non-fatal policy); transaction commits; exit 0. |
| `_write_feature_metrics` raises (DuckDB lock, schema drift) | ROLLBACK; no `step_events` row left committed; exit 5. |
| Step write fails inside the trigger transaction | ROLLBACK; no `feature_metrics` row left committed; exit 5. |
| Non-`mark-change-completed` step | Existing Phase 4 path runs unchanged. |

## Constraints

- DuckDB transactions issued via `db.execute("BEGIN" / "COMMIT" / "ROLLBACK")`. No nested transactions — the trigger path and the Phase 4 boundary path are mutually exclusive (`_phase5_handled` flag enforces this).
- `change_id` slug validation handled inside `upsert_feature_metrics` (existing behavior preserved). No new SQL is authored; the existing parameterised INSERT in `upsert.py:97-160` is the sole writer.
- Module name `record.py` is fixed by Phase 4 decision (not renamed).
- `feature_metrics` schema is frozen — Phase 5 does not add or remove columns. `DESCRIBE feature_metrics` against live DB confirms all 25 columns (including `computed_at` audit) match the legacy script's writes (cycle-12 rule).
- The trigger fires at the `mark-change-completed` step regardless of which phase it lives in. Today it lives in `_complete-phase`; if a future schema relocates it, the trigger still fires.

## Trade-offs

- **Special-case trigger vs. step-contract metadata**: chose special-case (Approach A). Costs: a one-step `if` branch in `record()`. Buys: avoids inventing a contract-schema field that has exactly one consumer. Discovery OQ-1 framed this; simplicity-first wins.
- **Fatal vs. fail-soft for the trigger**: chose fatal. Costs availability (a DuckDB lock now fails the step, not just the standalone ingest step). Buys consistency: `compute-swe-metrics` two positions later assumes a complete row, and silent partial writes are exactly the bug Phase 4 was built to prevent.
- **Trigger fires at `mark-change-completed` (not at the workflow's last step)**: costs a tiny departure from Phase 4's "all boundary writes fire at remove-worktree" mental model. Buys correctness: `compute-swe-metrics` reads the row via LEFT JOIN, and reordering it after `remove-worktree` is impossible (worktree gets deleted). This deliberate split is documented above (Context).
- **Six computation functions in `record.py` vs. a sibling module**: chose verbatim move into `record.py`. Costs ~250 lines of file growth. Buys: zero import-graph changes, parity with Phase 4's "all helpers in `record.py`" pattern, and one place to look for the trigger + helpers. A future cosmetic split is fine but not gated by Phase 5.
- **Two-stage rollout vs. single commit**: chose two stages. Costs: one extra commit and a brief window where both paths write `feature_metrics` (idempotent INSERT OR REPLACE makes this safe — the inline script's write is the second writer and its `source` value briefly wins until Stage B lands). Buys: this very feature's complete phase still works through Stage A because the inline script and step entry are intact.
- **Resolve outside BEGIN**: the git-log subprocess call (10 s timeout) and tasks.md parsing run BEFORE `BEGIN`. Costs: no atomicity between the resolve snapshot and the write — but the resolve depends only on git history (immutable post-commit) and on `state.yaml`/`tasks.md` which the workflow has already finalised by `mark-change-completed`. Buys: short transaction window (no locks held during git-log).

## Decisions

- Trigger condition: `step_id == "mark-change-completed"` AND `status == "completed"` → activate Phase 5 path. → Resolves OQ-1 with Option A (no new contract field).
- Failure mode: fatal-on-failure → atomic with the step row → matches Phase 4 NFR-3. → Resolves OQ-2.
- Transaction scope: `BEGIN; upsert_step_event(...); _write_feature_metrics(...); COMMIT;` — single transaction, resolve outside. → Resolves OQ-3.
- Helper layout: `_resolve_feature_metrics` + `_write_feature_metrics` in `record.py`; six computation functions verbatim in `record.py`. → Mirrors Phase 4 helper pattern.
- Stage layout: A=additive (helpers + trigger + parity test, both paths coexist); B=deletion (script, contract, complete-phase entry, legacy test, fixture key, verify-all entry). → Mirrors Phase 4 delete-last pattern.
- Parity fixture: `spec/changes/archive/2026-04-25-done-verb-level-aware-writes/`. Diff all 24 non-audit columns; exclude `source` (FR-6) and `computed_at` (audit timestamp). → Resolves OQ-6 from discovery.
- DDL: no migration. `feature_metrics` columns confirmed via live `DESCRIBE feature_metrics` (cycle-12 rule). → Frozen schema is part of "Out of scope".
- Caller-site verification (cycle-16 rule): `record()` already has `state_raw`, `change_id_val`, `repo_root_val`, `step_id`, `phase`, `status`, `db`, `_step_entry` in scope at the boundary block (verified by reading `record.py:920-927`). `re`, `json`, `subprocess` (via `_sp`), `pathlib.Path`, `datetime` (via `_dt`) are imported at module top (verified by reading `record.py:9-23`). The 6 computation functions can be appended after the existing helpers without new imports.

## Open Questions

- (None blocking implementation. OQ-1 / OQ-2 / OQ-3 closed in Decisions; OQ-4 — surviving invariant for `test-complete-phase-order.sh` — is `mark-change-completed → compute-swe-metrics` plus the `ingest-feature-metrics`-absent assertion (encoded in FR-8); OQ-5 — bootstrap staging — uses delete-last per Phase 4 retro precedent; OQ-6 — parity fixture and excluded columns — encoded in FR-10 and Decisions.)
