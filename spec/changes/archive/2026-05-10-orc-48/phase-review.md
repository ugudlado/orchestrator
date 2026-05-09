# Phase Review: ORC-48 — Agent spawn usage not flowing into DuckDB metrics

**Reviewed:** 2026-05-10
**Reviewer:** Reviewer Agent (claude-sonnet-4-6)
**Verdict:** NEEDS WORK

---

## Pre-conditions

- tasks.md unchecked items: 0 (no `- [ ]` items found; tasks use section format)
- 3 commits since main: T-1+T-2, T-3, and a follow-up fix commit

---

## Verification

### Tests

```
1 failed, 380 passed
Pre-existing failure: test_seed_state.py::test_seed_state_produces_dispatch_ready_pair (also fails on main, unrelated to ORC-48)
New tests (test_record_agent_field.py): 4/4 PASSED
```

### Build

No build step. Python/YAML project.

---

## Acceptance Criteria Verification

| AC | Criterion | Status | Evidence |
|---|---|---|---|
| AC-1 | record() rejects missing `agent` for agent-step → (error, exit_code=3) reason=payload_missing_agent_for_agent_step | PASS | test_missing_agent_rejected_for_agent_step PASSED |
| AC-2 | record() with agent="developer" writes step_history[-1].agent == "developer" | PASS | test_agent_recorded_from_payload PASSED |
| AC-3 | record() with agent_id populates output_tokens and model from orc-30 JSONL | PASS | test_jsonl_enrichment_fires_with_agent_id PASSED (JSONL found on disk) |
| AC-4 | SKILL.md template includes `agent` and `agent_id` | PASS | grep -n "agent.*agent_id" → line 229 confirmed |
| AC-5 | SKILL.md contains usage-capture step for agentId extraction from Task result | FAIL | spec verify: `grep -E "agentId.*Task.*result|extract.*agentId"` returns NO MATCHES. Line 211 reads "the result text contains a line `agentId: <17hex>`. Extract that" (newline between "Extract" and "agentId"); line 219 has "Task result text (agentId:" — reversed word order. Neither phrase matches either branch of the pattern. |
| AC-6 | record() with no `agent` for inline-script contract defaults to inline without error | PASS | test_inline_step_no_agent_required PASSED |
| AC-7 | DuckDB query after real workflow shows agent_name != 'inline', output_tokens > 0, model != '__default__' | FAIL | T-4 smoke run shows no captured query output in implementation notes. Manual verification not evidenced. |

---

## Dimension Scores

| Dimension | Score | Notes |
|---|---|---|
| Spec Compliance | 7/10 | 2 ACs fail their stated verify commands (AC-5, AC-7) |
| Correctness | 10/10 | record.py Check B logic correct; dataclass attribute access; fallback on contract errors; token check preserved |
| Security | 10/10 | No injection surfaces |
| Simplicity | 10/10 | Minimal delta reusing existing contract-load infrastructure |
| Code Quality | 10/10 | Follows existing patterns; tests use established fixture patterns; no dead code |
| **Overall** | **8.5/10** | Capped below 9 by two unmet ACs |

---

## Critical Issues (must fix before advancing)

None that are structural bugs. The code is correct; the AC-5 failure is a SKILL.md wording issue.

## Important Issues (must fix)

### Issue 1: AC-5 grep verify fails

**File:** `skills/orchestrate/SKILL.md` around line 211
**Spec verify:** `grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md`
**Result:** 0 matches

The comment at line 211 reads (paraphrased):
```
tool, the result text contains a line `agentId: <17hex>`. Extract that
hex value...
```
"Extract" and "agentId" are on different lines. The example at line 219 has the order reversed: "Task result text (agentId:..." not "agentId.*Task.*result".

**Fix:** Add a single line that satisfies `extract.*agentId` (case-sensitive). For example, change the opening of the comment block to read:
```
# 4. MANDATORY: extract agentId from the Task tool result text.
```
This makes the pattern `extract.*agentId` match. The substance of the instruction can remain as-is.

**Verify:** `grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md` returns at least one match.

