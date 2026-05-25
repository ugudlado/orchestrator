---
feature-id: orc-81
linear-ticket: ORC-81
---

# Design: workflow_plan-aware reconcile (ORC-81)

## Context

`reconcile_in_progress` (`config/scripts/orchestrator_next/reconcile.py`) is the
single helper that keeps `state.step_history` aligned with the DuckDB
`step_events` table at the start of every `orchestrator next` call. Today it
treats DuckDB as the sole authority for `in_progress` truth: FR-4 strips
YAML-only orphans, FR-5 materialises DB-only rows, neither predicate checks
`state.workflow_plan[phase].nodes` membership. A crashed prior run leaves
ghost `in_progress` rows in DuckDB; if the workflow schema later changes
(step renamed, deleted, or whole schema swapped under the same `change_id`),
the ghost survives reconcile, is materialised into `step_history`, and
`dispatch.py`'s resume branch (`dispatch.py:287-326`) dispatches it with
`is_resume: true`. The user sees a step that does not exist in the current
plan, while `state.yaml.next_step` reports something else — exactly the
ORC-71 recovery the ticket describes. A contributing factor: reconcile is
in-memory only; if `orchestrator next` exits via the phase-complete (exit 1)
or input-missing (exit 2) paths, the strip is lost and the orphan resurfaces
on the next call.

## Goals / Non-Goals

### Goals

- Stop reconcile from materialising or preserving `in_progress` step rows
  whose `step_id` is not in `state.workflow_plan[phase].nodes` for the row's
  phase.
- Ensure that when reconcile strips a `workflow_plan`-orphan from
  `state.step_history`, the change survives across `orchestrator next`
  invocations (durably persisted) even on early-exit paths (exit 1 / exit 2).
- Add defense-in-depth in `dispatch.py`'s resume branch so a contaminated
  `last` entry cannot be dispatched even if a future caller bypasses
  reconcile.
- Lock in the new behaviour with regression tests that use a populated
  `workflow_plan` (current tests use `workflow_plan={}`, which is why the
  gap is invisible).

### Non-Goals

- Purging legacy ghost rows from `metrics.duckdb` (operational cleanup; out
  of scope here — the guard prevents them from doing damage on resume).
- Changing the DB-wins contract for `in_progress` truth between YAML and
  DuckDB *within* the current workflow_plan — only out-of-plan rows are
  affected.
- Touching `record.py`'s upsert path or the `step_events` schema.
- Rewriting the in-memory-only reconcile design as a write-through helper;
  the persistence fix lives at the caller (`bin/orchestrator`) so reconcile
  keeps its current "pure mutation" contract.
- Backfilling `workflow_plan` membership checks onto already-`completed` or
  `failed` entries. The bug is `in_progress`-specific.

## Approaches Considered

### Approach 1: Guard in reconcile only (FR-4 + FR-5) + caller-side persistence

Extend both reconcile predicates with a `step_id ∈ phase_nodes(state, phase)`
test. FR-4 drops any `in_progress` entry whose `step_id` is not in the
current plan for its phase (regardless of DB presence). FR-5 skips
materialisation for any DB row whose `step_id` is not in the current plan
for the row's phase. Caller (`bin/orchestrator`) persists `state.yaml` once
after the reconcile call when `step_history` was mutated, so the strip
survives an exit-1/exit-2 early return.

- Pros:
  - Single source of truth for the membership rule (reconcile owns the
    DB↔YAML reconciliation contract; the guard belongs alongside FR-4/FR-5).
  - Reconcile stays a pure in-memory mutator — no I/O coupling — matching
    its documented contract (`reconcile.py:11-12`).
  - Caller persistence is one branch in `bin/orchestrator` between the
    `reconcile_in_progress(...)` call and `dispatch(...)`.
- Cons:
  - No defense-in-depth in `dispatch.py` — a future caller that calls
    `dispatch` without reconcile (e.g. tests, alt entry points) could still
    surface a ghost entry.
- Complexity: **S**

