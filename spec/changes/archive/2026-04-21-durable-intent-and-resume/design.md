# Design: Durable intent + idempotent resume

## Selected Approach — Status-column `in_progress` row + in-memory reconcile

**Summary.** Reuse the existing six-column PK `(repo_root, change_id, phase, step_id, attempt,
status)` at `upsert.py:53`. `status='in_progress'` becomes a distinct PK partition; rows with
NULL cost/usage live alongside terminal rows at the same `(step_id, attempt)` without collision.
Write the pending row in `bin/orchestrator` after `dispatch()` returns. Delete it in `record()`
on terminal status. On `next` startup, reconcile state.yaml against DuckDB (DB wins) BEFORE
calling `dispatch()`. Dispatch stays pure (no DB). Resume is a new action verb, NOT an overload
of the old `retry_step` path.

## OQ Resolutions

### OQ-1: Replace the existing `retry_step` path (Option A)

Verified facts against HEAD:

- `dispatch.py:270-308` returns `action='retry_step'` with `attempt = _compute_attempt(...)`
  — i.e. `last.attempt + 1` — when the last state.yaml history entry is `in_progress`.
- `_compute_attempt` (`dispatch.py:39-51`) does NOT filter by status; it includes the
  in_progress entry itself in its `attempts` list and returns `max + 1`.
- `config/steps/contracts/step-dispatch.md:78-96` documents `retry_step` as "the next attempt
  number — caller writes this value into the new step_history entry". This is the
  "prior-attempt-failed, bump-and-retry" semantic.
- The `/orchestrate` driver at `skills/orchestrate/SKILL.md:145-147` treats `retry_step`
  identically to `run_step` plus a `previous_failure` prompt line.
- `retry_step` is produced only at `dispatch.py:293` and consumed only at `SKILL.md:145`.
  Replacing it is a bounded two-file change plus the contract doc.

Resume semantics require the SAME attempt number, not `attempt + 1`. Overloading `retry_step`
to mean "resume" would silently wrong the attempt counter because `_compute_attempt` would
return 2 on resume from attempt 1. Therefore: **remove the `retry_step` branch; replace with
a `resume_step` branch that uses `last.attempt or 1` directly** (bypassing `_compute_attempt`).

After Phase 2 reconcile runs, the in-memory `State.step_history` reflects DB truth for
`in_progress`. A surviving `in_progress` entry under this invariant IS a legitimate resume.
There is no remaining case for the old "bump-and-retry on orphan in_progress" semantic —
reconcile has removed orphans (FR-4) and materialised DB-only rows (FR-5) before dispatch runs.
One mechanism, one source of truth.

### OQ-2: Reconcile in a new helper, called from `bin/orchestrator` before `dispatch()`

Verified against HEAD: `dispatch()` at `dispatch.py:248-254` is documented pure; the existing
DB interaction lives in `bin/orchestrator:554-582`. The reconcile logic belongs in a new module
`config/scripts/orchestrator_next/reconcile.py` exposing:

```python
def reconcile_in_progress(state: State, db, context: dict) -> None:
    """Mutate state.step_history in place so it matches DuckDB in_progress truth.

    Rules:
      - For each in_progress row in DuckDB for (repo_root, change_id):
          if state.step_history lacks a matching (phase, step_id, attempt, status='in_progress')
          entry, append a synthesised StepHistoryEntry with started_at from the DB row.
      - For each in_progress entry in state.step_history for the current change_id:
          if DuckDB has no matching row, remove that entry from state.step_history.

    DB wins. No writes to state.yaml in this helper — only in-memory mutation. The caller
    (bin/orchestrator) does not persist the mutated state.yaml to disk here; the write-back
    happens naturally when `next` appends its own in_progress entry and any subsequent code
    writes state.yaml. If `next` exits without a write path (e.g. complete_workflow), the
    mutated state is discarded — which is correct, because no step was dispatched.
    """
```

