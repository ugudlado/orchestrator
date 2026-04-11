# Design: Track per-tool-use breakdown in agent usage metrics

## Context

The orchestrator records agent usage in `step_history[].usage` within state.yaml. Currently this includes token counts, tool_uses (integer total), and duration. HL-273 added per-agent token attribution in compute-swe-metrics.sh. HL-276 extends the same pattern to track which specific tools (Read, Bash, Edit, Grep, etc.) each agent invoked, enabling cost diagnosis without transcript reading.

Three touch points exist: the SKILL.md dispatch loop instruction (writes per-step data), compute-swe-metrics.sh (aggregates into JSONL), and documentation (CONVENTIONS.md, grammar.yaml, step contract).

## Goals / Non-Goals

### Goals

- Record per-tool-type invocation counts in state.yaml per agent step
- Aggregate tool counts by agent name in JSONL telemetry output
- Maintain backward compatibility with pre-HL-276 state.yaml files

### Non-Goals

- Telemetry dashboard display of tool breakdown (ship data first, display second)
- Backfilling historical state.yaml files
- Changing the existing `tool_uses` integer (it remains authoritative)
- Filtering or capping which tool names appear

## Approaches Considered

### Approach A: Inline extension of existing artifacts

Extend the SKILL.md dispatch loop instruction to also write a `tools:` map. Extend the existing per-agent awk block in compute-swe-metrics.sh to aggregate tool maps using SUBSEP keys. Update documentation.

Pros: Fits existing schema, consistent with HL-273 pattern, minimal change surface, single-pass awk.
Cons: Dispatch loop instruction is natural language -- depends on LLM correctly parsing tool types from agent results.

### Approach B: Separate awk block for tools

Add a second dedicated awk block in compute-swe-metrics.sh specifically for tool aggregation, separate from the per-agent token block.

Pros: Isolation -- tools logic doesn't complicate existing token logic.
Cons: Second pass over state.yaml, code duplication of the YAML state machine, inconsistent with how per-agent tokens were added.

### Selected Approach

Approach A: Inline extension. The existing per-agent awk block already walks step_history entries with agent+usage. Adding tool-type tracking to the same block avoids a second pass and duplicated state machine logic. This is the same pattern used by HL-273 for per_agent_tokens.

## High-Level Design

### Architecture Overview

```
Agent step completes
       |
       v
SKILL.md dispatch loop (orchestrator LLM)
  - Parses agent result footer for tool types
  - Writes to state.yaml:
      step_history[-1].usage.tools: { Read: N, Bash: N, ... }
       |
       v
compute-swe-metrics.sh (at feature completion)
  - Reads step_history[].usage.tools from state.yaml
  - Aggregates by agent name using awk SUBSEP keys
  - Emits per_agent_tools in JSONL output
       |
       v
feature-metrics.jsonl
  - per_agent_tools: {"developer":{"Read":89,"Bash":45,...}}
```

### Key Abstractions

No new abstractions. The design extends two existing patterns:
1. The natural-language dispatch instruction pattern (SKILL.md tells the LLM what to parse and write)
2. The awk indentation-based state machine pattern (compute-swe-metrics.sh walks YAML structure by indent depth)

## Low-Level Design

### Components

#### 1. SKILL.md dispatch loop (lines 109-110)

Current instruction:
```
IF step had agent: field, parse the agent result footer for usage data and add:
  usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms}
```

Extended instruction adds after the usage field list:
```
  Also count tool invocations by type name (Read, Bash, Edit, Grep, Write, Glob,
  WebSearch, WebFetch, SendMessage, etc.) from the agent result, and write as:
    tools: {ToolName: count, ...}
  under usage:. The sum of all tools values must equal tool_uses.
  If no tool calls were made, omit tools: or write tools: {}.
```

This is a natural-language extension. The orchestrating LLM already parses tool_uses from agent results; this tells it to also count by type.

#### 2. compute-swe-metrics.sh (lines 236-266, line 324)

Extend the per-agent awk block to:
1. Detect `tools:` sub-map within `usage:` (at 8-space indent depth)
2. Parse `ToolName: count` lines within the tools block (at 10-space depth)
3. Accumulate into `tool_count[agent, tool_type]` using awk SUBSEP
4. In the END block, build a JSON object keyed by agent name, each value a `{tool: count}` map

The awk state machine adds one more state: `in_tools` (entered when seeing `tools:` at 6-space depth within usage, parsing lines at 8-space depth).

