---
feature-id: hl-276
linear-ticket: HL-276
---

# Discovery Brief: Track per-tool-use breakdown in agent usage metrics

## Feature Summary

We currently record a single `tool_uses` integer in each step_history usage block (the total count of all tool invocations for that step). HL-276 adds a `tools:` map within that block that breaks the count down by tool type — e.g., `Read: 32, Bash: 18`. This answers the question "why did this agent use so many tokens?" without reading the full conversation transcript. The same data flows into compute-swe-metrics.sh and the JSONL telemetry log.

The underlying goal is diagnostic: when a feature is expensive, the engineer should be able to look at step_history and immediately see whether cost came from many Bash calls, heavy Read usage, or Agent spawns — not just a total number.

## Build or Reuse?

**Build** — there is no existing library or tool to reuse. The entire stack (orchestrate skill, compute-swe-metrics.sh, state.yaml schema) is internal bash/YAML. The feature extends three existing artifacts rather than introducing anything new. Extending is the correct approach; no net-new module is needed.

## Approaches Considered

### Approach A — Extend usage block in state.yaml (what the ticket asks for)

Add a `tools:` sub-map under `step_history[].usage` in state.yaml. The orchestrate dispatch loop already parses the agent result footer for `tool_uses` (SKILL.md line 109-110); extend that parsing to also count per-tool-type. Extend compute-swe-metrics.sh to aggregate these maps. Extend the JSONL write in compute-swe-metrics.sh to include a `per_agent_tool_breakdown` field.

Pros: fits exactly into the existing schema, consistent with how per_agent_tokens was added in HL-273, minimal change surface.
Cons: the dispatch loop instruction is natural language (SKILL.md lines 109-110) — the LLM that runs the orchestrator must understand what "tool_use content blocks" means in the Claude API response; this is the hardest parsing step and its exact format is undocumented in the codebase.
Effort: medium (3 touch points, parsing logic has open questions).

### Approach B — Parse from JSONL transcript post-hoc (don't change state.yaml)

At compute-swe-metrics time, read the Claude Code session JSONL transcript (if available) and count tool_use blocks there. Skip the state.yaml schema change entirely.

Pros: no change to dispatch loop, no schema migration.
Cons: the transcript file path is not captured anywhere in the codebase, the JSONL format varies across Claude Code versions, and the existing HL-275 work explicitly moved AWAY from transcript parsing to state.yaml as the canonical source (compute-swe-metrics.sh line 10-11). Reverting that decision is a regression.
Effort: medium-high, with higher fragility.

### Approach C — Add tools count to JSONL only, skip state.yaml

Write tool breakdown only to feature-metrics.jsonl (at compute-swe-metrics time) by asking the developer agent to introspect the completed steps. Skip state.yaml state contract changes.

Pros: no schema migration, no dispatch loop change.
Cons: loses per-step auditability. state.yaml is the audit trail — feature-metrics.jsonl is a derived view. If the data isn't in state.yaml, future features that read state.yaml (e.g., /learn, /telemetry) won't see it. Also inconsistent with how per_agent_tokens was already structured.
Effort: small — but wrong architectural direction.

## Recommendation

Approach A. It is the natural extension of the pattern established by HL-273 (per-agent tokens). The three touch points are well-scoped: state.yaml schema, the orchestrate SKILL.md dispatch loop instruction, and compute-swe-metrics.sh. The open questions below are the only design risks.

## Personas & Actors

**Mahesh (workflow engineer)**: runs autopilot, reviews telemetry after each cycle, wants to understand cost spikes without reading a full transcript.

**LLM orchestrator (orchestrate skill)**: the agent executing the dispatch loop in orchestrate/SKILL.md — responsible for parsing agent result footers and writing step_history.

**compute-swe-metrics.sh**: the bash script that reads step_history and produces JSONL telemetry. Must be updated to aggregate tool type maps.

**telemetry skill**: the /telemetry command that reads feature-metrics.jsonl and renders the dashboard. Currently shows `tool_calls` as an integer — could show per-tool breakdown if available.

## Use Cases

### Happy Path