Called from `bin/orchestrator` inside the existing `_metrics_db_path` block (`bin/orchestrator:
560-580`), after `ensure_schema(_db)` and the terminal-row upsert loop, BEFORE `dispatch(state,
...)` at line 585. If `_metrics_db_path` is None (offline/test), reconcile is skipped —
dispatch runs against unmodified state.yaml, same as today.

### OQ-3: state.yaml in_progress removal matches on `(step_id, phase, status='in_progress')`, no attempt

Verified: `record()` at `record.py:420-424` computes `attempt = max(prior_attempts) + 1` from
terminal history entries. It can be called with `payload.attempt` (`record.py:483`), but the
Phase 2 invariant (FR-1 + FR-6) says at most one `(step_id, phase, status='in_progress')` entry
exists at any time: `next` writes one, `record` deletes it. So match keyed on `(step_id, phase,
status='in_progress')` is unambiguous and removes a class of off-by-one bugs from threading
attempt through both writer and reader.

Mutation is in-place on `state_raw["step_history"]` before the append at `record.py:489`:

```python
# Remove any pre-existing in_progress placeholder for this (step_id, phase).
history = [
    e for e in history
    if not (
        isinstance(e, dict)
        and e.get("step_id") == step_id
        and e.get("phase") == phase
        and e.get("status") == "in_progress"
    )
]
# Append the terminal entry (existing logic).
history.append(entry)
state_raw["step_history"] = history
```

### OQ-4: Pending write happens in `bin/orchestrator` after `dispatch()`, gated by action verb

Verified: `bin/orchestrator:584-595` is where the action is retrieved and printed. The pending
write belongs at lines ~585-593 (post-dispatch, pre-print). Gated on `action.get("action") in
{"run_step", "run_inline", "resume_step"}`. The gating key is the action verb, NOT which
branch of `dispatch()` produced it — this is a learned rule from discovery Constraint #10: all
non-step early returns (`blocked`, `verify_phase`, `complete_workflow`) produce distinct action
verbs, so verb-based gating is both simpler and safer than branch inference.

On `resume_step`: the pending write is an idempotent re-INSERT of the same row (same PK, same
started_at from reconcile-populated state). INSERT OR REPLACE writes identical contents. This
is acceptable; it keeps the write path uniform. No code branch reads "is this the first write
or a resume write?" — they are the same write.

### OQ-5: Test fixtures mutate state.yaml + DB directly

Tests use the existing `in_memory_db` fixture pattern (`test_record_cost_compute.py:108-113`)
plus a `tmp_path` with a hand-crafted state.yaml and plan.yaml. For reconcile scenarios, tests
directly execute `UPSERT INTO step_events (...)` or directly edit state.yaml bytes before
invoking `reconcile_in_progress(state, db, context)` — no subprocess. For the end-to-end `next`
test, the `bin/orchestrator` subprocess is invoked with `METRICS_DB` env var pointing to the
in-memory DB's file-backed counterpart; alternatively the helper path is tested directly and
the subprocess path covered by one thin integration test per action verb.

