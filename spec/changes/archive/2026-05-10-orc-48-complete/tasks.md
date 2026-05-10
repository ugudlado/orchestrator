# Tasks: ORC-48-complete — AC verification for phase-review

Two small tasks to close phase-review findings from ORC-48.

---

## T-1: Fix AC-5 grep pattern — reword SKILL.md agentId extraction line

**Type:** fix
**Files:** `skills/orchestrate/SKILL.md`

**Description:**

Phase-review flagged that line 211-219 does not match the verify grep pattern:
```bash
grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md
```

Reword the usage-capture block (around line 210-219) to put "extract agentId" on the same line, or reorder to match the pattern.

**Current text** (approximately):
```
# 4. MANDATORY: AGENT IDENTITY CAPTURE — when spawning an agent via the Task
#    tool, the result text contains a line `agentId: <17hex>`. Extract that
#    hex value...
```

**Proposed reword**:
```
# 4. MANDATORY: extract agentId from the Task tool result text. When spawning
#    an agent via the Task tool, the result contains a line `agentId: <17hex>`.
```

This puts "extract agentId" on the same line, matching the pattern `extract.*agentId`.

**Verify:**
```bash
grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md
```
Must return at least 1 match (non-zero exit code 0).

---

## T-2: AC-7 smoke evidence or deferral note

**Type:** verification  
**Files:** `spec/changes/orc-48-complete/tasks.md` (implementation notes for T-2)

**Description:**

Phase-review noted that T-4 in the original ORC-48 tasks.md describes end-to-end smoke verification but no evidence was captured.

**Option A** (preferred if feasible): Run a real smoke workflow on a test change ID (e.g. `orc-48-smoke`), wait for at least one agent step to complete, then query DuckDB and capture the output. Add the output to these implementation notes.

**Option B** (acceptable): Add explicit deferral note to T-4 in the original `spec/changes/archive/2026-05-10-orc-48/tasks.md` (or in implementation notes here) documenting that AC-7 is deferred to feature signoff because:
- T-4 requires a live agent spawn (cannot be mocked in unit tests)
- AC-3 (JSONL enrichment test) already proves the engine code path is correct
- Full driver→engine loop requires real workflow execution, which is an integration gate, not a unit gate

**Verify:** Either:
- (A) DuckDB query output in these notes showing `agent_name != 'inline'`, `output_tokens > 0`, `model != '__default__'`, OR
- (B) Explicit deferral note in implementation notes explaining why AC-7 is deferred

**Recommendation:** Option B is acceptable for phase review closure. The code is proven correct by AC-1 through AC-6; AC-7 is a full integration gate that belongs to feature signoff, not bugfix phase review.

---

## Implementation Notes

### T-1 status (AC-5)

Grep pattern already matches as of current SKILL.md. Line 211 reads:

```
#    tool, extract agentId from the Task tool result text (it contains a line
```

This matches the `extract.*agentId` branch of the pattern. No edit required.

Verified: `grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md` exits 0 with one match.

### T-2 deferral (AC-7)

**AC-7 deferred to feature signoff.**

Reason: T-4 end-to-end smoke requires a live agent spawn so that `orchestrator done` runs with the new `agent`/`agent_id` payload and writes to DuckDB. This cannot be exercised in the worktree unit phase without spinning up a real workflow and waiting for a sub-agent to complete.

Why deferral is safe:
- AC-1 through AC-6 are proven by the four unit tests in T-1 (record.py) and T-2/T-3 (JSONL enrichment + step_events schema).
- T-1 case 3 specifically confirms the JSONL enrichment path fires and populates `output_tokens > 0` and `model = claude-sonnet-4-6` from real JSONL.
- The deferral note in the archived `spec/changes/archive/2026-05-10-orc-48/tasks.md` T-4 (lines 168–172) captures this rationale: *"Full end-to-end DuckDB smoke is blocked on global SKILL.md sync... AC-7 deferred to feature signoff after global sync."*
- AC-7 integration verification will run at the next live workflow execution once the global SKILL.md is synced.
