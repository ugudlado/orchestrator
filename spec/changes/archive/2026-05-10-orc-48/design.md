# Design: ORC-48 — Agent spawn usage flowing into DuckDB metrics

## Selected approach

**Driver passes `agent` and `agent_id`; engine validates and enriches.**

Two coordinated changes:

1. **`skills/orchestrate/SKILL.md`** — update the `done` payload template
   (line 210) and add an explicit step instructing the driver to extract
   `agentId` from the Task tool result text.

2. **`config/scripts/orchestrator_next/record.py`** — strengthen Check B
   so that a payload missing `agent` for a step whose contract declares
   `agent:` is rejected with a clear error. The step contract is already
   loaded earlier in `done` for output validation; `contract.agent` is
   directly accessible.

The existing JSONL enrichment block (`record.py` lines 1143-1177) is
already written to consume `agent_id` from the payload — no change needed
there. Once the driver actually supplies `agent_id`, enrichment fires and
populates `output_tokens`, `model`, and cache token counts from the
on-disk subagent JSONL.

## Why this approach

- **Uses infrastructure already in place.** `extract_agent_usage()`,
  `_locate_subagent_jsonl()`, and the JSONL enrichment block were built
  for exactly this purpose; the bug is that the input (`agent_id`)
  never arrives. Fixing the source is simpler than building a parallel
  correlation path.
- **Authoritative data.** The Task tool result explicitly emits
  `agentId: <17hex>` in its body — verified by inspecting
  `~/.claude/projects/-Users-spidey-code-orchestrator/eb954b82-99cf-4001-8369-e49e14af7299.jsonl`
  (a recent driver JSONL contained a tool_result with this exact text).
  No window-correlation guessing needed.
- **Engine guard catches future regressions.** Even if a future SKILL.md
  edit drops the field again, Check B will fail loudly instead of silently
  defaulting to `inline`.

## Component breakdown

### Component 1 — SKILL.md template fix

**File:** `/Users/spidey/code/feature_worktrees/orc-48/skills/orchestrate/SKILL.md`
(canonical, plus mirror at `/Users/spidey/.claude/skills/orchestrate/SKILL.md` if
present in the user environment — the worktree copy is the source of truth that
the orchestrator dispatches against; the global mirror is updated by the global
sync script).

**Change A — line 210 template:**

Before:
```
orchestrator done state.yaml <<< {step_id, phase, status, outputs, usage, evidence}
```

After:
```
orchestrator done state.yaml <<< {step_id, phase, status, agent, agent_id, outputs, usage, evidence}
```

**Change B — usage capture step (around lines 193-208):**

Add a new sub-step instructing the driver to capture `agent_id` from the
Task tool result. The Task tool returns text like:

```
Async agent launched successfully.
agentId: <17hex> (internal ID - do not mention to user...)
```

Add to the usage-capture block:

```
# 4. MANDATORY: AGENT IDENTITY CAPTURE — when spawning an agent via the Task
#    tool, the result text contains a line `agentId: <17hex>`. Extract that
#    hex value (the JSONL filename stem) and pass it as `agent_id` in the
#    done payload, alongside the agent role from `action.agent` (returned by
#    `orchestrator next`) which goes in the `agent` field.
#
#    Example payload:
#      {"step_id": "...", "phase": "...", "status": "completed",
#       "agent": "developer",            # from action.agent
#       "agent_id": "a6e7ca188209d1f47", # from Task result text
#       "outputs": {...}, "usage": {...}, "evidence": {...}}
#
#    For inline-script steps (action has `run:` instead of `agent:`), omit
#    both fields — record.py defaults to agent="inline".
```

### Component 2 — record.py Check B strengthening

**File:** `/Users/spidey/code/feature_worktrees/orc-48/config/scripts/orchestrator_next/record.py`
**Location:** lines 1070-1092 (Check B)

The existing check uses `agent = payload.get("agent", "inline")` and only
guards token presence when `agent != "inline"`. When the payload omits
`agent`, the guard is silently bypassed.

**Approach:** load the step contract (already loaded later in the function
for output validation — move that load earlier or duplicate the small
`_load_step_contract` call), then assert:

```python
contract = _load_step_contract(step_id)  # may already be loaded; reuse
contract_agent = (contract or {}).get("agent")
payload_has_agent = "agent" in payload

if status == "completed" and contract_agent and contract_agent != "inline":
    if not payload_has_agent:
        return (
            {
                "reason": "payload_missing_agent_for_agent_step",
                "step_id": step_id,
                "expected_agent": contract_agent,
                "hint": (
                    "step contract declares agent: %s but payload omitted "
                    "the 'agent' field. The driver must include agent and "
                    "agent_id (extracted from the Task result text) in the "
                    "done payload. See skills/orchestrate/SKILL.md line ~210."
                ) % contract_agent,
            },
            3,
        )
    # Once agent is present, the existing token check still applies.
```