### Approach 2: Guard in reconcile (FR-4 + FR-5) + caller persistence + dispatch resume membership assert

Approach 1 plus a one-line membership check in the dispatch resume branch
(`dispatch.py:287-326`): if `last.step_id not in {n["id"] for n in
phase_nodes(state, state.phase)}`, log an error and fall through to the
DAG-walk path (or exit 3 — TBD in tasks). Reuse a shared
`_step_in_plan(state, phase, step_id)` helper so reconcile and dispatch
cannot drift.

- Pros:
  - Defense-in-depth: dispatch refuses to resume out-of-plan steps even if
    reconcile is bypassed, was skipped due to missing DB, or its strip was
    not persisted for any reason.
  - Shared helper guarantees the two call sites use identical membership
    semantics (and both go through `phase_nodes()`, so the legacy
    `active:[ids]` shape is handled).
- Cons:
  - Two call sites to maintain (mitigated by the shared helper).
  - Slightly more test surface (a dispatch-resume regression test on top of
    the reconcile tests).
- Complexity: **S**

### Approach 3: Reconcile writes state.yaml directly

Reconcile takes a `state_yaml_path` parameter and writes the file when it
mutates `step_history`. No caller change needed for persistence.

- Pros:
  - Persistence is automatic; callers cannot forget.
- Cons:
  - Breaks reconcile's documented "no disk writes" contract
    (`reconcile.py:11-12,42-47`).
  - Reconcile becomes harder to test (needs a tmp_path for every test)
    and harder to reason about (mutates disk + memory together).
  - Couples reconcile to the YAML serialiser and the post-write corruption
    guard already implemented in `dispatch._persist_node_status`.
- Complexity: **M**

### Selected Approach

**Approach 2** — guard in reconcile (both FR-4 and FR-5), caller-side
persistence after a mutation, plus a defense-in-depth membership assert in
`dispatch.py`'s resume branch using a shared `_step_in_plan` helper.

Rationale:
- The ticket explicitly cites `dispatch.py:236-275` as a second site and
  asks which layer owns the guard. Approach 2 picks reconcile as the
  primary owner (cleaner — it already owns DB/YAML reconciliation truth)
  and adds a cheap dispatch-side assert as insurance against future
  bypasses. Approach 1 leaves that insurance off the table for no real
  saving.
- Approach 3 violates reconcile's existing contract and a documented
  decision (`reconcile.py:11-12`). Approach 2's caller-side persistence
  achieves the same durability with a one-branch change at the caller.
- Both Approach 1 and Approach 2 are complexity S; Approach 2 covers the
  ticket's second site, so it wins on completeness for the same cost.

## High-Level Design

### Architecture Overview

```
orchestrator next
  └─ bin/orchestrator
       ├─ reconcile_in_progress(state, db, context)         ← FR-4/FR-5 + plan guard
       │     ├─ FR-4 strip: drop YAML in_progress entries that are
       │     │              (DB-absent) OR (workflow_plan-absent)
       │     └─ FR-5 materialise: append DB in_progress rows ONLY when
       │                          step_id ∈ phase_nodes(state, row.phase)
       ├─ persist state.yaml IF step_history was mutated     ← new
       └─ dispatch(state, state_yaml_path)
             └─ resume branch: assert last.step_id ∈ phase_nodes(state, state.phase)
                               (defense-in-depth; reuses shared helper)
```

### Key Abstractions

- **`_step_in_plan(state, phase, step_id) -> bool`** — new private helper
  in `reconcile.py`, exported for `dispatch.py`. Wraps
  `phase_nodes(state, phase)` (already in `parser.py`) so the legacy
  `active:[ids]` shape and the post-promotion `nodes:` shape both resolve
  identically. Single membership predicate; both reconcile and dispatch
  import it.

  ```python
  def _step_in_plan(state: State, phase: str, step_id: str) -> bool:
      return any(str(n.get("id", "")) == step_id
                 for n in phase_nodes(state, phase))
  ```

## Low-Level Design

### Components

