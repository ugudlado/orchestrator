# Diagnosis: reconcile.py FR-4 misses step rows absent from current workflow_plan

## Symptoms

On `/implement` resume of ORC-71, `orchestrator next` returned a dispatch action for
`execute-next-task` with `is_resume: true`, while `state.yaml` `next_step` claimed
`preview-route`. DuckDB held four ghost `in_progress` step rows
(`specify-acceptance-criteria`, `generate-implementation-plan`,
`implement-pricing-module`, `verify-record-py-integration`) left over from an older
run on a different workflow schema. Reconcile did not strip them, dispatch
materialised one of them, and `orchestrator next` returned a step that does not
exist in the current `workflow_plan`. Manual recovery required purging
`step_events` and `tool_calls` rows from `metrics.duckdb` and reverting off-spec
working-tree edits.

## Reproduction Steps

Runnable Python reproduction against an in-memory DuckDB and a synthetic State.
Save as `/tmp/repro_orc81.py` in the worktree and run with
`PYTHONPATH=config/scripts python3 /tmp/repro_orc81.py`.

```python
import duckdb
from orchestrator_next.upsert import ensure_schema, upsert_pending_step_event
from orchestrator_next.parser import State
from orchestrator_next.reconcile import reconcile_in_progress

# 1. State whose workflow_plan only knows about preview-route + execute-next-task
state = State(
    change_id="my-feature",
    phase="implement",
    repo_root="/repo/root",
    workflow_dir="/repo/root",
    workflow_plan={"implement": {"nodes": [
        {"id": "preview-route"},
        {"id": "execute-next-task"},
    ]}},
    step_history=[],
    raw={},
)

# 2. DuckDB has a ghost in_progress row from an older schema (step_id not in plan)
db = duckdb.connect(":memory:")
ensure_schema(db)
upsert_pending_step_event(
    db,
    repo_root="/repo/root",
    change_id="my-feature",
    phase="implement",
    step_id="implement-pricing-module",   # NOT in workflow_plan
    attempt=1,
    agent_name="developer",
    started_at="2024-01-01T00:00:00Z",
)

# 3. Reconcile — FR-4 should drop the ghost; today it does not.
reconcile_in_progress(state, db, {"repo_root": "/repo/root", "change_id": "my-feature"})

plan_ids = {n["id"] for n in state.workflow_plan["implement"]["nodes"]}
materialised = [e for e in state.step_history if e.status == "in_progress"]
print("materialised in_progress entries:", [(e.step_id, e.step_id in plan_ids) for e in materialised])
assert not materialised or all(e.step_id in plan_ids for e in materialised), \
    "FR-4 leak: in_progress entry whose step_id is not in workflow_plan"
```

### Expected vs Actual

- **Expected**: `materialised in_progress entries: []` (the ghost row is recognised
  as not part of the current plan and dropped). Or, equivalently, a clear
  reconcile-time error rejecting the contaminated state.
- **Actual**: `materialised in_progress entries: [('implement-pricing-module', False)]`
  — the AssertionError fires. The ghost row from a previous schema is materialised
  into `state.step_history`, and on the next call `dispatch.py:288-326` will
  resume it as `is_resume: true`, returning a step that does not exist in
  `workflow_plan`.

## Investigation

### Evidence Gathered

- `config/scripts/orchestrator_next/reconcile.py:24-30` — `_SELECT_IN_PROGRESS`
  reads all `in_progress` rows for `(repo_root, change_id)` with no
  `workflow_plan` filter.
- `config/scripts/orchestrator_next/reconcile.py:77-83` — FR-4 strip predicate
  keys only on `(phase, step_id, attempt) ∈ db_keys`. There is no test for
  `step_id ∈ workflow_plan[phase].nodes`. A YAML in_progress entry that exists
  in DB but not in the plan survives.
