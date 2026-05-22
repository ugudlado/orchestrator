---
feature-id: orc-67
linear-ticket: N/A
---

# Design: Implement run-phase-review needs_work rework loop

## Context

`run-phase-review` is specified to loop on a `needs_work` verdict — drain the
reviewer-appended fix tasks, then re-run the review — bounded by `max_retries`
with an `on_max_retries` escalation. The spec exists (`run-phase-review.yaml`
step 7c, `error-recovery.md` row 17) but the engine never implements it.

The decisive constraint comes from reading the engine. `dispatch.py` selects
the next step purely from `readiness.next_ready_node(state)` — a DAG walk over
`workflow_plan[phase].nodes[].status`. It **never reads `state.next_step`**;
`resume-token.md` confirms `next_step` is a derived resume pointer, not the
dispatch source of truth. Therefore the only lever that controls re-dispatch is
**node status** (`readiness.mark_node_status`). A node with `status: completed`
is skipped by `is_node_ready`; a node left `in_progress` is re-dispatchable.

`record.py` already exploits exactly this for `repeat_until`: after a completed
record, `_repeat_until_pending` keeps the just-completed node `in_progress`
(record.py:1494-1498) instead of `completed` when the predicate is still False,
so the DAG walk re-emits it. The rework loop is the same mechanism applied to a
verdict instead of a predicate.

The reviewer emits `status: completed` with `outputs.phase_review_report.verdict`
∈ {`pass`, `needs_work`, `incomplete_phase`} and `state_patch.retries` carrying
absolute counts. `record.py` runs `_apply_state_patch` → `mark_node_status` →
`_compute_next_step` in that order (record.py:1468-1503), so the retry count is
available before next-step computation.

## Goals / Non-Goals

### Goals

- On a `needs_work` (or `incomplete_phase`) verdict with `retries < max_retries`,
  the engine re-dispatches `execute-next-task` (draining the reviewer-appended
  fix tasks), then re-runs `run-phase-review`.
- On `retries >= max_retries`, the engine executes the `on_max_retries`
  escalation — it halts the dispatch loop rather than silently advancing.
- A `pass` verdict advances linearly with zero behavior change.
- The loop composes with the ORC-63 `nodes`-shape `workflow_plan` via the
  existing `readiness` helpers — no new DAG concept.
- Reconcile `error-recovery.md` row 17 with `run-phase-review.yaml` step 7b so
  the two contracts agree on the terminal status of a `needs_work` review.

### Non-Goals

- No change to the `orchestrator next` / `orchestrator done` CLI interface
  (ORC-63 non-goal carried forward).
- No DAG back-edge / general-graph support, no new `repeat_until` predicate on
  `run-phase-review`, no grammar change.
- `run-ux-critique.yaml` step 43 has an identical retry-loop shape — generalising
  the pattern to cover it is an explicit follow-up (OQ-7), out of scope here.
- No `state.yaml` schema change; all writes stay on the existing
  `record.py` / `readiness.mark_node_status` path.
- General step-failure recovery is unchanged (already handled by
  `error-recovery.md` Step Failure row).

## Approaches Considered

### Approach 1: Verdict-aware branch in `record.py` (mirror `repeat_until`)

`_compute_next_step` (and the node-status flip block that precedes it) gains a
verdict-aware branch: when the just-completed step is `run-phase-review` and the
verdict is `needs_work`/`incomplete_phase`, read `retries` vs `max_retries`.
Below ceiling: re-open the rework-loop nodes via `mark_node_status` so the DAG
walk naturally re-emits `execute-next-task` then `run-phase-review`. At ceiling:
record the entry with a blocking status and set `state.status = "paused"`.

- Pros: reuses `_compute_next_step`, `mark_node_status`, and the `repeat_until`
  pattern living in the *same function*; `next_step` stays a single-writer
  field; retry count is in hand at record time; Python-testable.
- Cons: `record.py` (already large) absorbs verdict-specific logic; escalation
  is a side-effect (`state.status`, blocking step status) rather than a pure
  `next_step` return.
- Complexity: **M (3)**.

### Approach 2: Driver-side verdict check in `skills/orchestrate/SKILL.md`

