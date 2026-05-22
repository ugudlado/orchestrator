# Diagnosis: ORC-75 — run-learn-cycle Step Contract Mismatch

## Summary

`run-learn-cycle.yaml` declares `agent: workflow-improver`, but its instruction
says to invoke `/learn` inline (a skill that runs in the driver conversation, not
as a spawned subagent). This creates a three-way deadlock: every valid status
results in either a `record.py` rejection or an infinite re-dispatch loop.

---

## Reproduction

```bash
# Simulates the exact payload the driver produces after running /learn inline
# against a state.yaml that has run-learn-cycle as the current step.

STATE_YAML="<path-to-active-state.yaml>"

echo '{
  "step_id": "run-learn-cycle",
  "phase": "main",
  "status": "completed",
  "agent": "workflow-improver",
  "outputs": {"learn_result": "ok"}
}' | orchestrator done "$STATE_YAML"
# → exit 3, reason: agent_step_missing_usage
# record.py rejects: agent step must carry usage tokens or agent_task_result
# with an agentId line. Inline /learn produces neither.

# Alternative 1: status:blocked — also rejected
echo '{
  "step_id": "run-learn-cycle",
  "phase": "main",
  "status": "blocked",
  "agent": "workflow-improver",
  "outputs": {"learn_result": "ok"}
}' | orchestrator done "$STATE_YAML"
# → exit 3, reason: invalid_status
# Valid statuses are only: completed, recovered, abandoned

# Alternative 2: status:abandoned — accepted but loops
echo '{
  "step_id": "run-learn-cycle",
  "phase": "main",
  "status": "abandoned",
  "agent": "workflow-improver",
  "outputs": {"learn_result": "ok"}
}' | orchestrator done "$STATE_YAML"
# → exit 0, BUT:
# - record.py sets state_raw["status"] = "blocked" (line 1580)
# - The node-status flip block (lines 1597–1617) only runs for "completed"
#   or "recovered", so the run-learn-cycle workflow_plan node stays
#   "in_progress"
# - _compute_next_step calls readiness.next_ready_node, which treats
#   "in_progress" as ready (is_node_ready only excludes "completed"),
#   so next_step stays {"step_id": "run-learn-cycle"}
# → orchestrator next re-dispatches run-learn-cycle; infinite loop
```

**Evidence from orc-67** — the exact failure occurred in production:
`spec/changes/archive/2026-05-22-orc-67/state.yaml`, lines 1519–1559.
The step_history entry shows `status: abandoned` with a `blocker` note
documenting the contract mismatch; the node in `workflow_plan.main.nodes`
is left as `status: in_progress` (line 322).

---

## Root Cause

### Bug site 1 — Wrong dispatch type in `run-learn-cycle.yaml`

**File:** `/Users/spidey/.config/orchestrator/config/steps/run-learn-cycle.yaml`  
**Line 6:** `agent: workflow-improver`

The step declares `agent:`, which triggers the agent-spawn code path in
`dispatch.py` (line 327) and the agent-step usage guard in `record.py`
(lines 1399–1454). But the instruction (lines 21–47) says:
`Invoke /learn with the change ID: Skill({ skill: "learn", args: "<CHANGE_ID>" })`

`/learn` is a skill that executes in the driver's own conversation context.
It spawns sub-agents internally (workflow-evaluator, workflow-improver) but
is not itself a spawned agent. The driver never calls `Task(agent=…)` for
this step — it calls `Skill(…)`. No `agentId:` line is emitted into the
Task tool result, and no subagent JSONL is written under the driver session.

### Bug site 2 — `record.py` agent-step usage guard rejects completion

**File:** `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`  
**Lines 1399–1454** (`record()` function)

```python
contract_agent = contract.agent if contract is not None else None
if status == "completed" and contract_agent and contract_agent != "inline":
    if "agent" not in payload:
        return ({"reason": "payload_missing_agent_for_agent_step", ...}, 3)

agent = payload.get("agent", "inline")
...
if status == "completed" and agent != "inline":
    has_tokens = (input_tokens > 0 or output_tokens > 0)
    if not has_tokens:
        if agent_task_result and resolved_agent_id:
            pass  # JSONL enrichment path
        elif agent_task_result:
            return ({"reason": "agent_step_missing_usage", "hint": "no agentId line"}, 3)
        else:
            return ({"reason": "agent_step_missing_usage", "hint": "..."}, 3)
```

