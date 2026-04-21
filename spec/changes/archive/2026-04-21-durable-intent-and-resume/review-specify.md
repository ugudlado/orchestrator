# Phase Review: Specify — Durable intent + idempotent resume

**Reviewer:** Reviewer agent (claude-sonnet-4-6)
**Date:** 2026-04-20
**Artifacts reviewed:** spec.md, design.md, tasks.md, discovery.md (+ state.yaml, plan.yaml)
**Overall score:** 7.8 / 10
**Verdict:** REJECTED — 2 major findings, 2 minor findings

---

## Verification Results

| Check | Result |
|-------|--------|
| Artifacts exist | PASS — all 4 artifacts present in `.state/durable-intent-and-resume/` |
| AC section present with testable criteria | PASS — 10 ACs, each with named test and method |
| 14 tasks with TDD pairing | PASS — all RED/GREEN pairs verified |
| Locked-decision compliance | PASS — no new CLI subcommand; no schema migration; status='in_progress' as new PK value; delete-on-terminal |
| `_compute_attempt` grep verification | PASS — confirmed at `dispatch.py:39-51`; does NOT filter by status; OQ-1 Option A rationale is correct |
| `retry_step` scope audit (all 5 targets) | PASS — all real files confirmed: `dispatch.py:293`, `SKILL.md:145`, `test_dispatch_allowed_tools.py:208-220`, `test_orchestrator_next.py:124-125`, `test_attempt_counting.py:6,74-75`, `test_cost_so_far.py:112,117`, `golden/state-in-progress-no-ended.json:2` |
| Caller-site verification table (16 claims) | PASS — all 16 entries verified against HEAD |
| NFR-1 performance budget | PASS — design.md cites absolute production target: "p99 < 5 ms end-to-end `next` invocation wall-clock, not a tight-loop call count" |
| Multi-level metrics invariant | PASS — no new columns; `in_progress` is a new row (PK variant), not a new column |
| `sum_cost_usd` NULL handling | PASS — `COALESCE(SUM(cost_usd), 0.0)` at `upsert.py:193-197` confirmed; SQL SUM skips NULLs natively |
| `record()` db=None gating | PASS — `record.py:299-301` confirms `db=None` signature; DELETE block gated on `if db is not None` |
| Crash-between-write-and-return coverage | PASS — NFR-2 + FR-4/FR-5 reconcile cascade; next `next` call repairs any partial state |

---

## Per-AC Coverage Summary

| AC | FR | Test Task | RED task | GREEN task | Status |
|----|-----|-----------|----------|------------|--------|
| AC-1 | FR-1, FR-2 | `test_dispatch_pending_row.py::test_next_writes_in_progress_row_and_state_entry` | T-8 | T-9 | COVERED |
| AC-2 | FR-3 | `test_dispatch_resume.py::test_resume_returns_same_attempt_and_is_resume_flag` | T-5 | T-6 | COVERED |
| AC-3 | FR-6, FR-7 | `test_record_cleans_pending.py::test_terminal_record_deletes_pending_row_and_state_entry` | T-10 | T-11 | COVERED |
| AC-4 | FR-4 | `test_reconcile_in_progress.py::test_yaml_orphan_stripped_when_db_empty` | T-3 | T-4 | COVERED |
| AC-5 | FR-5 | `test_reconcile_in_progress.py::test_db_row_materialises_yaml_entry` | T-3 | T-4 | COVERED |
| AC-6 | FR-8 | `test_dispatch_pending_row.py::test_non_step_actions_skip_pending_write` | T-8 | T-9 | COVERED |
| AC-7 | FR-9 | `test_dispatch_pending_row.py::test_retry_attempt_two_coexists_with_attempt_one` | T-8 | T-9 | COVERED |
| AC-8 | (invariant) | `test_record_cleans_pending.py::test_in_progress_rows_do_not_affect_cost_sum` | T-10 | T-11 | COVERED |
| AC-9 | FR-10 | `test_dispatch_resume.py::test_resume_emits_stderr_log_in_auto_mode` | T-13 | T-7/T-9 | COVERED |
| AC-10 | NFR-5 | `test_record_cleans_pending.py::test_two_cycle_lifecycle_leaves_no_in_progress_rows` | T-12 | T-11 | COVERED |

---

## Findings

### MAJOR-1 — spec.md has three scope-count errors and internally contradicts its own AC section