The driver parses `verdict` from the reviewer COMPLETION before calling
`orchestrator next` and steers dispatch (override node statuses / re-queue
`execute-next-task`) from prose.

- Pros: keeps `record.py` a pure DAG-walk; escalation is natural for the driver.
- Cons: splits "what step comes next" between tested Python and untested prose;
  reintroduces verdict-parsing into the driver right after ORC-45/63 normalised
  the dispatcher-vs-agent separation; brittle to COMPLETION-format drift.
- Complexity: **M (3)** — small in Python, but prose branching + lost test
  coverage offset that.

### Approach 3: DAG back-edge in the `workflow_plan` nodes shape

Declare a conditional back-edge so `readiness.py` makes a `completed`
`run-phase-review` node re-eligible when a verdict predicate holds.

- Pros: most composable long-term; `run-ux-critique` generalises for free;
  aligns with the ORC-65 per-task-DAG epic.
- Cons: largest surface — touches `grammar.yaml`, `readiness.py`,
  `generate_plan.py`, and the node schema; turns the DAG into a general graph
  (cycle/termination reasoning); out of scope by the ticket's own framing.
- Complexity: **L (5)**.

### Selected Approach

**Approach 1.** Auto-selection heuristic (XS=1..XL=5; lowest complexity; tie →
higher module reuse; further tie → alphabetical):

| Approach | Complexity | Score | Module reuse |
|----------|-----------|-------|--------------|
| 1 — record.py verdict branch | M | 3 | High — `_compute_next_step`, `mark_node_status`, `repeat_until` pattern, all in one file |
| 2 — driver-side check        | M | 3 | Low — moves tested logic to SKILL.md prose |
| 3 — DAG back-edge            | L | 5 | Low — new grammar + readiness + generate_plan surface |

Approaches 1 and 2 tie on complexity (3). The tiebreaker is module reuse:
Approach 1 reuses the `repeat_until` machinery that already lives in
`_compute_next_step` and the node-status flip block, where verdict logic is a
two-line extension of an existing pattern. Approach 2 scores lower — it relocates
next-step logic out of tested Python into prose. Approach 1 wins before reaching
the alphabetical tiebreaker. This also honors the project rule "prefer the boring
solution": the boring solution is to extend the proven `repeat_until` pattern,
not to invent a graph dialect (Approach 3) or move logic into the driver
(Approach 2).

## High-Level Design

### Architecture Overview

The change is contained in `record.py` plus one contract-doc edit. No new
module, no schema change, no `dispatch.py` change beyond what already exists.

Sequence on a `run-phase-review` completion record:

```
orchestrator done  →  record():
  1. _apply_state_patch        → retries.run-phase-review updated (absolute count)
  2. node-status flip block    → run-phase-review node status decided HERE
  3. _compute_next_step        → next_step pointer re-derived from DAG walk
  4. yaml.safe_dump            → state.yaml written
```

The rework decision happens at **step 2** (which nodes are left re-dispatchable)
and, on ceiling, also writes `state.status` and the recorded entry's `status`.
Step 3 then derives `next_step` from the node statuses step 2 set — no special
case needed inside `_compute_next_step` itself beyond the existing one.

### Key Abstractions

- **`_phase_review_verdict(payload) -> str | None`** — extracts
  `outputs.phase_review_report.verdict` from the done payload; `None` for any
  non-`run-phase-review` step or absent report.
- **`_rework_loop_active(verdict, retries, max_retries) -> "retry" | "escalate" | None`**
  — pure decision function. `retry` when verdict needs rework and
  `retries < max_retries`; `escalate` when verdict needs rework and
  `retries >= max_retries`; `None` for `pass` / non-rework verdicts. Unit-tested
  in isolation.
- **`_max_retry_rounds(state_raw) -> int`** — reads
  `quality_bar.max_retry_rounds` from `project.yaml` (the same key the reviewer
  uses); defaults to a documented fallback if absent.

The existing `readiness.mark_node_status` and `is_node_ready` are reused
unchanged. "Rework verdict" = `verdict ∈ {needs_work, incomplete_phase}`.

## Low-Level Design

### Components