| Component | Change |
|---|---|
| `config/scripts/orchestrator_next/reconcile.py` | Add `_step_in_plan` helper. Extend FR-4 strip predicate with `OR not _step_in_plan(state, e.phase, e.step_id)`. Add FR-5 skip when `not _step_in_plan(state, phase, step_id)`. Update module docstring to note the new membership rule. |
| `config/scripts/orchestrator_next/dispatch.py` | Import `_step_in_plan`. In the resume branch (`dispatch.py:287-326`), before loading the contract, check membership. On miss: log `ERROR: refusing to resume <step_id> — not in workflow_plan[<phase>].nodes (likely ghost from prior schema); strip the entry from state.step_history and exit 3` so the caller sees a hard failure rather than dispatching a nonexistent step. Rationale for exit 3: the state is contaminated; the caller (`bin/orchestrator`) already treats exit 3 as a hard error. |
| `bin/orchestrator` | After the `reconcile_in_progress(state, _db, _context)` call (line 283), snapshot `step_history` length+ids before the call and re-compare after. If mutated, write the in-memory `state` back to `state_yaml_path` using the same dump pattern as `dispatch._persist_node_status` (with the corruption-guard restore). This persists FR-4 strips before the exit-1/exit-2 early-return paths inside `dispatch()`. |
| `config/scripts/orchestrator_next/tests/test_reconcile_in_progress.py` | Update fixture(s) or add a new test module to use populated `workflow_plan`. Add three regression tests for the new behaviour (see Test Scenarios below). |

### Data Flow

1. `orchestrator next` opens DuckDB and calls `reconcile_in_progress`.
2. Inside reconcile:
   - FR-4 walks `state.step_history`. An `in_progress` entry is **dropped**
     when `(phase, step_id, attempt) not in db_keys` **OR** when
     `not _step_in_plan(state, e.phase, e.step_id)`. (Either condition is
     sufficient to declare the entry an orphan.)
   - FR-5 iterates DB rows. A row is **skipped** when
     `not _step_in_plan(state, phase, step_id)`. Surviving rows are
     materialised exactly as before.
3. Back in `bin/orchestrator`, after reconcile returns, compute a stable
   signature of `step_history` (e.g. tuple of `(phase, step_id, attempt,
   status)`). If different from the pre-reconcile signature, write
   `state.yaml` to disk before calling `dispatch()`.
4. `dispatch()` runs. In the resume branch, if `last.step_id` fails the
   `_step_in_plan` check, dispatch prints an error and exits 3.

### State Management

- `state.step_history` — mutated by reconcile (existing behaviour) and now
  optionally persisted to disk by the caller when reconcile changed it.
- `state.workflow_plan` — read-only in this change; the source of truth for
  the membership predicate.
- DuckDB `step_events` — untouched. Ghost rows remain in the table; the
  guard prevents them from corrupting `state.step_history` and dispatch.
  Operational cleanup is out of scope.

### Error Handling

- Reconcile: invalid `change_id` still raises `ValueError` (existing
  behaviour). The new membership check is a pure read; no new exceptions.
- Caller persistence: wrap the post-reconcile write in `try/except` so a
  disk-write failure logs a warning but does not block dispatch (mirrors
  the existing `warning: reconcile failed — ...` pattern at line 285).
  Reuse the post-write corruption guard from `_persist_node_status`
  (`dispatch.py:216-222`).
- Dispatch resume guard: on a membership miss, print a single-line
  `ERROR:` to stderr identifying the rogue `step_id` and `phase`, then
  exit 3.

## Constraints

- Must not break the existing reconcile public API (`reconcile_in_progress(state, db, context)`).
- Must go through `parser.phase_nodes()` for membership so the legacy
  `active:[ids]` shape continues to work (a raw `workflow_plan[phase]["nodes"]`
  read would mis-strip pre-promotion in-flight steps — flagged by advisor).
- Must not introduce DuckDB writes in reconcile (DB rows stay; they are
  operational data, not engine truth for plan membership).