## Components Modified

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/upsert.py` | New function `upsert_pending_step_event(db, *, repo_root, change_id, phase, step_id, attempt, agent_name, started_at)`. Reuses `_INSERT_OR_REPLACE` SQL. Slug guard on `change_id`. NULL for usage/cost/tool_calls/agent_id. No `tool_calls` fan-out. |
| `config/scripts/orchestrator_next/reconcile.py` | New module. One public function `reconcile_in_progress(state, db, context)`. One SELECT from `step_events` filtered by `status='in_progress'`. Mutates `state.step_history` in place. |
| `config/scripts/orchestrator_next/dispatch.py` | (a) Replace the `retry_step` branch at 270-308 with a `resume_step` branch: detect `last.status == "in_progress"`, return action verb `resume_step`, pass `is_resume: true`, use `last.attempt if last.attempt is not None else 1` directly (DO NOT call `_compute_attempt` here). (b) Remove `retry_step` from the docstring at line 7. |
| `config/scripts/orchestrator_next/record.py` | (a) Before the `history.append(entry)` at line 489, filter out any prior in_progress entry for `(step_id, phase)`. (b) After the state.yaml write, if `db` is not None, issue the parameterised DELETE (below) for the terminal row's `(repo_root, change_id, phase, step_id, attempt, 'in_progress')`. DELETE is best-effort: log and continue on failure. |
| `bin/orchestrator` | (a) After `ensure_schema(_db)` at line 567 and before `dispatch()` at 585, call `reconcile_in_progress(state, _db, _context)` if `_db` is not None. (b) After `dispatch(...)` returns, if `action.get("action") in {"run_step", "run_inline", "resume_step"}` and `_db` is not None, call `upsert_pending_step_event` with fields from `action` + `state.repo_root`/`state.change_id`; also re-load and append the in_progress state.yaml entry (or skip append if reconcile already placed it). |
| `config/steps/contracts/step-dispatch.md` | Replace the `retry_step` section (lines 78-96) with a `resume_step` section describing the new verb. Update the exit-code table (line 24) to list `resume_step` alongside `run_step`, `run_inline`. |
| `skills/orchestrate/SKILL.md` | Replace the `retry_step` handler line (line 145) with a `resume_step` handler that logs `RESUMING step <id> (attempt <N>)` on stderr then executes identically to `run_step` / `run_inline` based on whether `action.run` is present. Must fire even under `flags.auto = true`. |

## Pseudocode

### `upsert_pending_step_event` (new helper, ~15 lines)

```python
_INSERT_PENDING = _INSERT_OR_REPLACE  # alias for clarity; same SQL at upsert.py:166-190

def upsert_pending_step_event(
    db,
    *,
    repo_root: str,
    change_id: str,
    phase: str,
    step_id: str,
    attempt: int,
    agent_name: str,
    started_at: str,
) -> None:
    if not _SLUG_RE.match(change_id):
        raise ValueError(f"change_id '{change_id}' violates slug guard.")
    params = [
        repo_root, change_id, phase, step_id, attempt, agent_name,
        None,              # agent_id
        "in_progress",     # status
        started_at,        # started_at
        None,              # ended_at
        None,              # duration_ms
        None, None, None,  # model, input_tokens, output_tokens
        None, None,        # cache_read_input_tokens, cache_creation_input_tokens
        None, None,        # cost_usd, turns
        None, None, None,  # tool_calls_json, artifacts_json, escalation_json
    ]
    db.execute(_INSERT_PENDING, params)
    # No tool_calls fan-out; no _DELETE_TOOL_CALLS call (pending rows never owned any).
```

### `reconcile_in_progress` (new module)

```python
# config/scripts/orchestrator_next/reconcile.py
from orchestrator_next.parser import StepHistoryEntry

_SELECT_IN_PROGRESS = """
SELECT phase, step_id, attempt, agent_name, started_at
FROM step_events
WHERE repo_root = ?
  AND change_id = ?
  AND status = 'in_progress'
"""

def reconcile_in_progress(state, db, context: dict) -> None:
    db_rows = db.execute(
        _SELECT_IN_PROGRESS,
        [context["repo_root"], context["change_id"]],
    ).fetchall()
    db_keys = {(r[0], r[1], r[2]) for r in db_rows}  # (phase, step_id, attempt)

    # FR-4: strip yaml orphans whose (phase, step_id, attempt) is not in DB.
    kept = []
    for e in state.step_history:
        if e.status == "in_progress" and (e.phase, e.step_id, e.attempt) not in db_keys:
            continue  # orphan — drop
        kept.append(e)
    state.step_history = kept

    # FR-5: materialise DB rows that are missing from yaml.
    yaml_keys = {
        (e.phase, e.step_id, e.attempt)
        for e in state.step_history
        if e.status == "in_progress"
    }
    for phase, step_id, attempt, agent_name, started_at in db_rows:
        if (phase, step_id, attempt) in yaml_keys:
            continue
        state.step_history.append(StepHistoryEntry(
            step_id=step_id,
            phase=phase,
            status="in_progress",
            agent=agent_name,
            attempt=attempt,
            started_at=str(started_at) if started_at is not None else None,
            ended_at=None,
            usage={},
            escalation=None,
            raw={
                "step_id": step_id, "phase": phase, "status": "in_progress",
                "agent": agent_name, "attempt": attempt,
                "started_at": str(started_at) if started_at is not None else None,
            },
        ))
