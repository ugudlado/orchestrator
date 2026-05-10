# Spec: ORC-48-complete — Fix phase-review ACs for agent metrics fix

## Summary

ORC-48 successfully implemented the agent metrics fix (record.py + SKILL.md changes) and all 4 unit tests passed. However, phase review flagged two ACs as unverified:

- **AC-5**: SKILL.md wording doesn't match grep pattern for `agentId` extraction
- **AC-7**: T-4 smoke test evidence (DuckDB query output) not captured

## Requirements

### AC-5: SKILL.md grep pattern match

**Current state**: Line 211-219 in skills/orchestrate/SKILL.md describe agentId extraction but the exact wording does not match the verify command.

**Verify command** (from phase-review):
```bash
grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md
```

**Expected**: At least one match in the grep output.

**What changed** (from ORC-48 phase-review):
- Problem: "Extract" and "agentId" are on different lines (line 211-212 split)
- Solution: Reword to put "extract agentId" on the same line, or reorder to "agentId.*Task.*result" pattern

**Acceptance**: Grep command returns at least 1 match.

### AC-7: T-4 smoke evidence

**Current state**: T-4 (end-to-end smoke verification) in tasks.md describes running a live workflow and querying DuckDB, but no evidence was captured in implementation notes during ORC-48.

**Expected evidence**: DuckDB query output showing:
- At least one row with `agent_name != 'inline'` (e.g. `discoverer`)
- That row has `output_tokens > 0`
- That row has `model != '__default__'` (e.g. `claude-sonnet-4-6`)

**Option 1 (preferred)**: Run a live smoke workflow (e.g. `orc-48-smoke`) and capture the DuckDB query output.

**Option 2** (acceptable): Add explicit deferral note to T-4 in tasks.md documenting that AC-7 is deferred to feature signoff because T-4 requires a live agent spawn that cannot be tested in worktree unit phase.

## Non-requirements

- No code changes to record.py or implementation logic
- No re-running of unit tests (they pass)
- No re-diagnosis (diagnosis.md from ORC-48 is final)