**Location:** `spec.md:63-66` (In Scope section)

**Evidence:** spec.md states:

> "Three new test files: `test_dispatch_resume.py`, `test_reconcile_in_progress.py`, `test_record_cleans_pending.py`. One existing test update: `test_dispatch_allowed_tools.py:135`"

Both counts are wrong. The actual counts derived from tasks.md are:

**New test files — 5, not 3:**

| File | Task | Listed in spec.md In Scope? |
|------|------|----------------------------|
| `test_dispatch_resume.py` | T-5 | YES |
| `test_reconcile_in_progress.py` | T-3 | YES |
| `test_record_cleans_pending.py` | T-10 | YES |
| `test_dispatch_pending_row.py` | T-8 | NO — missing |
| `test_upsert_pending.py` | T-1 | NO — missing |

**Existing test updates — 5 files/artifacts, not 1:**

| File | Task | Listed in spec.md In Scope? |
|------|------|----------------------------|
| `test_dispatch_allowed_tools.py` | T-5 | YES (line 65) |
| `test_orchestrator_next.py` | T-5 | NO — missing |
| `test_attempt_counting.py` | T-5 | NO — missing |
| `test_cost_so_far.py` | T-5 | NO — missing |
| `golden/state-in-progress-no-ended.json` | T-5 | NO — missing |

The omission of `test_dispatch_pending_row.py` is an internal contradiction: spec.md's own AC section references this file at lines 147, 172, and 176, yet the In Scope section doesn't list it. The spec disagrees with itself.

**Why it matters:** spec.md is the scope contract. Developers are bound to the Files lists in tasks.md, which are derived from the spec's stated scope. The undercounts leave the developer without authoritative guidance on what the spec intended. An auditor or future retro comparing "predicted files (from spec)" vs "actual files (from git diff)" will record false scope creep for the 4 missing files, degrading prediction accuracy metrics. The internal contradiction (AC cites a file the In Scope section doesn't list) is a quality failure: a reviewer can't trust the spec to be self-consistent.

**Required fix (atomic — all three artifacts):**

In `spec.md` In Scope section, replace the two sentences at lines 63-66 with:

> "Five new test files: `test_dispatch_resume.py`, `test_reconcile_in_progress.py`, `test_record_cleans_pending.py`, `test_dispatch_pending_row.py`, `test_upsert_pending.py`. Five existing test updates: `test_dispatch_allowed_tools.py`, `test_orchestrator_next.py`, `test_attempt_counting.py`, `test_cost_so_far.py`, and `golden/state-in-progress-no-ended.json`."

No change needed in tasks.md or design.md (those are already correct).

---

### MAJOR-2 — Design prose says "single try/finally" but pseudocode never uses try/finally; `_db` can leak if `_append_in_progress_state_entry_if_absent` throws

**Location:** `design.md` lines 280 and 347-356

**Evidence:** design.md line 280 says: "Wrap the whole DB-scoped region in a single try/finally so close fires even on dispatch exceptions." The pseudocode that follows never uses `try/finally`. Instead it uses `if _db is not None: _db.close()` in two dispatch exception handlers (lines 324, 328) and once at line 355. However, the post-dispatch block at lines 347-353 calls `_append_in_progress_state_entry_if_absent` with NO exception wrapper. If this writer throws — corruption-guard failure, disk full, YAML parse error — execution exits the `if` block and skips line 355. The `_db` connection is not closed.

The prose/pseudocode contradiction is the core defect. A developer following the pseudocode does not implement the try/finally the prose promised. T-9's own Verify requirement ("Ensure `_db.close()` fires in both the dispatch-exception path and the happy path") cannot be met with the pseudocode as written.

**Why it matters:** The design is the developer's implementation guide. A pseudocode that contradicts its own prose description produces an implementation that fails the task's Verify criterion on the first review pass, forcing a cycle-wasting re-spin. The resource leak itself is narrow (process exits shortly after; OS reclaims the connection), but the spec defect is not.

**Required fix:**

In `design.md`, wrap `_append_in_progress_state_entry_if_absent` in a `try/except` to match the surrounding style:

```python
    try:
        _append_in_progress_state_entry_if_absent(
            state_yaml_path,
            step_id=action["step_id"], phase=action["phase"],
            attempt=int(action["attempt"]),
            agent=action.get("agent", "inline"),
            started_at=started_at,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"warning: state.yaml in_progress append failed — {exc}", file=sys.stderr)

if _db is not None:
    _db.close()
```