```

Invariant preserved: DB is authoritative; state.step_history is a reconciled view. The helper
does NOT write to disk — caller decides whether to persist.

### Dispatch — new `resume_step` branch (replaces `retry_step` at dispatch.py:270-308)

```python
# --- Check: last entry is in_progress → resume (post-reconcile, this is the DB truth)
if (
    last is not None
    and last.phase == state.phase
    and last.status == "in_progress"
    and last.ended_at is None
):
    step_id = last.step_id
    # Resume: keep the ORIGINAL attempt. DO NOT call _compute_attempt — it returns max+1,
    # which is retry semantics. Resume semantics require attempt unchanged.
    attempt = last.attempt if last.attempt is not None else 1
    try:
        contract = load_contract_for_step(step_id, state_yaml_path)
    except FileNotFoundError:
        contract = StepContract(
            id=step_id, agent=last.agent or "inline",
            run=None, instruction="", rules=[],
        )
    inputs_resolved, _missing = _resolve_inputs(state, contract)
    resolved_allowed_tools = _resolve_allowed_tools(contract)
    action = {
        "action": "resume_step",
        "step_id": step_id,
        "phase": state.phase,
        "attempt": attempt,
        "is_resume": True,
        "started_at": last.started_at,
        "agent": contract.agent,
        "run": contract.run,                   # present iff contract has `run:`
        "instruction": contract.instruction,
        "rules": contract.rules,
        "inputs": inputs_resolved,
        "expected_outputs": contract.outputs,
        "resolved_allowed_tools": resolved_allowed_tools,
        "env": _build_env(state, step_id, attempt),
    }
    plan = _load_plan(state_yaml_path)
    action["step_context"] = _find_step_in_plan(plan, state.phase, step_id)
    return action, 0
```

### `bin/orchestrator` — reconcile + post-dispatch pending write

**DB lifecycle change required.** Today `bin/orchestrator:580` closes `_db` BEFORE `dispatch()`
at line 585. Phase 2 needs `_db` open both before (for reconcile) and after (for pending
write) dispatch. The restructure: remove the existing `_db.close()` at line 580 and move it
to after the post-dispatch pending-write block. Wrap the whole DB-scoped region in a single
try/finally so close fires even on dispatch exceptions.

```python
# Replace the existing try block (bin/orchestrator:560-582) + the dispatch call site
# (lines 584-591) with this structure:

_cost_so_far = 0.0
_db = None
try:
    if _metrics_db_path:
        import duckdb
        from orchestrator_next.upsert import (
            ensure_schema, upsert_step_event, sum_cost_usd,
            upsert_pending_step_event,
        )
        from orchestrator_next.reconcile import reconcile_in_progress

        _terminal_statuses = {"completed", "failed", "blocked", "escalate_to_architect"}
        _db = duckdb.connect(_metrics_db_path)
        ensure_schema(_db)
        _context = {"repo_root": state.repo_root, "change_id": state.change_id}
        for _entry in state.step_history:
            if _entry.status in _terminal_statuses:
                try:
                    upsert_step_event(_db, _entry, _context)
                except ValueError as _ve:
                    print(f"warning: upsert skipped — {_ve}", file=sys.stderr)
        try:
            _cost_so_far = sum_cost_usd(_db, _context)
        except Exception as _ce:  # noqa: BLE001
            print(f"warning: cost sum failed — {_ce}", file=sys.stderr)

        # --- NEW: reconcile state.step_history against DB in_progress truth.
        try:
            reconcile_in_progress(state, _db, _context)
        except Exception as exc:  # noqa: BLE001 — reconcile must not block dispatch
            print(f"warning: reconcile failed — {exc}", file=sys.stderr)
except Exception as exc:  # noqa: BLE001
    print(f"warning: step_events upsert/reconcile failed — {exc}", file=sys.stderr)