### Issue 2: AC-7 smoke evidence not captured

**Task:** T-4 (end-to-end smoke verification)
**Expected:** Implementation notes in T-4 contain actual DuckDB query output showing `agent_name != 'inline'`, `output_tokens > 0`, `model != '__default__'` for a real workflow run.
**Found:** No captured evidence in implementation notes.

**Fix:** Either:
(a) Run a real smoke workflow on `orc-48-smoke` branch and capture the DuckDB query output in T-4's implementation notes, OR
(b) Explicitly defer to feature signoff with documented rationale (acceptable if the infrastructure is proven by AC-3 and T-4 is understood to be an integration gate, not a unit gate).

**Recommendation:** Option (b) is acceptable. AC-3's JSONL enrichment test proves the engine code path. T-4 verifies the full driver→engine loop which requires a live agent spawn. Explicitly marking AC-7 as "deferred to feature signoff with rationale: live driver not testable in worktree unit phase" is sufficient.

---

## Fix Tasks

### T-5: Fix AC-5 grep pattern — reword SKILL.md agentId extraction line

**Type:** fix  
**Files:** `skills/orchestrate/SKILL.md`

**Description:**
Add or modify a line in the usage-capture comment block (around line 210-219) so that `grep -E "extract.*agentId"` or `grep -E "agentId.*Task.*result"` returns at least one match.

Minimal change: change the comment opening from
```
# 4. MANDATORY: AGENT IDENTITY CAPTURE — when spawning an agent via the Task
#    tool, the result text contains a line `agentId: <17hex>`. Extract that
```
to
```
# 4. MANDATORY: extract agentId from the Task tool result text. When spawning
#    an agent via the Task tool, the result contains a line `agentId: <17hex>`.
```

**Verify:**
- `grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md` returns at least one match
- All 4 test_record_agent_field.py tests still pass
- `grep -n "agent.*agent_id" skills/orchestrate/SKILL.md` still returns line 229 (AC-4 unaffected)

### T-6 (optional): Capture T-4 smoke evidence or document deferral

**Type:** verification  
**Files:** `spec/changes/orc-48/tasks.md` (implementation notes for T-4)

**Description:**  
Either run a live smoke workflow and capture DuckDB query output in T-4 implementation notes, or add an explicit note documenting that AC-7 is deferred to feature signoff with rationale.

**Verify:** T-4 implementation notes contain either (a) DuckDB query output with non-inline rows, or (b) explicit deferral note.

---

## What Is Solid

- record.py Check B guard: correct placement (before `agent = payload.get(...)`), correct conditions (status == completed AND contract_agent AND contract_agent != "inline" AND "agent" not in payload), correct error shape
- ContractError fallback: widened exception catch is correct and logs to stderr
- `contract.agent` attribute access is valid (StepContract dataclass declares `agent: str | None`)
- T-1 regression tests are well-structured: isolated contracts dir via env var, proper state.yaml fixture, tests all 4 code paths
- Pre-existing test_seed_state failure is unrelated and predates this branch

---

## Verdict: NEEDS WORK

Score: 8.5/10 (below min_phase_review_score of 9)

AC-5 fails its spec-defined verify command. This is the only code change required (a one-line SKILL.md wording tweak). Fix T-5 first, then re-run phase review.

---

# Re-Review: ORC-48 — Post-T-5/T-6 Fix Pass

**Re-reviewed:** 2026-05-10
**Reviewer:** Reviewer Agent (claude-sonnet-4-6)
**Trigger:** T-5 (SKILL.md AC-5 wording fix) + T-6 (T-4 deferral note) applied in commit dc8651e

---

## Re-Verification

### Tests

```
1 failed, 380 passed, 28 warnings
Pre-existing failure: test_seed_state.py::test_seed_state_produces_dispatch_ready_pair (also fails on main — unrelated to ORC-48)
ORC-48 tests (test_record_agent_field.py): 4/4 PASSED
```

### Commits since main