- `config/scripts/orchestrator_next/reconcile.py:85-111` — FR-5 materialise loop
  appends a `StepHistoryEntry` for every DB row not already in YAML. No
  `workflow_plan` membership check, so ghost rows from an older schema become
  YAML in_progress entries.
- `config/scripts/orchestrator_next/dispatch.py:287-326` — the resume branch
  triggers on `last.phase == state.phase and last.status == "in_progress"`. It
  loads the contract (or a fallback `StepContract` if the file is gone) and
  returns `is_resume: true` with no membership check against
  `state.workflow_plan[phase].nodes`.
- `config/scripts/orchestrator_next/readiness.py:27,31` — node identity comes
  from `node["id"]`, so membership is a string-set test against
  `phase_nodes(state, phase)`. This identifier is the same form stored in
  `step_history[*].step_id`.
- Reconcile is documented (`reconcile.py:11-12`, `:42-47`) as "no disk writes —
  caller is responsible for persisting state.yaml if needed." If `orchestrator
  next` exits via the `complete_workflow` branch or `exit 1`/`exit 2` paths
  without persisting the reconciled `state.yaml`, the strip is discarded and the
  orphan re-appears on the next invocation. This is the related sub-symptom
  noted in the ticket implementation notes.

### Data Flow Trace

1. A prior workflow attempt crashes mid-step, leaving an `in_progress` row in
   `metrics.duckdb.step_events` with a `step_id` that belongs to an older
   workflow schema for the same `change_id`.
2. The user `/implement`-resumes after switching to a newer schema whose
   `workflow_plan` lists different step ids.
3. `orchestrator next` runs `reconcile_in_progress(state, db, context)`
   (`reconcile.py:33`).
4. `_SELECT_IN_PROGRESS` returns the ghost row. `db_keys` includes
   `(phase, "implement-pricing-module", 1)`.
5. FR-4 (`reconcile.py:78-83`) walks `state.step_history`. The ghost is not yet
   in YAML, so nothing to strip. If the ghost *were* in YAML, the
   `(phase, step_id, attempt) in db_keys` check would keep it because DB still
   holds it. Neither branch tests `step_id ∈ workflow_plan[phase].nodes`.
6. FR-5 (`reconcile.py:90-111`) appends a new `StepHistoryEntry(step_id=
   "implement-pricing-module", status="in_progress", …)` to
   `state.step_history`.
7. Back in `dispatch.py`, `_get_last_entry(state.step_history)` (line 281)
   returns the freshly materialised ghost. The resume branch
   (`dispatch.py:287-326`) matches and emits a dispatch action for
   `implement-pricing-module` with `is_resume: true`. The step is not in
   `workflow_plan`, so `readiness.next_ready_node` would never have picked it,
   but the resume short-circuit fires before the DAG walk
   (`dispatch.py:328-331`).
8. `state.yaml.next_step` is stale (`preview-route`) because nothing has
   updated it; the caller sees the divergence the ticket reports.

## Root Cause

Reconcile treats DuckDB as the sole authority for `in_progress` truth and never
cross-references the *current* `workflow_plan`. Both the FR-4 strip predicate
and the FR-5 materialise loop accept any `(phase, step_id, attempt)` from
`step_events`, including step ids that no longer exist in
`state.workflow_plan[phase].nodes` (older schema, renamed step, deleted step).
The downstream resume branch in `dispatch.py` also lacks a membership guard, so
a ghost row turns into a dispatch action for a non-existent step.

Reference: `config/scripts/orchestrator_next/reconcile.py:77-83` (FR-4 strip
predicate — missing `workflow_plan` membership clause) and
`config/scripts/orchestrator_next/reconcile.py:90-111` (FR-5 materialise loop —
appends ghost rows unconditionally). Secondary site:
`config/scripts/orchestrator_next/dispatch.py:287-326` (resume branch — no
`workflow_plan` membership gate).

A related contributing condition: reconcile mutates in memory only
(`reconcile.py:11-12`). If the dispatching call returns without writing
`state.yaml` (e.g. `exit 1` "phase complete" at `dispatch.py:349`, or an
upstream input-missing `exit 2` at `dispatch.py:268`), any strip is lost across
calls and the orphan resurfaces.

## Impact

### Severity

high

### Affected Areas

- Any `change_id` reused across workflow schema changes — the engine
  self-modification hazard already tracked in memory
  ([[project_engine_self_modification_hazard]]) shares this failure mode.
- Resume of any feature whose prior attempt crashed leaving an `in_progress` row
  while the step contract was later renamed or deleted under the same
  `change_id`.
- All callers of `reconcile_in_progress`: `dispatch.py` (orchestrator next),
  any future caller that runs reconcile before consulting `step_history`.
  `record.py` does not call reconcile but consumes the mutated `step_history` on
  the next dispatch.
- Existing test coverage in
  `config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py`
  constructs states with `workflow_plan={}` (line 43), so the gap is invisible
  to the current suite. Tests will need fixtures with populated `workflow_plan`
  and ghost step ids to lock in the new behaviour.

### Since When

Introduced with the reconcile helper itself: commit `500bfbe feat(dispatch):
add reconcile_in_progress helper — DB-wins yaml sync`. The follow-up
`9a159f7 fix(orc-58): T-2 resolver fall-through and reconcile terminal-entry
skip fixes` did not address `workflow_plan` membership. Latent in main since
the reconcile helper landed.

## Key Decisions

- **Selected design direction (orc-81, design-and-draft-artifacts):** Approach 2 — guard in reconcile (FR-4 + FR-5) using a shared `_step_in_plan(state, phase, step_id)` helper that routes through `parser.phase_nodes()` (handles both `nodes:` and legacy `active:[ids]` shapes), plus caller-side `state.yaml` persistence in `bin/orchestrator` when reconcile mutates `step_history`, plus a defense-in-depth membership assert in `dispatch.py`'s resume branch that exits 3 on a contaminated `last` entry.
- **Selection criteria:** Auto-heuristic — Approach 1 (S=2), Approach 2 (S=2), Approach 3 (M=3). Tie on S; Approach 2 wins on completeness because it addresses the ticket's explicitly-cited second site (`dispatch.py:236-275`) at the same complexity as Approach 1. Approach 3 ruled out: violates reconcile's documented "no disk writes" contract.
- **Persistence sub-symptom (ticket Implementation Notes):** addressed via caller-side write in `bin/orchestrator` after `reconcile_in_progress` mutates `step_history`. Reconcile keeps its in-memory-only contract.

## Linear Ticket

ORC-81

COMPLETION:
  status: completed
  summary: |
    Diagnosed ORC-81. Root cause: reconcile.py FR-4/FR-5 use DuckDB as the sole
    authority for in_progress truth and never check workflow_plan[phase].nodes
    membership, so a ghost step row from an older schema survives the strip and
    is materialised into state.step_history. dispatch.py's resume branch
    (lines 287-326) then dispatches it with is_resume:true, returning a step
    that does not exist in the current plan. Secondary contributing factor:
    reconcile is in-memory only, so a strip is lost if the call exits without
    a state.yaml write. Repro is a runnable Python snippet against in-memory
    DuckDB; expected/actual captured.
  outputs:
    discovery_result:
      name: discovery
      path: spec/changes/orc-81/discovery.md
  artifacts:
    - spec/changes/orc-81/discovery.md
  evidence:
    files_inspected:
      - config/scripts/orchestrator_next/reconcile.py
      - config/scripts/orchestrator_next/dispatch.py
      - config/scripts/orchestrator_next/readiness.py
      - config/scripts/orchestrator_next/parser.py
      - config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py
    root_cause_refs:
      - config/scripts/orchestrator_next/reconcile.py:77-83
      - config/scripts/orchestrator_next/reconcile.py:85-111
      - config/scripts/orchestrator_next/dispatch.py:287-326