try:
    try:
        action, exit_code = dispatch(state, state_yaml_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr); sys.exit(3)
    except Exception as exc:  # noqa: BLE001
        print(f"error: dispatch failed — {exc}", file=sys.stderr); sys.exit(3)

    # --- NEW: post-dispatch pending write, gated by action verb.
    _STEP_VERBS = {"run_step", "run_inline", "resume_step"}
    if _db is not None and action.get("action") in _STEP_VERBS:
        from datetime import datetime, timezone
        started_at = action.get("started_at") or datetime.now(timezone.utc).isoformat()
        try:
            upsert_pending_step_event(
                _db,
                repo_root=state.repo_root, change_id=state.change_id,
                phase=action["phase"], step_id=action["step_id"],
                attempt=int(action["attempt"]),
                agent_name=action.get("agent", "inline"),
                started_at=started_at,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warning: pending upsert failed — {exc}", file=sys.stderr)
        try:
            _append_in_progress_state_entry_if_absent(
                state_yaml_path,
                step_id=action["step_id"], phase=action["phase"],
                attempt=int(action["attempt"]),
                agent=action.get("agent", "inline"),
                started_at=started_at,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warning: state.yaml pending append failed — {exc}", file=sys.stderr)

    action["cost_so_far"] = _cost_so_far
    print(emit_json(action), end="")
finally:
    if _db is not None:
        try:
            _db.close()
        except Exception:  # noqa: BLE001
            pass
sys.exit(exit_code)
```

The restructure is necessary because the current `_db.close()` at line 580 fires before
dispatch runs. Reconcile needs `_db` open pre-dispatch; pending write needs it open
post-dispatch. Single close after both completes.

The `_append_in_progress_state_entry_if_absent` helper reads state.yaml raw, checks
`step_history` for an existing `(step_id, phase, status='in_progress')` entry, appends if
absent, and writes back. This is a narrow state.yaml writer — separate from the main `record`
writer — because `next` has never mutated state.yaml before. Keeps the mutation surface
minimal and inspectable.

### `record()` — pending DELETE + state.yaml scrub

```python
# In record.py, replace the block around line 489:
history = [
    e for e in history
    if not (
        isinstance(e, dict)
        and e.get("step_id") == step_id
        and e.get("phase") == phase
        and e.get("status") == "in_progress"
    )
]
history.append(entry)   # existing line
state_raw["step_history"] = history

# After state.yaml write (after record.py:498), delete the in_progress row from DB.
if db is not None:
    _DELETE_PENDING_SQL = (
        "DELETE FROM step_events "
        "WHERE repo_root = ? AND change_id = ? AND phase = ? "
        "AND step_id = ? AND attempt = ? AND status = 'in_progress'"
    )
    try:
        repo_root = state_raw.get("repo_root") or ""
        change_id = state_raw.get("change_id") or ""
        db.execute(
            _DELETE_PENDING_SQL,
            [repo_root, change_id, phase, step_id, entry["attempt"]],
        )
    except Exception as exc:  # noqa: BLE001 — DELETE must not block record success
        sys.stderr.write(f"[record] warning: pending DELETE failed: {exc}\n")
```

The DELETE is parameterised. All five PK filter values come from trusted (validated) sources:
repo_root and change_id from state.yaml (slug guard applies to change_id; repo_root is a
filesystem path), phase/step_id from the payload (already used in the SQL fan-out in
upsert.py), attempt from `entry["attempt"]` which is an int.

## Multi-level Metrics Invariant

In_progress rows have `cost_usd = NULL`. `sum_cost_usd` at `upsert.py:193-197` is
`COALESCE(SUM(cost_usd), 0.0)` — SQL SUM natively skips NULLs; the COALESCE covers the
all-NULL case. Thus:

- Per-step rollups (COUNT or SUM by step_id) already filter on `status='completed'` in existing
  report queries (`cost_report.py`, `metrics_report.py`); in_progress rows are invisible to
  those filters.
- Per-phase, per-feature, per-driver rollups are not introduced in this phase (those are Phase
  4). The invariant holds by default: any future level-scoped query that SUMs cost must use
  the existing NULL-skipping pattern.
- The explicit test AC-8 asserts `sum_cost_usd` is unchanged by in_progress presence.

## SQL Validation Note

The new SQL statements (`_INSERT_PENDING` which is an alias of `_INSERT_OR_REPLACE` at
upsert.py:166-190, `_SELECT_IN_PROGRESS`, `_DELETE_PENDING_SQL`) use column names validated
against the live `step_events` DDL at `upsert.py:28-55`: `repo_root, change_id, phase, step_id,
attempt, agent_name, agent_id, status, schema_name, started_at, ended_at, duration_ms, model,
input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, cost_usd,
turns, tool_calls_json, artifacts_json, escalation_json, upserted_at`. The SELECT uses five
existing columns; DELETE filters on six PK columns; the pending INSERT is already the same SQL
used by `upsert_step_event` (line 521).

## Performance Budget (NFR-1)

The added work per `next` invocation:

1. One `SELECT ... FROM step_events WHERE repo_root = ? AND change_id = ? AND status =
   'in_progress'`. Index on `(repo_root, change_id)` already exists
   (`_CREATE_INDEX`, upsert.py:74-77). Expected: sub-millisecond against ~10k rows.
2. One `INSERT OR REPLACE` into `step_events`. Single-row write. Expected: sub-millisecond.
3. One state.yaml read + conditional append + write. Already happens on `record`; the added
   surface on `next` is one additional file round-trip (~1-2 ms on warm cache).

Production target: combined overhead **p99 < 5 ms** end-to-end `next` latency on a workstation
DuckDB instance with up to 10 k `step_events` rows. This is an absolute production target, not
a microbenchmark: the measurement harness is wall-clock around the `orchestrator next`
subprocess against a realistic populated DB. No tight-loop 1000-call microbenchmark.

## Caller-site verification checklist

Every caller-site claim in this document is grounded in a `rg -n` against HEAD:

| Claim | File:Line |
|-------|-----------|
| `dispatch()` is pure, no DB | `dispatch.py:248-254` |
| Existing retry path reads state.yaml only | `dispatch.py:270-308` |
| `_compute_attempt` returns max+1 without status filter | `dispatch.py:39-51` |
| `bin/orchestrator` opens DB, runs terminal upsert loop | `bin/orchestrator:554-582` |
| `record()` accepts optional `db=None` param | `record.py:299-301` |
| `record()` computes attempt internally | `record.py:420-424` |
| `record()` appends to history at | `record.py:489` |
| `record()` writes state.yaml at | `record.py:497-498` |
| `_INSERT_OR_REPLACE` SQL | `upsert.py:166-190` |
| PK is six-column including status | `upsert.py:53` |
| NULL cost_usd skipped by SUM | `upsert.py:193-197` |
| `retry_step` produced only at | `dispatch.py:293` |
| `retry_step` consumed only at | `skills/orchestrate/SKILL.md:145` |
| Contract doc for `retry_step` | `config/steps/contracts/step-dispatch.md:78-96` |
| Index on `(repo_root, change_id)` | `upsert.py:74-77` |
| Parser accepts in_progress + null ended_at | `parser.py:144-159` |

## Risks

- **R-1**: a concurrent second `orchestrator next` on the same change_id — very unlikely in the
  single-driver workflow, but not impossible. Both could see the same DB state, both write the
  same in_progress row (idempotent), both return the same resume action. The driver should not
  observe duplication; the first to commit wins. Mitigation: documented; no code change.
- **R-2**: the `_append_in_progress_state_entry_if_absent` helper in `bin/orchestrator` is a
  new state.yaml writer. The existing corruption guard in `record.py:399-414` (read pre-write
  bytes, restore on parse failure) should be mirrored here. Design decision: yes — copy the
  same guard pattern into the new helper.
- **R-3**: the retired `retry_step` verb may still appear in one test fixture
  (`test_dispatch_allowed_tools.py:135`). Tasks include updating this test to assert
  `resume_step` instead.