The existing `has_tokens` check (lines 1078-1092) remains, but is now
guaranteed to actually run for agent steps because `agent` is bound to the
real value from the payload.

**Notes on contract loading:**
- The `_load_step_contract` helper is the same one used by output
  validation later in `done`. Verify the exact symbol name during
  implementation; if loading is expensive, cache the result in a local
  variable and pass it forward.
- If the contract file is missing (e.g. legacy step ID no longer in the
  catalog), fall back to current behavior (allow `agent` to default to
  `inline`) — do not block recording on a contract lookup miss. Log to
  stderr instead.

### Component 3 — JSONL enrichment (no change required)

`record.py` lines 1143-1177 already handle:
- Reading `agent_id` from `payload["agent_id"]` or `usage["agent_id"]`.
- Calling `extract_agent_usage(repo_root, agent_id)` to read the subagent
  JSONL.
- Overwriting `usage["input_tokens"|"output_tokens"|"cache_*"|"model"|"turns"]`
  with JSONL values.
- Persisting `usage["agent_id"]` so `upsert.py` writes the column.

This block fires automatically once Component 1 ensures `agent_id` is in
the payload.

## Data flow (after fix)

```
orchestrator next
   └─→ returns {agent: "developer", instruction: ..., ...}

driver (LLM via SKILL.md):
   ├─→ spawn Task(subagent_type="developer", prompt=...)
   ├─→ Task result text contains: "agentId: a6e7ca188209d1f47"
   ├─→ extract: agent_id = "a6e7ca188209d1f47"
   ├─→ collect usage from result <usage> block
   └─→ orchestrator done <<< {
           step_id, phase, status: "completed",
           agent: "developer",        # from action.agent
           agent_id: "a6e7ca188209d1f47",  # from Task result text
           outputs, usage, evidence
       }

record.py (orchestrator done):
   ├─→ Check B: contract.agent="developer", payload.agent="developer" → pass
   ├─→ Check B: usage tokens present → pass
   ├─→ JSONL enrichment block: agent_id present → fires
   │   └─→ extract_agent_usage(repo_root, "a6e7ca188209d1f47")
   │       reads ~/.claude/projects/<slug>/<session>/subagents/agent-a6e7ca188209d1f47.jsonl
   │       returns {input_tokens, output_tokens, model, cache_*, turns}
   ├─→ usage now has authoritative output_tokens and model
   ├─→ entry["agent"] = "developer" (not "inline")
   └─→ upsert_step_event writes correct row to step_events

DuckDB:
   step_events.agent_name = "developer"
   step_events.output_tokens = 4548 (real value from JSONL)
   step_events.model = "claude-sonnet-4-6"
   agent_report aggregates per-agent rows correctly
```

## Error handling

- **Payload omits `agent` for an agent step** → Check B returns exit 3,
  driver halts the workflow with an actionable error pointing at SKILL.md.
- **Payload includes `agent` but omits `agent_id`** → Check B passes
  (agent identity is recorded), JSONL enrichment is skipped, existing
  token check on `usage.input_tokens > 0 OR usage.output_tokens > 0`
  still applies. Result: agent name correct, JSONL-sourced model and
  output_tokens may be missing if the driver didn't capture them from the
  result `<usage>` block. Logged but not fatal — degrades gracefully.
- **`agent_id` provided but JSONL missing on disk** → existing
  enrichment block already wraps in `try/except` and logs to stderr; no
  change.
- **Step contract file missing** → fall back to current behavior; log a
  warning. Do not block recording.

## Verification of design assumptions

| Assumption | How verified |
|---|---|
| Driver receives `action.agent` from `orchestrator next` | `dispatch.py:376` returns `"agent": contract.agent` in JSON action. |
| Task tool result exposes `agent_id` | Inspected `~/.claude/projects/-Users-spidey-code-orchestrator/eb954b82-...jsonl`; tool_result text begins `Async agent launched successfully.\nagentId: add15d9599de8615a`. |
| Step contract YAML has `agent:` field | Confirmed in `config/steps/diagnose.yaml` line 5: `agent: discoverer`. |
| JSONL enrichment block already gated only on `agent_id` | `record.py:1143-1177` reads `payload.get("agent_id") or usage.get("agent_id")`. |
| `meta.json` sidecar exists per subagent | Confirmed: `~/.claude/projects/.../subagents/agent-*.meta.json` contains `{"agentType": "...", "description": "..."}`. Not required by this fix but documents that engine-side fallback (Alt A/B) would also have data available. |

## Parallelism note (assumption)

Today's dispatch loop is strictly sequential per driver session — one
agent runs at a time. The design relies on the driver capturing
`agent_id` from the immediate Task result of the spawn it just initiated;
no time-window correlation is involved. If future work introduces
parallel agent spawns within one driver session, this design still works
because `agent_id` uniquely identifies the JSONL file regardless of
overlap. [ASSUMPTION — sequential dispatch verified by reading
`skills/orchestrate/SKILL.md` lines 180-210, which describe a single
spawn-collect-record cycle per step.]