UC-1: Developer agent completes execute-next-task step — after the developer agent finishes, the orchestrating LLM parses the agent result footer, counts each tool_use block by type (Read, Bash, Edit, Grep, etc.), and writes them to state.yaml under `step_history[-1].usage.tools`. The total in `tool_uses` equals the sum of all values in `tools`.

UC-2: Feature completes, compute-swe-metrics runs — the script reads all step_history entries that have a `tools:` sub-map, aggregates counts by tool type across steps, and emits both a per-agent tool breakdown and a feature-level tool breakdown in the JSONL output. The engineer opens feature-metrics.jsonl and can immediately see "developer agent: Read: 89, Bash: 45, Grep: 30".

UC-3: Old state.yaml (pre-HL-276) runs through compute-swe-metrics — entries that have `usage:` but no `tools:` sub-map are silently skipped. The script produces the same output as today for those entries. No backward compat breakage.

### Error & Edge Cases

UC-E1: Agent result footer contains no tool_use blocks (the step made zero tool calls, e.g., a pure reasoning step) — the orchestrator writes `tools: {}` or omits the `tools:` field entirely. compute-swe-metrics handles the absent key gracefully, treating it as all-zeros. The `tool_uses` field remains 0.

UC-E2: A tool type name in the result footer is unknown (Claude introduces a new tool type not in the expected set) — the aggregation still works; the new tool name becomes a new key in the `tools:` map. No hard-coded enum required.

UC-E3: The dispatch loop runs but the orchestrating agent fails to parse tool type breakdown (model regression or format change) — the `tools:` key is absent in state.yaml for that entry. compute-swe-metrics must not fail; it treats absent `tools:` as if the entry predates HL-276 (backward compat path).

## Scope

### In Scope

- Schema addition: `tools:` map under `step_history[].usage` in state.yaml.
- CONVENTIONS.md § State Field Registry: update the `step_history[].usage` row to document the new `tools:` sub-map.
- orchestrate/SKILL.md dispatch loop: extend the "parse agent result footer" instruction to also count per-tool-type and write `tools:` map.
- compute-swe-metrics.sh: aggregate `tools:` maps across steps; emit per-feature and per-agent tool breakdowns in JSONL output.
- grammar.yaml: add `tools:` to the swe_metrics schema comment block (documentation only, not enforcement).

### Out of Scope

- Telemetry dashboard (telemetry/SKILL.md): displaying tool distribution per agent type. The ticket marks this as "may be out of scope." Discovery concurs — the JSONL data is a prerequisite for any dashboard change; ship data first, display second.
- Changing tool_uses (the integer total): it stays as-is. tools: is additive.
- Backfilling historical state.yaml files: not feasible; backward-compat path handles old data.
- Filtering or capping which tool names appear in the map: accept all tool types as keys.

## UI Direction

N/A — this is a pure backend/data feature. No UI components are involved.

## Key Decisions

1. **Where does the `tools:` map sit?** Under `usage:` alongside `tool_uses:`, not as a sibling of `usage:`. This keeps all usage-derived data co-located and consistent with the existing structure.

2. **Total vs. breakdown relationship**: `tool_uses` (integer) remains the authoritative total. `tools:` is a breakdown — sum of values MUST equal `tool_uses`. This is a verifiable invariant.

3. **Emit format in JSONL**: The existing `per_agent_tokens` field in JSONL is a JSON object string (not nested YAML — see compute-swe-metrics.sh line 324). Tool breakdown should follow the same convention: a JSON object keyed by agent type, each value a map of tool -> count.

4. **No enum enforcement**: Tool type names are used as-is from the agent result. No allowlist. This future-proofs against new tool types.

5. **Design approach: Inline extension (Approach A)**. Extend the existing SKILL.md dispatch instruction (line 109-110) to also write per-tool breakdown, and extend the existing per-agent awk block in compute-swe-metrics.sh (lines 236-266) to also aggregate tool counts using SUBSEP keys. Selected via auto-selection: Approach C (XS) was disqualified for not fulfilling UC-2. Approaches A and B tied at S complexity; A won on higher reuse (extends existing awk block vs. creating a new one).