**`record.py` — node-status flip block (currently record.py:1494-1498).**
Today: `_repeat_until_pending` decides `in_progress` vs `completed` for the
just-completed node. Extension: when the step is `run-phase-review` and
`_rework_loop_active` returns `retry`, exactly **two** nodes are re-opened:
1. the just-completed `run-phase-review` node is left re-dispatchable
   (`in_progress`) — this mirrors `repeat_until` exactly, no backward mutation;
2. the `execute-next-task` node (which is `completed` at review time, see OQ-5)
   is reset to `in_progress` via `mark_node_status` — the one unavoidable
   backward mutation.

Intermediate nodes (e.g. `run-ux-critique`, which sits between the two in
`feature.yaml`) are left `completed` and are **not** re-dispatched — a
code-quality `needs_work` finding must not re-spend the UX critic's LLM budget.
The DAG walk then proceeds: `execute-next-task` is ready (its
`repeat_until: all_tasks_completed` drains the appended fix tasks, then it flips
to `completed`); `run-ux-critique` is skipped (`completed`);
`run-phase-review` becomes ready (its only dependency is `completed`) and
re-runs. `execute-next-task` precedes `run-phase-review` in every workflow that
uses the step (verified in `feature.yaml`, `bugfix.yaml`, `spike.yaml`).

**`record.py` — escalation on ceiling.** When `_rework_loop_active` returns
`escalate`, record.py:
1. Overrides the recorded `step_history` entry `status` from `completed` to
   `blocked` (the payload says `completed`; the engine downgrades it because
   the review round did not pass and retries are spent). `blocked` is in
   `dispatch.py`'s `_BLOCKING_STATUSES`, so the next `orchestrator next` exits 2
   and the driver halts — this is the only mechanism that actually stops the
   loop, since `dispatch.py` does not read top-level `state.status`.
2. Sets `state_raw["status"] = "paused"` (mirrors the existing FR-2 precedent
   at record.py:1487 where `abandoned` → `state.status = "blocked"`), so a
   resuming session and `error-recovery.md § Escalation Protocol` see the
   paused state.
3. Leaves all nodes `completed` — no re-dispatch; the loop is terminated.