Note on indentation: In state.yaml, step_history entries are a YAML list. The structure is:
- 2 spaces: `- step_id: ...` (list item)
- 4 spaces: `usage:` (entry field)
- 6 spaces: `tool_uses: N`, `tools:` (usage sub-fields)
- 8 spaces: `Read: N` (tools sub-map values)

The existing awk block uses these indent levels for its state machine. The tools extension adds tracking at the 8-space level within a `tools:` context.

Output: New shell variable `PER_AGENT_TOOLS` containing a JSON string, emitted alongside `per_agent_tokens` in the YAML output block.

#### 3. CONVENTIONS.md State Field Registry (line 218)

Update the `step_history[].usage` row description to include `tools: { ToolName: N, ... }` in the field list.

#### 4. grammar.yaml swe_metrics section

Add a comment documenting `per_agent_tools` in the benchmarks or efficiency section.

#### 5. compute-swe-metrics.yaml step contract (instruction 3a)

Update instruction 3a to mention that `per_agent_tools` should also be included in the JSONL output, paralleling `per_agent_tokens`.

### Data Flow

1. **Write path** (per step): Orchestrator LLM reads agent result -> counts tool types -> writes `tools:` map under `usage:` in state.yaml
2. **Read path** (at completion): compute-swe-metrics.sh reads state.yaml -> awk parses step_history entries -> aggregates tool counts by agent -> emits `per_agent_tools` JSON string -> appended to JSONL

### State Management

**state.yaml** (per step, append to step_history):
```yaml
step_history:
  - step_id: execute-next-task
    agent: developer
    usage:
      input_tokens: 12000
      output_tokens: 3500
      total_tokens: 18500
      tool_uses: 7
      tools:
        Read: 3
        Bash: 2
        Edit: 1
        Grep: 1
      duration_ms: 42000
```

**feature-metrics.jsonl** (per feature):
```json
{..., "per_agent_tools": "{\"developer\":{\"Read\":89,\"Bash\":45,\"Grep\":30,\"Edit\":12}}", ...}
```

The `per_agent_tools` value is a JSON-encoded string (same convention as `per_agent_tokens`).

### Error Handling

- **Missing tools: key**: awk checks `in_tools` flag. If a usage block has no `tools:` line, `in_tools` is never set and no tool data is accumulated for that entry. The entry's tokens/tool_uses still count normally.
- **Empty tools map** (`tools: {}`): Flow-style empty map on one line. awk sees `tools:` but no subsequent 8-space lines before the next field, so no tool data accumulated. Equivalent to absent.
- **Unknown tool name**: No validation. Any key at the correct indent level under `tools:` is accumulated as-is.
- **Malformed tools block**: If indentation is wrong, awk won't match the patterns and the tools data is silently skipped. tool_uses integer is unaffected.

## Constraints

- SKILL.md instructions are natural language interpreted by the orchestrating LLM. The instruction must be clear enough for the LLM to reliably parse tool types from agent results.
- compute-swe-metrics.sh uses awk (not jq) because state.yaml is YAML, not JSON. The awk pattern must handle YAML indentation correctly.
- The `per_agent_tools` JSON string in JSONL uses the same quoting convention as `per_agent_tokens` (single-quoted JSON string embedded in YAML output).

## Trade-offs

- **Natural language instruction vs. structured parsing**: We rely on the LLM correctly counting tool types from agent results. This is acceptable because the LLM already successfully extracts token counts and tool_uses from the same results. The incremental ask (break down by type) is small.
- **Single awk block vs. separate block**: Combining tools aggregation into the existing per-agent block adds complexity to that block but avoids a second pass and code duplication. The block is already the most complex part of the script; adding SUBSEP keys is a modest extension.

## Decisions

- `tools:` under `usage:` alongside `tool_uses:` (Key Decision 1)
- `tool_uses` remains authoritative total; `tools:` is breakdown (Key Decision 2)
- JSONL field: `per_agent_tools` as JSON object string (Key Decision 3, 9)
- No enum enforcement on tool names (Key Decision 4)
- Inline extension of existing awk block with SUBSEP keys (Key Decision 5, 8)
- Natural-language dispatch instruction, no structured parsing spec (Key Decision 6)
- No "Step 0 verification" task needed; orchestrator demonstrably writes usage data (Key Decision 7)
- Group by agent name; execute-next-task and run-simplify merge under "developer" (Key Decision 10)

## Open Questions

None. All open questions from discovery (OQ-1 through OQ-5) were resolved during discovery. See discovery.md Key Decisions 6-10.
