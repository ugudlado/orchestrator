## Phase Review: ORC-75 — Fix run-learn-cycle step contract mismatch + abandoned re-dispatch loop

### Verification

- Type-check: N/A (Python project, no static type checker invocation in project)
- Tests: 484 passed, 5 failed (all 5 pre-existing; 0 new failures). New regression suite test_record_abandoned_node.py: 3/3 passed.
- Build: N/A (no build step)
- Task completeness: All 7 tasks [x]. No unchecked tasks remain.

### Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| Spec Compliance | 8/10 | AC-3, AC-4, AC-5 fully satisfied. AC-1, AC-2 satisfied by pivot approach; design.md not updated to document pivot. |
| Algorithm Correctness | 10/10 | abandoned flip logic is correct; rework-loop and repeat_until guards properly exclude abandoned. |
| Security | 10/10 | No new attack surface. |
| Performance | 10/10 | No performance implications. |
| Readability | 9/10 | Code is clear; record.py comment block accurately describes the fix. |
| Simplicity | 8/10 | workflow-learner approach adds a new agent file where agent: inline would have been smaller; tradeoff is justified by correctness (the pivot avoids the inline dispatch path needing dispatch.py changes). |
| Code Quality | 9/10 | DRY maintained; skills/learn/SKILL.md correctly delegates to workflow-learner; no dead code. |
| Functional Completeness | 9/10 | Both bugs fixed; pivot is architecturally sound. |
| **Overall** | **9/10** | |

### Acceptance Criteria Verification

| AC | Criterion | Status | Evidence |
|----|-----------|--------|----------|
| AC-1 | run-learn-cycle dispatches with agent: "workflow-learner" | PASS | `grep agent: ~/.config/orchestrator/config/steps/run-learn-cycle.yaml` → `agent: workflow-learner` |
| AC-2 | orchestrator done with status: completed, agent: "workflow-learner" + real usage → accepted | PASS | record.py lines 1399/1420 check `contract_agent != "inline"` and `agent != "inline"`. workflow-learner is a real spawned agent producing real usage + agentId. Guard passes for real spawned completions. |
| AC-3 | abandoned record for in_progress node → node flips to completed, state.status = blocked | PASS | test_abandoned_flips_node_to_completed + test_abandoned_sets_state_status_blocked both pass |
| AC-4 | After abandoned record, orchestrator next does NOT re-dispatch the same step | PASS | test_abandoned_node_is_not_ready_for_redispatch passes; logic verified at record.py lines 1597-1617 |
| AC-5 | Existing steps unchanged — no regressions | PASS | 481 previously passing tests still pass; 5 pre-existing failures unchanged |

### Key Findings

**Fix 1 — abandoned node flip (T-1, T-2):** Implemented correctly in `record.py` at lines 1597-1617. The `abandoned` status now enters the node-flip branch, the rework-loop and repeat_until guards are correctly scoped to `completed`/`recovered` only, and `abandoned` falls through to the `else` clause that marks the node `completed`. Three regression tests confirm the fix.

**Fix 2 — run-learn-cycle agent pivot (T-3 through T-5):** The developer pivoted from the design's selected `agent: inline` approach (Approach 3) to a new approach: creating `agents/workflow-learner.md` as a full standalone agent that runs the learn pipeline itself. This is architecturally valid — `workflow-learner` does not call `/learn` (the skill that spawns sub-agents); it embeds the full pipeline logic directly. The design's rejection of Approach 2 was about "spawn thin agent → call /learn → /learn spawns sub-agents"; the actual implementation avoids that chain. The pivot is sound.

**Documentation gap:** `design.md` was not updated after the pivot. It still declares Approach 3 (`agent: inline`) as selected and explicitly rejects Approach 2, while the implementation chose an approach not described in design.md. This is a documentation debt but does not affect correctness.

**Minor concern — workflow-learner tool list:** `agents/workflow-learner.md` frontmatter lists `tools: ["Read", "Write", "Edit", "Grep", "Glob", "Bash", "WebSearch", ...]` but the body says to "spawn workflow-improver" for `workflow_improvement` findings. Since `workflow-improver` is invoked via CLI/Bash (not via Claude's `Task()` tool), the tool list is sufficient for actual execution. However, the instruction language "spawn" is ambiguous — it should say "invoke via Bash/CLI" to be precise.

**No test for AC-1 dispatch shape:** There is no automated test that calls `orchestrator next` with a `run-learn-cycle` step and asserts the output JSON carries `"agent": "workflow-learner"`. This is acceptable for this phase given the change is a one-line YAML edit that's directly verifiable by inspection, but it's a coverage gap.

### Critical Issues

None. Both fixes are correctly implemented with no regressions.

### Important Issues

None blocking.

### Minor Issues (non-blocking)

1. `design.md` was not updated to document the pivot from `agent: inline` to `agent: workflow-learner`. Future maintainers reading design.md will see a contradiction between the selected approach and what's in the codebase.
2. `agents/workflow-learner.md` uses the word "spawn" for invoking `workflow-improver` but lacks the `Task` tool. Clarify that invocation is via Bash/CLI to prevent confusion.

### Verdict: PASS (overall 9/10)

Both bugs are correctly fixed. Tests pass. No regressions. The documentation gap (design.md not updated) is non-blocking for this phase.