**`error-recovery.md` row 17 — contract reconciliation (OQ-1).** Edit row 17 so
the terminal status of a `needs_work` review is `status: completed` with
`verdict: needs_work` (matching `run-phase-review.yaml` step 7b and the
reviewer's actual emission), not `status: failed`. The retry counter key is
named `run-phase-review` (the step id), consistent with `retries.<step_id>` used
everywhere else; `retries.phase_verify` in row 17 is replaced.

### Data Flow

1. Reviewer emits `{status: completed, outputs.phase_review_report.verdict,
   state_patch.retries: {run-phase-review: <absolute count>}}`.
2. `_apply_state_patch` merges `retries` into `state_raw["retries"]`.
3. Node-status flip block reads `_phase_review_verdict(payload)`,
   `state_raw["retries"].get("run-phase-review", 0)`, and `_max_retry_rounds`.
4. `_rework_loop_active` → `retry` | `escalate` | `None`.
   - `retry`: leave `run-phase-review` `in_progress` and reset
     `execute-next-task` to `in_progress`; intermediate nodes stay `completed`.
   - `escalate`: recorded entry status → `blocked`, `state.status` → `paused`.
   - `None`: existing behavior (`repeat_until` check, else `completed`).
5. `_compute_next_step` derives `next_step` from the resulting node statuses.

### State Management

- `state_raw["retries"]` — `{step_id: absolute_count}`, owned by the reviewer's
  `state_patch`, merged by `_apply_state_patch`. Absent / malformed `retries`
  defaults to `{}`; `retries.get("run-phase-review", 0)` treats absence as 0.
- `workflow_plan[phase].nodes[].status` — mutated only via
  `readiness.mark_node_status`. The rework loop touches exactly two nodes
  (`run-phase-review` left `in_progress`, `execute-next-task` reset to
  `in_progress`); intermediate nodes are untouched. No node moves to a status
  it cannot already hold.
- `state_raw["status"]` — set to `"paused"` on escalation. `status` is **not**
  in `_STATE_PATCH_KEYS`, so the reviewer cannot route it via `state_patch`;
  `record.py` writes it directly, exactly as it does for `abandoned` today.
- All writes go through the existing `yaml.safe_dump(..., sort_keys=False)`
  path — diffable, no schema change.

### Error Handling

- **`retries` absent / malformed** — `retries.get("run-phase-review", 0)`
  treats it as 0; `_apply_state_patch` already defaults to `{}`.
- **`max_retry_rounds` absent from project.yaml** — `_max_retry_rounds` returns
  a documented default (`3`, matching the `verify_block.max_retries` historical
  default) and logs a `[record]` stderr warning. The repo's own project.yaml
  sets it to 8, so the default only applies to a misconfigured repo.
- **needs_work with no fix tasks appended (UC-E1)** — no extra guard. The
  re-opened `execute-next-task` finds `all_tasks_completed` immediately True and
  bounces straight back to `run-phase-review`; the retry counter bounds the
  loop. A tight loop is capped by `max_retry_rounds`, which is the correct
  ceiling — adding a second guard would duplicate the bound.
- **Unknown verdict** — `_validate_phase_review_output` already rejects any
  verdict outside `_PHASE_REVIEW_VERDICTS` at the record boundary (exit 3)
  before the flip block runs; the rework code only ever sees a valid verdict.
- **Legacy `active:[ids]` plan shape** — `mark_node_status` is a no-op when the
  phase has no `nodes` list; the rework loop degrades to current behavior
  (linear advance) on a legacy plan, which is acceptable since ORC-63 plans are
  the supported shape.

## Constraints

- `orchestrator next` / `orchestrator done` interface unchanged (ORC-63).
- `state.yaml` writes stay stable/diffable: `yaml.safe_dump`, `sort_keys=False`.
- stdlib + pyyaml + duckdb only.
- `dispatch.py` is not modified — it already exits 2 on a `blocked` last
  entry, which is the halt mechanism escalation relies on.
- `_STATE_PATCH_KEYS` is unchanged — `status` and `next_step` stay engine-owned.

## Trade-offs

- **`record.py` absorbs verdict logic.** `_compute_next_step` and the flip block
  are no longer a pure DAG walk — they now know one verdict enum. Accepted: the
  logic is small, isolated in named helpers, fully unit-testable, and reuses the
  `repeat_until` precedent that already made this function verdict-of-a-kind
  aware. The alternative (driver prose) is worse — untestable and format-fragile.
- **Escalation downgrades a recorded `completed` entry to `blocked`.** A
  `step_history` entry's status no longer always equals the payload status for
  `run-phase-review`. Accepted and necessary: `dispatch.py` only halts on a
  blocking last-entry status, and top-level `state.status` is not read by
  dispatch. The downgrade is documented in `error-recovery.md` and confined to
  the retry-exhausted case.
- **No back-edge / no generalisation.** `run-ux-critique` still needs its own
  fix later. Accepted: scoping to one step keeps the change a contained bug fix;
  the follow-up can extract the shared helper once the pattern is proven here.

## Acceptance Criteria

- AC-1: Given a `run-phase-review` completion with
  `verdict: needs_work` and `retries["run-phase-review"] < max_retry_rounds`,
  when `orchestrator done` records it, then the `run-phase-review` and
  `execute-next-task` nodes are left re-dispatchable (`status: in_progress`) and
  the next `readiness.next_ready_node` is `execute-next-task`. [traces: UC-1]
- AC-2: Given a `run-phase-review` completion with `verdict: needs_work` and
  `retries["run-phase-review"] >= max_retry_rounds`, when `orchestrator done`
  records it, then the recorded `step_history` entry has `status: blocked`,
  `state.status` is `"paused"`, and the next `orchestrator next` exits 2 (no
  silent advance). [traces: UC-2]
- AC-3: Given a `run-phase-review` completion with `verdict: pass`, when
  `orchestrator done` records it, then the node is marked `completed` and
  `next_step` advances to the next workflow step exactly as before this change
  (the `test_repeat_until.py` and `test_dispatch_resume.py` suites stay green).
  [traces: UC-3]
- AC-4: Given a `needs_work` verdict and a `workflow_plan` in the ORC-63
  `nodes` shape, when the rework loop re-opens nodes, then it does so only via
  `readiness.mark_node_status` over `workflow_plan[phase].nodes`, and a legacy
  `active:[ids]` plan degrades to linear advance without error. [traces: UC-1, UC-3]
- AC-5: Given `verdict: incomplete_phase` with retries remaining, when
  `orchestrator done` records it, then the rework loop re-dispatches
  `execute-next-task` identically to the `needs_work` path. [traces: UC-E2]
- AC-6: Given a `needs_work` verdict where the reviewer appended no fix tasks,
  when the rework loop re-opens `execute-next-task`, then `execute-next-task`
  finds `all_tasks_completed` True and the loop re-runs `run-phase-review`,
  bounded by `max_retry_rounds` (no unbounded loop). [traces: UC-E1]
- AC-7: Given `state.yaml` with `retries` absent or not a dict, when a
  `needs_work` verdict is recorded, then the engine treats the retry count as 0
  and does not raise. [traces: UC-E3, UC-E4]
- AC-8: Given `error-recovery.md` after this change, when row 17 is read, then
  it states a `needs_work` review is `status: completed` with
  `verdict: needs_work` and increments `retries.run-phase-review` — matching
  `run-phase-review.yaml` step 7b. [traces: UC-1, UC-2]

## Decisions

- **OQ-1 — terminal status of a `needs_work` review** → `status: completed`
  with `verdict: needs_work` is authoritative; `error-recovery.md` row 17 is
  edited to match `run-phase-review.yaml` step 7b. → Rationale: it is what the
  reviewer emits today, what `test_record_validation.py::TestCheckD` already
  asserts, and what `extract_review_scores` already filters on; a `needs_work`
  review *succeeded* at finding gaps — it did not fail. → Consequence:
  `_compute_next_step` sees a `needs_work` verdict on a `completed` record;
  the rework branch keys off the verdict, not a `failed` status.
- **OQ-2 — `max_retries` source** → `quality_bar.max_retry_rounds` in
  `project.yaml` (8 for this repo). → Rationale: the reviewer already reads this
  key; the engine MUST read the same one or retry accounting splits.
  `verify_block.max_retries` is a separate slot for a different purpose and is
  not conflated. → Consequence: `_max_retry_rounds` reads `project.yaml`;
  default `3` only on a misconfigured repo.
- **OQ-3 — locus** → Approach 1 (`record.py` verdict branch). → Rationale:
  auto-selection heuristic — ties Approach 2 on complexity (3), wins on module
  reuse (reuses the `repeat_until` pattern in the same function); honors
  "prefer the boring solution". → Consequence: no driver/SKILL.md contract
  change, no grammar change; logic stays Python-testable.
- **OQ-4 — `run-phase-review` node status after `needs_work`** → leave it
  `in_progress` (re-dispatchable), mirroring `repeat_until` exactly. → Rationale:
  `_repeat_until_pending` already keeps a just-completed node `in_progress`;
  reusing that path introduces no "backward DAG mutation" precedent. →
  Consequence: the DAG walk re-emits the node naturally; no new readiness concept.
- **OQ-5 — `execute-next-task` state at rework re-entry** → it is `completed`
  at review time (its `all_tasks_completed` predicate was True, which is what
  let the workflow reach `run-phase-review`); the rework loop resets it to
  `in_progress`. → Rationale: a `completed` node is skipped by `is_node_ready`;
  it must be re-opened for the fix tasks to be drained. → Consequence: the
  rework loop resets the whole `execute-next-task`..`run-phase-review` segment,
  and `execute-next-task`'s own `repeat_until` then drains the new tasks.
- **OQ-6 — `incomplete_phase` verdict** → treated identically to `needs_work`
  (re-dispatch `execute-next-task`). → Rationale: the recovery path is the same
  — drain unchecked tasks, re-review; a separate path would duplicate logic. →
  Consequence: "rework verdict" = `{needs_work, incomplete_phase}` throughout.
- **OQ-7 — `run-ux-critique` retry loop** → explicit follow-up, not in this
  ticket. → Rationale: scoping to `run-phase-review` keeps this a contained bug
  fix; the shared helper can be extracted once the pattern is proven. →
  Consequence: a follow-up ticket generalises the rework loop to
  `run-ux-critique` step 43.

## Open Questions

- None. All seven discovery open questions are resolved in the Decisions section.
</content>
</invoke>