An inline `/learn` invocation provides none of the three escape hatches:
- No `agent_task_result` with an `agentId:` line (no Task tool call was made)
- No `usage.input_tokens > 0` or `usage.output_tokens > 0` (inline, not billed to a subagent)

Result: `status:completed` → exit 3, `reason: agent_step_missing_usage`.

### Bug site 3 — `status:blocked` is not a valid done-payload status

**File:** `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`  
**Line 1343:**

```python
_VALID_STATUSES = {"completed", "recovered", "abandoned"}
```

`blocked` is a dispatch exit code (exit 2), not a record status. The driver
cannot honestly report `status:blocked` to `orchestrator done`.

### Bug site 4 — `status:abandoned` accepted but leaves node in `in_progress`, causing infinite re-dispatch

**File:** `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`  
**Lines 1578–1617**

The `abandoned` branch only sets `state_raw["status"] = "blocked"` (line 1580).
The node-status flip block (lines 1597–1617) is gated on
`if status in ("completed", "recovered")` — `abandoned` is excluded, so the
`run-learn-cycle` DAG node stays `in_progress`.

`readiness.is_node_ready` (line 75):
```python
if node.get("status") == "completed":
    return False
```

Only `completed` is excluded. An `in_progress` node passes the check and is
re-emitted by `next_ready_node`. The driver dispatches the same step again.

---

## Execution Path From Trigger to Failure

```
orchestrate skill
  → orchestrator next → dispatch.py → contract.agent="workflow-improver"
      → driver spawns... no, driver calls Skill("learn") inline
  → /learn runs in driver context (no subagent, no agentId)
  → driver calls orchestrator done with status:completed, agent:workflow-improver
      → record.py line 1420: agent != "inline", no tokens, no agentId
      → exit 3: agent_step_missing_usage
  → driver retries with status:abandoned
      → record.py line 1343: accepted
      → line 1580: state.status = "blocked"
      → node NOT flipped to completed (lines 1597–1617 skip abandoned)
      → _compute_next_step returns run-learn-cycle again
      → state.next_step = {step_id: run-learn-cycle}
  → orchestrator next re-dispatches run-learn-cycle
  → loop
```

---

## Impact Assessment

### Direct callers affected

- Every workflow that includes `run-learn-cycle` in its `workflow_plan` — currently all feature and bugfix schemas via the `_complete` phase template.
- `orchestrator next` re-dispatches the same step whenever an `abandoned` record is written for an `in_progress` node (this affects any step that hits `abandoned`, not just `run-learn-cycle`).

### Secondary impact: `abandoned` loop applies beyond `run-learn-cycle`

The node-not-flipped behavior is general: any `abandoned` record for a node
that is currently `in_progress` will cause an infinite re-dispatch. This is a
latent bug in `record.py` independent of the `run-learn-cycle` mismatch.

### Existing tests

```bash
grep -r "run-learn-cycle\|abandoned" \
  /Users/spidey/code/orchestrator/config/tests/ 2>/dev/null
```

No tests currently exercise the `abandoned` + loop case or
`run-learn-cycle` dispatch behavior. The gap in coverage allowed both bugs
to reach production undetected.

### Past workaround (orc-67)

orc-67 wrote `status:abandoned` to unblock, then completed the remaining
steps (compute-swe-metrics, archive-completed-change, remove-worktree)
manually outside the engine. The learning artifact was already written to
disk before the record step, so learning itself was not lost.

---

## Fix Options

Two independent fixes are required:

### Fix 1 (primary): Convert `run-learn-cycle.yaml` to a `run:` step

Change `agent: workflow-improver` → `run: scripts/inline/run-learn-cycle.sh`
(or equivalent inline mechanism). The driver invokes the `/learn` skill; no
subagent billing is produced; `record.py` treats it as an inline step with
no usage requirement.

This is the honest fix — the instruction already says "invoke /learn", which
is an inline skill invocation, not an agent spawn.

**Effort:** small. One-line YAML change plus an inline script (or the driver
reads the instruction and invokes the skill directly, as other `run:`-style
steps do via prompt injection).

### Fix 2 (secondary, latent bug): `record.py` — flip `abandoned` node to `completed`

In `record.py` lines 1597–1617, include `abandoned` in the node-status flip:

```python
if status in ("completed", "recovered", "abandoned"):
    ...
    else:
        readiness.mark_node_status(state_raw, phase, step_id, "completed")
```

This prevents the infinite re-dispatch loop for `abandoned` steps. An
`abandoned` step is terminal — the engine sets `state.status = "blocked"` and
pauses. The node should be `completed` in the DAG so the driver sees exit 2
(blocked) rather than re-dispatching the same step endlessly.