```
dc8651e fix(orc-48): T-5/T-6 reword SKILL.md agentId comment + T-4 deferral note
25f2c55 fix(orc-48): T-2 fix check ordering and stale hint line number
0928e1e fix(orc-48): T-3 add agent and agent_id to done payload template in SKILL.md
241b3e6 fix(orc-48): T-1+T-2 regression tests + Check B agent guard
```

---

## AC Re-Verification

| AC | Criterion | Status | Evidence |
|---|---|---|---|
| AC-1 | record() rejects missing `agent` for agent-step → (error, exit_code=3) | PASS | test_missing_agent_rejected_for_agent_step PASSED |
| AC-2 | record() with agent="developer" writes step_history[-1].agent == "developer" | PASS | test_agent_recorded_from_payload PASSED |
| AC-3 | record() with agent_id populates output_tokens and model from orc-30 JSONL | PASS | test_jsonl_enrichment_fires_with_agent_id PASSED |
| AC-4 | SKILL.md template includes `agent` and `agent_id` | PASS | `grep -n "agent.*agent_id"` → line 229 confirmed |
| AC-5 | SKILL.md contains usage-capture step for agentId extraction | PASS | `grep -E "extract.*agentId"` → line 211: "tool, extract agentId from the Task tool result text" |
| AC-6 | record() with no `agent` for inline-script contract defaults to inline without error | PASS | test_inline_step_no_agent_required PASSED |
| AC-7 | DuckDB query after real workflow shows agent_name != 'inline', output_tokens > 0, model != '__default__' | DEFERRED | Explicit deferral note in T-4: blocked on global SKILL.md sync; engine path verified by AC-3 unit test (JSONL enrichment, output_tokens > 0, model = claude-sonnet-4-6). Deferred to feature signoff after global sync. |

### AC-5 detail

Previous failure: "Extract" and "agentId" were on separate lines; neither grep branch matched.

Fix applied (commit dc8651e, SKILL.md line 211):
```
# 4. MANDATORY: AGENT IDENTITY CAPTURE — when spawning an agent via the Task
#    tool, extract agentId from the Task tool result text (it contains a line
```
`grep -E "extract.*agentId"` now matches on: `"tool, extract agentId from the Task tool result text"` — confirmed.

### AC-7 deferral assessment

The deferral is substantively justified:
- AC-3 proves the engine enrichment code path (JSONL lookup → output_tokens + model populated).
- The only gap is the driver not yet passing `agent`/`agent_id` because the global SKILL.md has not been synced.
- A live smoke run before the sync would produce `inline` rows, which would be a false negative — not evidence of a bug.
- Deferring to feature signoff (after global sync) is the correct gate.

---

## Dimension Scores

| Dimension | Score | Notes |
|---|---|---|
| Spec Compliance | 9/10 | All ACs pass or have explicit justified deferral; AC-7 deferred to signoff with engine-level proof |
| Correctness | 10/10 | record.py Check B logic correct; dataclass attribute access valid; fallback on contract errors; token check preserved |
| Security | 10/10 | No injection surfaces |
| Simplicity | 10/10 | Minimal delta; no over-engineering |
| Code Quality | 10/10 | Follows existing patterns; tests well-structured; no dead code |
| **Overall** | **9/10** | AC-5 now verified. AC-7 deferred with documented rationale and engine-level evidence — meets the minimum bar. |

---

## Issues Resolved

- T-5: AC-5 grep verify now passes. `grep -E "extract.*agentId"` returns a match on line 211.
- T-6: T-4 deferral note explicitly states rationale (global SKILL.md sync blocked; engine path verified by unit test).

## Remaining Notes

- The pre-existing `test_seed_state` failure (also fails on `main`) is out of scope and does not affect this phase.
- AC-7 remains a feature signoff gate — the global `~/.claude/skills/orchestrate/SKILL.md` must be synced before a live smoke run will produce meaningful results.

---

## Verdict: PASS

Score: 9/10 (spec: 9, correctness: 10, security: 10, simplicity: 10, quality: 10)

All tasks complete. AC-5 now verified by grep match. AC-7 deferred with explicit rationale and engine-level proof. No structural issues remain. Phase is ready to advance.