Alternatively, implement the originally-promised `try/finally` around the entire post-dispatch block. Either brings pseudocode into alignment with prose and with T-9's Verify requirement. No change needed in tasks.md.

---

### MINOR-1 — T-5 cites stale line number `test_dispatch_allowed_tools.py:135`; actual `retry_step` assertion is at line 208-220

**Location:** `tasks.md:T-5`

**Evidence:** T-5 reads "Retarget `test_dispatch_allowed_tools.py:135`." Grep against HEAD shows the `retry_step` assertion is `assert action["action"] == "retry_step"` at line 220, inside the method `test_retry_step_has_resolved_allowed_tools` starting at line 208. Line 135 is a different test fixture.

**Why it matters:** Developer following T-5 searches line 135, finds unrelated code, and misses the actual assertion. Navigation hazard; not a functional error.

**Required fix:** In `tasks.md:T-5`, change `test_dispatch_allowed_tools.py:135` to `test_dispatch_allowed_tools.py:208-220`.

---

### MINOR-2 — discovery.md OQ-4 and design.md OQ-4 resolve different questions

**Location:** `discovery.md` Open Questions (OQ-4 = repeat-until ordering) vs `design.md` OQ Resolutions (OQ-4 = pending-write location in `bin/orchestrator`)

**Evidence:** The discovery uses "OQ-4" for the question "Does Phase 2 conflict with the `dispatch-repeat-until-honor` fix ordering?" (resolved by Constraint #7). The design uses "OQ-4" for the question "Where does the pending write live in `bin/orchestrator`?" — a question that only crystallised during architecture and was not enumerated in discovery's Open Questions. The traceability chain discovery OQ → design resolution is broken for this label.

**Why it matters:** A reader following the OQ chain from discovery to design finds a mismatch. Cosmetic but confusing; future retrospectives that audit OQ resolution completeness will see a false positive.

**Required fix:** In design.md, relabel the pending-write-location resolution as "OQ-A (Architecture finding)" or add a note that discovery's OQ-4 is answered by Constraint #7 and spec.md Out of Scope, while design.md's OQ-4 is a new architectural question. One-line clarification is sufficient.

---

## Locked-Decision Compliance

| Decision | Status |
|----------|--------|
| Reuse existing PK with `in_progress` status value | COMPLIANT — `upsert.py:53` PK confirmed; design uses it directly |
| No new `orchestrator` CLI subcommand | COMPLIANT — no subcommand additions in scope |
| Delete-on-terminal (not UPDATE-in-place) | COMPLIANT — `record.py` DELETE is the primary cleanup mechanism |
| DB wins on reconcile | COMPLIANT — `reconcile_in_progress` strips yaml orphans, materialises DB rows |
| `is_resume` in action JSON payload | COMPLIANT — design pseudocode includes `"is_resume": True` in the `resume_step` action dict |

---

## OQ-1 Architectural Soundness Verification

The architect's claim that `_compute_attempt` does NOT filter by status was independently verified against HEAD:

```
dispatch.py:46-50:
    attempts = [
        e.attempt
        for e in step_history
        if e.phase == phase and e.step_id == step_id and e.attempt is not None
    ]
```

Filter condition: `e.phase == phase and e.step_id == step_id and e.attempt is not None` — no status filter. An `in_progress` entry with `attempt=1` IS included. If `_compute_attempt` were called on the resume branch it would return `max(1) + 1 = 2`, which is retry semantics (wrong). Option A (replace `retry_step` branch with `resume_step` branch that uses `last.attempt if last.attempt is not None else 1` directly) is architecturally correct. Verified.

---

## Scope Hygiene

The `done` verb rename is explicitly excluded (`spec.md:71`). Confirmed absent in tasks.md and design.md.

The five files flagged for retargeting in T-5 are all real and confirmed by grep. Retargeting is minimal — in-place assertion updates, not rewrites. The `test_attempt_counting.py` update (changing `attempt=3` to `attempt=2`, dropping `previous_failure`, rewriting docstring) is semantically correct under Phase 2 semantics.

No Phase 3/4/5 work detected in tasks.

---

## Risk Coverage

| Risk | Coverage Status |
|------|----------------|
| Crash between pending DB write and action return | COVERED — NFR-2 + FR-4/FR-5 reconcile repairs any partial state on next `next` call |
| record() called with no pending row (offline path) | COVERED — `if db is not None` gates DELETE; missing row is not an error |
| Concurrent second `next` on same change_id | DOCUMENTED as R-1; idempotent INSERT OR REPLACE mitigates; single-driver model makes it very unlikely |
| `_append_in_progress_state_entry_if_absent` writer correctness | DOCUMENTED as R-2; design requires copying corruption guard from `record.py:399-414`; MAJOR-2 finds the wrapper is missing from pseudocode |

---

## Score Breakdown

| Dimension | Score | Notes |
|-----------|-------|-------|
| Spec compliance | 6/10 | Three count errors in In Scope + internal contradiction (AC cites unlisted file); self-consistency failure |
| Algorithm correctness | 10/10 | Resume/retry logic correct; `_compute_attempt` hazard identified and mitigated; all edge cases handled |
| Security | 10/10 | Parameterised SQL throughout; slug guard on change_id; no string interpolation |
| Performance | 10/10 | Absolute p99 < 5ms target cited; production wall-clock measurement harness described |
| Readability | 8/10 | Pseudocode contradicts prose on try/finally (MAJOR-2); OQ-4 label collision (MINOR-2) |
| Simplicity | 9/10 | Minimal new surface; reuses existing SQL; no over-engineering |
| Code quality | 9/10 | Pending write reuses `_INSERT_OR_REPLACE`; corruption guard cross-referenced; pseudocode gap in MAJOR-2 |
| Functional completeness | 9/10 | All 10 ACs covered; test coverage requirement clear |
| Caller-site verification | 9/10 | 16/16 claims in table verified; stale line number in T-5 (MINOR-1) is a navigation hazard |
| **Overall** | **7.8/10** | |

---

## Required Changes Before Approval

### Fix 1 (addresses MAJOR-1) — Spec.md In Scope section: correct both scope counts

In `spec.md` lines 63-66, replace the two count sentences with accurate counts: five new test files (adding `test_dispatch_pending_row.py` and `test_upsert_pending.py`) and five existing test updates (adding `test_orchestrator_next.py`, `test_attempt_counting.py`, `test_cost_so_far.py`, `golden/state-in-progress-no-ended.json`).

All three artifacts (spec.md, design.md, tasks.md) must be updated atomically per the learned rule. tasks.md and design.md are already correct; only spec.md needs this change.

### Fix 2 (addresses MAJOR-2) — Design.md pseudocode: wrap `_append_in_progress_state_entry_if_absent` in try/except

Add a `try/except` around the `_append_in_progress_state_entry_if_absent` call in the `bin/orchestrator` pseudocode block so the `if _db is not None: _db.close()` line is always reached. Brings pseudocode into alignment with the prose's "single try/finally" promise and with T-9's Verify requirement.

### Fix 3 (addresses MINOR-1) — tasks.md T-5: correct stale line number

Change `test_dispatch_allowed_tools.py:135` to `test_dispatch_allowed_tools.py:208-220`.

Fix 3 should be applied in the same pass as Fix 1 and Fix 2.

---

## Verdict

**REJECTED.** Two major findings prevent advancement:

1. spec.md's In Scope section undercounts new test files (3 listed, 5 created) and existing test updates (1 listed, 5 updated), and internally contradicts its own AC section which references a file the In Scope section doesn't name. The spec is not self-consistent.
2. The design pseudocode for `bin/orchestrator` contradicts its own prose: the text promises "a single try/finally" but the pseudocode uses scattered close calls that fail to protect against an unwrapped state.yaml writer call, violating T-9's own Verify requirement.

Fixes are surgical and do not touch the architecture. After applying all three fixes, re-review should approve.

---

## Re-review 2026-04-21

**Reviewer:** Reviewer agent (claude-sonnet-4-6)
**Date:** 2026-04-21
**Prior score:** 7.8/10 (REJECTED)
**Fixes applied:** MAJOR-1, MAJOR-2, MINOR-1

---

### Fix Verification

| Finding | Required Fix | Verified? | Evidence |
|---------|-------------|-----------|----------|
| MAJOR-1 | spec.md In Scope: "Five new test files" + full list; "Five retargeted test artifacts" + full list; NFR-4 updated | PASS | `grep -n "Five new test files\|Five retargeted"` returns lines 63 and 138 with full enumerations including `test_upsert_pending.py`, `test_dispatch_pending_row.py`, `test_orchestrator_next.py`, `test_attempt_counting.py`, `test_cost_so_far.py`, `golden/state-in-progress-no-ended.json` |
| MAJOR-2 | design.md bin/orchestrator pseudocode: outer `try/finally` wraps dispatch + post-dispatch; `_db.close()` in `finally`; `_append_in_progress_state_entry_if_absent` wrapped in `try/except` | PASS | Lines 320-363 confirm: outer `try:` at 320 with `finally:` at 357; `_db.close()` inside `finally` at 360; `_append_in_progress_state_entry_if_absent` wrapped in `try/except` at lines 344-353 |
| MINOR-1 | tasks.md T-5: `test_dispatch_allowed_tools.py:135` → `test_dispatch_allowed_tools.py:208-220` everywhere | PASS | `grep -n "test_dispatch_allowed_tools.py:135"` returns zero matches; T-5 text shows `test_dispatch_allowed_tools.py:208-220` in three places |
| MINOR-2 (deferred) | OQ-4 label reuse — non-blocking, confirmed left as-is | N/A | Driver confirmed no impact on implementation; acceptable technical debt |

**MAJOR-1 detail:** spec.md line 63 now reads "Five new test files: `test_upsert_pending.py`, `test_dispatch_pending_row.py`, ..." including the two previously missing files. Line 65 reads "Five existing test artifacts retargeted..." with all five named including the four previously missing. NFR-4 at line 138 now says "Five new test files plus five retargeted test artifacts (see In Scope)." Internal contradiction between In Scope section and AC section is resolved.

**MAJOR-2 detail:** The fix implemented the "alternatively" branch from the prior review's Required Fix section: the `try/finally` wraps the entire post-dispatch region (lines 320-363). `_append_in_progress_state_entry_if_absent` is now wrapped in its own `try/except` (lines 344-353), matching the `upsert_pending_step_event` wrapper above it. The outer `finally` calls `_db.close()` unconditionally (with its own inner `try/except` swallowing close errors), satisfying both the prose claim ("Wrap the whole DB-scoped region in a single try/finally so close fires even on dispatch exceptions") and T-9's Verify requirement.

**MINOR-1 detail:** All three occurrences of the stale `test_dispatch_allowed_tools.py:135` reference in T-5 have been replaced with `test_dispatch_allowed_tools.py:208-220`. No stale references remain.

---

### Updated Score

| Dimension | Prior Score | Updated Score | Delta | Notes |
|-----------|-------------|---------------|-------|-------|
| Spec compliance | 6/10 | 10/10 | +4 | In Scope now accurate and internally consistent; NFR-4 matches |
| Algorithm correctness | 10/10 | 10/10 | 0 | Unchanged; resume/retry logic correct |
| Security | 10/10 | 10/10 | 0 | Unchanged |
| Performance | 10/10 | 10/10 | 0 | Unchanged |
| Readability | 8/10 | 9/10 | +1 | Pseudocode now matches prose (MAJOR-2 resolved); OQ-4 cosmetic label collision remains (MINOR-2 deferred, -1 retained) |
| Simplicity | 9/10 | 9/10 | 0 | Unchanged |
| Code quality | 9/10 | 9/10 | 0 | Unchanged; pseudocode gap fixed |
| Functional completeness | 9/10 | 9/10 | 0 | Unchanged; all 10 ACs covered |
| Caller-site verification | 9/10 | 10/10 | +1 | Stale line number corrected (MINOR-1 resolved) |
| **Overall** | **7.8/10** | **9.6/10** | **+1.8** | |

---

### Verdict

**APPROVED.**

All three blocking fixes verified correct. The spec is now self-consistent: In Scope counts match the AC section counts, the tasks.md file lists, and the NFR-4 summary. The design pseudocode now implements what the prose promised — a single `try/finally` that unconditionally closes `_db` regardless of which downstream call throws. The stale line number that would have sent a developer to the wrong location in `test_dispatch_allowed_tools.py` is corrected.

MINOR-2 (OQ-4 label reuse) remains as cosmetic technical debt. It does not affect implementation correctness or future retro accuracy beyond a one-time reader confusion risk. Non-blocking.

Resolved findings: MAJOR-1, MAJOR-2, MINOR-1. Deferred: MINOR-2.