**Effort:** small. Two-line change in `record.py` + a test case.

### Fix 3 (alternative to Fix 1): Rewrite instruction to actually spawn a subagent

Rewrite the instruction so it genuinely invokes `workflow-improver` as a Task
subagent, which in turn calls `/learn` from within its own context. This
produces a real `agentId` and subagent JSONL for `record.py`.

**Effort:** medium. Requires a `workflow-improver` agent definition that knows
how to invoke the `/learn` skill. The `run:` conversion (Fix 1) is simpler and
more honest given the current architecture.

---

## Recommended Fix Order

1. **Fix 1** (convert to `run:`): resolves the primary contract mismatch.
   This is the minimum viable fix — it unblocks every feature workflow.
2. **Fix 2** (abandoned node flip): resolves the latent loop bug in `record.py`.
   Prevents the same failure class from surfacing in any other step that hits
   `abandoned` while its node is `in_progress`.

Fix 3 is not recommended — it adds unnecessary subagent overhead for what is
fundamentally an inline skill invocation.

---

## Key Files

| File | Role |
|------|------|
| `/Users/spidey/.config/orchestrator/config/steps/run-learn-cycle.yaml` | Primary bug: `agent:` declaration contradicts inline instruction |
| `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py` lines 1343, 1399–1454, 1578–1617 | Validation gates that reject or loop on the mismatched step |
| `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/readiness.py` lines 66–85 | `is_node_ready` — excludes only `completed`, not `in_progress`, from re-dispatch |
| `/Users/spidey/code/orchestrator/spec/changes/archive/2026-05-22-orc-67/state.yaml` lines 1519–1559 | Production evidence of the failure in orc-67 |

---

## Key Decisions (appended by architect, design-and-draft-artifacts step)

### Decision 1: Fix 1 dispatch type is `agent: inline`, not `run: <script>`

The diagnosis's Fix 1 said "convert to a `run:` step (`run: scripts/inline/run-learn-cycle.sh`)".
On inspection that literal form is **wrong**: every existing `run:` step in
`config/steps/*.yaml` points to an executable shell script that the CLI runs
synchronously (exit 0, no JSON). `/learn` is a Claude **skill** (`user-invocable: true`,
`skills/learn/SKILL.md`) — it cannot be invoked from a shell script. It must run in
the **driver's** conversation context because it itself spawns sub-agents
(workflow-evaluator, workflow-improver) and a sub-agent cannot spawn sub-agents.

This also rules out the diagnosis's Fix 3 (spawn a `workflow-improver` agent that
calls `/learn`) — architecturally impossible for the same nesting reason.

**Chosen direction: declare `run-learn-cycle` as `agent: inline`.** The driver
executes the step's `instruction` (invoke `/learn`) in its own context and reports
`agent: "inline"` to `orchestrator done`. `record.py` already treats
`agent == "inline"` correctly — both usage guards (lines 1399, 1420) are gated on
`!= "inline"`, so an inline step has **no** usage requirement. The only engine
changes needed are: (a) `dispatch.py` must route `agent: inline` to a
driver-execute action rather than a spawn action; (b) `skills/orchestrate/SKILL.md`
needs a driver branch for that action. `agent: inline` is already a recognized
sentinel in the codebase (`dispatch.py:126`, `record.py:1416`), so this extends an
existing concept rather than inventing a new dispatch mode. This is the simplest
fix that is also honest about what the step does.

### Decision 2: Fix 2 — `abandoned` node flipped to `completed` (terminal pause)

Confirmed via `readiness.py:75` — `is_node_ready` excludes only `completed`, so an
`abandoned` node left `in_progress` is re-emitted by `next_ready_node` forever.
`record.py:1580` already sets `state.status = "blocked"` for `abandoned`. Marking
the node `completed` makes the DAG-walk skip it; the next `orchestrator next` then
exits 2 (blocked) and the driver halts for human intervention — the intended
terminal-pause behavior. `abandoned` is terminal-for-this-run, not auto-re-queued.

### Auto-selection: two-fix approach, complexity S

Both fixes are small, independent, and required (Fix 2 is a latent loop bug
affecting any `abandoned` step). No simpler single-fix option exists: Fix 1 alone
leaves the latent loop; Fix 2 alone leaves `run-learn-cycle` unable to complete.
Selected because it is the lowest-complexity option that fully resolves the
reported bug and the latent class of bug behind it.