- The caller-side persistence write must use the same corruption-guard
  pattern as `_persist_node_status` so a malformed write cannot brick
  `state.yaml`.

## Trade-offs

- **Ghost rows linger in DuckDB.** Out-of-plan in_progress rows are not
  deleted; they are simply ignored by reconcile/dispatch. Accepted because
  (a) cleaning the table belongs to an operational tool, not the dispatch
  hot path, and (b) the guard makes them inert.
- **Two call sites use the membership predicate** (reconcile FR-4/FR-5 and
  dispatch resume). Mitigated by the shared `_step_in_plan` helper; both
  sites import the same function so semantics cannot drift.
- **Caller persistence runs an extra YAML write on the rare reconcile-mutates
  path.** Cost is one write per `orchestrator next` call only when reconcile
  actually changed `step_history` (uncommon — happens on crash recovery and
  schema-swap edge cases). Acceptable; the alternative is a recurring
  orphan-resurfacing bug.

## Acceptance Criteria

- AC-1: `reconcile_in_progress` strips any `in_progress` entry from
  `state.step_history` whose `step_id` is not in
  `phase_nodes(state, entry.phase)`, regardless of DuckDB presence.
  [traces: UC-1]
- AC-2: `reconcile_in_progress` does not materialise a DuckDB `in_progress`
  row whose `step_id` is not in `phase_nodes(state, row.phase)`.
  [traces: UC-1]
- AC-3: When `reconcile_in_progress` mutates `state.step_history`,
  `bin/orchestrator` persists `state.yaml` to disk before calling
  `dispatch()`, so the strip survives an exit-1 or exit-2 early return.
  Verified by a test that runs `orchestrator next` twice against a state
  with a ghost row and asserts the ghost is absent from `state.yaml` on
  disk after the first call. [traces: UC-2]
- AC-4: `dispatch()`'s resume branch refuses to resume a `last.step_id`
  that is not in `phase_nodes(state, state.phase)` — exits 3 with an
  ERROR-prefixed stderr message naming the rogue `step_id` and `phase`.
  [traces: UC-1]
- AC-5: The reconcile module exposes a single `_step_in_plan(state, phase,
  step_id)` helper used by both `reconcile.py` and `dispatch.py`; both call
  sites go through `parser.phase_nodes()` (so the legacy `active:[ids]`
  shape resolves correctly). [traces: UC-1, UC-2]
- AC-6: Existing reconcile tests
  (`test_reconcile_in_progress.py`, `test_reconcile_terminal_skip.py`)
  still pass; their fixtures are updated to populate `workflow_plan` with
  the step ids they exercise so the membership check does not regress
  them. [traces: UC-1]

## Decisions

- Guard lives in reconcile (FR-4 + FR-5), with a defense-in-depth assert in
  `dispatch.py`'s resume branch → reconcile is the canonical DB↔YAML
  reconciliation owner; a single membership predicate cannot accidentally
  drift between the two sites because a shared helper is imported.
- Membership predicate goes through `parser.phase_nodes()` (not a raw
  `workflow_plan[phase]["nodes"]` read) → consequence: pre-promotion
  states using the legacy `active:[ids]` shape resolve correctly and do
  not have their in-flight steps incorrectly stripped.
- Persistence fix lives at the caller (`bin/orchestrator`), not inside
  reconcile → consequence: reconcile keeps its documented "no disk writes"
  contract (`reconcile.py:11-12`) and remains easy to unit-test; the
  caller takes on a small `before/after` signature compare + write.
- Dispatch resume guard exits 3 (hard error) rather than silently
  stripping and re-dispatching → consequence: a contaminated `last`
  entry that somehow escaped reconcile is loud, not silent; matches the
  existing exit-3 treatment for `FileNotFoundError` and
  `ContractDispatchError` in `bin/orchestrator:294-303`.
- Ghost DuckDB rows are left in place (not deleted) → consequence:
  operational cleanup is out of scope; the guard makes them inert.

## Open Questions

(none — all five advisor decision points resolved above)