6. **OQ-1 resolved — Agent result footer format**: The SKILL.md dispatch instruction (line 109-110) is a natural-language directive to the orchestrating LLM. The "footer" is whatever usage summary the Agent tool returns at the end of execution. The implementation extends this instruction to say: "also count tool invocations by type name (Read, Bash, Edit, Grep, Write, Glob, WebSearch, WebFetch, SendMessage, etc.) from the agent result, and write as `tools: {ToolName: count, ...}`". No structured parsing specification is needed — the orchestrator LLM interprets the instruction the same way it already interprets the token-extraction instruction.

7. **OQ-2 resolved — Current usage parsing status**: Evidence from the HL-276 state.yaml shows `usage: total_tokens: 63904` — partial data is being written. The full schema (input_tokens, output_tokens, etc.) is not fully populated, but this is a pre-existing gap, not in scope for HL-276. The orchestrator demonstrably writes usage data; the instruction just needs to include `tools:` alongside existing fields. No "Step 0 verification" task is needed.

8. **OQ-3 resolved — awk strategy**: Extend the existing per-agent awk block (lines 236-266) using SUBSEP concatenated keys (`tool_count[agent SUBSEP tool_type]`). Single pass, single block modification. This reuses the existing indentation-based state machine and adds one more level of state tracking for the `tools:` sub-map (8-space depth within usage).

9. **OQ-4 resolved — JSONL field name and scope**: Emit per-agent tool breakdown only (option a). New field: `per_agent_tools`. Format: JSON object keyed by agent name, each value a map of `{tool_type: count}`. This parallels `per_agent_tokens` — one aggregation level, consistent naming. Per-step tool maps remain in state.yaml for audit; JSONL is the aggregated view.

10. **OQ-5 resolved — run-simplify distinction**: Group by agent name in compute-swe-metrics (consistent with `per_agent_tokens`). Both execute-next-task and run-simplify record `agent: developer`, so their tool counts merge under "developer" in the JSONL aggregation. The per-step `tools:` maps in state.yaml already distinguish them via `step_id`. Finer JSONL granularity (by step_id) is out of scope for HL-276.

## Open Questions

All open questions (OQ-1 through OQ-5) have been resolved. See Key Decisions 6-10 above.

## Technical Context

**Primary files to change:**
- `/Users/spidey/code/orchestrator/skills/orchestrate/SKILL.md` — lines 107-110 (dispatch loop, AFTER step completes block)
- `/Users/spidey/code/orchestrator/config/scripts/compute-swe-metrics.sh` — lines 236-266 (per-agent awk block), line 324 (YAML output `per_agent_tokens`)
- `/Users/spidey/code/orchestrator/config/steps/CONVENTIONS.md` — line 218 (State Field Registry `step_history[].usage` row)
- `/Users/spidey/code/orchestrator/config/grammar.yaml` — swe_metrics section (documentation)

**Files to read but not necessarily change:**
- `/Users/spidey/code/orchestrator/config/steps/compute-swe-metrics.yaml` — step contract; may need to update `3a` instruction if JSONL schema changes
- `/Users/spidey/code/orchestrator/skills/telemetry/SKILL.md` — future consumer; no change in this ticket

**Schema evidence:**
- Current `step_history[].usage` schema: `{ input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms }` (CONVENTIONS.md line 150-157, State Field Registry line 218)
- Target addition: `tools: { Read: N, Edit: N, Bash: N, ... }` as a sibling of `tool_uses`
- All archived state.yaml files have null metrics (no live usage data to validate against)
- The `per_agent_tokens` field in JSONL is currently null in all recent entries (HL-270 through HL-273); first working data will come from HL-276 and later features

**awk parsing pattern in compute-swe-metrics.sh:**
- Token aggregation: lines 58-73 (single-pass awk, indentation-based state machine)
- Per-agent aggregation: lines 236-266 (second awk block, same pattern, groups by agent name)
- Both blocks use indentation depth (`/^      /` = 6 spaces = usage sub-field level) as the YAML structure signal — adding `tools:` at 8-space depth (sub-map within usage) requires one more level of state tracking
