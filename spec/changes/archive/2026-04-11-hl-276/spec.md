---
feature-id: hl-276
linear-ticket: HL-276
---

# Specification: Track per-tool-use breakdown in agent usage metrics

## Motivation

The orchestrator records a single `tool_uses` integer per step -- the total count of all tool invocations. When a feature is expensive, engineers must read the full conversation transcript to understand whether cost came from many Bash calls, heavy Read usage, or Agent spawns. HL-276 adds a `tools:` map that breaks the total down by tool type (e.g., `Read: 32, Bash: 18`), making cost diagnosis immediate from state.yaml and JSONL telemetry.

## What Changes

1. The `step_history[].usage` block in state.yaml gains a `tools:` sub-map that records per-tool-type invocation counts for each agent step.
2. The orchestrate SKILL.md dispatch loop instruction is extended to parse tool types from the agent result and write the `tools:` map.
3. compute-swe-metrics.sh aggregates `tools:` maps across steps into a `per_agent_tools` field in the JSONL output.
4. CONVENTIONS.md State Field Registry and grammar.yaml are updated to document the new field.

## Requirements

### Functional

1. **FR-1**: After each agent step, the orchestrator writes a `tools:` map under `step_history[].usage` containing tool-type keys with integer counts. The sum of all values in `tools:` equals the `tool_uses` integer.
2. **FR-2**: compute-swe-metrics.sh reads `tools:` sub-maps from step_history, aggregates them by agent name, and emits a `per_agent_tools` JSON field in JSONL output. Format: `{"developer":{"Read":89,"Bash":45},...}`.
3. **FR-3**: Tool type names are used as-is from agent results. No enum enforcement -- unknown tool types become new keys.
4. **FR-4**: state.yaml entries without a `tools:` sub-map (pre-HL-276) are silently skipped by compute-swe-metrics.sh. No backward compatibility breakage.
5. **FR-5**: When an agent step has zero tool calls, the `tools:` field is either `{}` or omitted entirely. Both are valid.

### Non-Functional

1. **NFR-1**: compute-swe-metrics.sh remains a single-pass awk script per block. The tools aggregation extends the existing per-agent awk block rather than adding a new pass.
2. **NFR-2**: No new files are introduced. All changes extend existing artifacts.

## Architecture

| File | Change | Rationale |
|------|--------|-----------|
| `skills/orchestrate/SKILL.md` | Extend dispatch loop instruction (lines 109-110) to write `tools:` map | Source of per-step tool data |
| `config/scripts/compute-swe-metrics.sh` | Extend per-agent awk block (lines 236-266) to aggregate tools; add `per_agent_tools` to YAML output | JSONL aggregation |
| `config/steps/CONVENTIONS.md` | Update State Field Registry row for `step_history[].usage` | Schema documentation |
| `config/grammar.yaml` | Add `per_agent_tools` to swe_metrics comment block | Grammar documentation |
| `config/steps/compute-swe-metrics.yaml` | Update instruction 3a to mention `per_agent_tools` field | Step contract alignment |

## Test Strategy

N/A -- YAML/markdown and bash script changes only. Verification via manual inspection and compute-swe-metrics.sh dry run.

## Acceptance Criteria

- AC-1: Given a step with agent usage, when the orchestrator completes the step, then `step_history[-1].usage.tools` contains a map of tool-type to count, and the sum equals `tool_uses`. [traces: UC-1]
- AC-2: Given a completed feature with tools data in state.yaml, when compute-swe-metrics.sh runs, then the JSONL output contains a `per_agent_tools` field with per-agent tool breakdowns. [traces: UC-2]
- AC-3: Given a state.yaml without `tools:` sub-maps (pre-HL-276), when compute-swe-metrics.sh runs, then the script produces valid output with `per_agent_tools` as `{}` or with only data from entries that have `tools:`. [traces: UC-3]
- AC-4: Given a step where the agent made zero tool calls, when the orchestrator writes step_history, then either `tools: {}` is written or `tools:` is omitted, and compute-swe-metrics handles both cases. [traces: UC-E1]
- AC-5: Given an agent result containing an unknown tool type name, when the orchestrator writes step_history, then the unknown name appears as a key in `tools:` without error. [traces: UC-E2]
- AC-6: Given the orchestrator fails to parse tool breakdown for a step, when compute-swe-metrics.sh runs, then the missing `tools:` key is treated as a pre-HL-276 entry and the script does not fail. [traces: UC-E3]

## Alternatives Considered

**Alternative B: Parse from JSONL transcript post-hoc**
Rejected. HL-275 explicitly moved away from transcript parsing to state.yaml as the canonical source. Reverting that decision would be a regression. Fragile due to varying JSONL formats across Claude Code versions.

**Alternative C: Add tools count to JSONL only, skip state.yaml**
Rejected. Loses per-step auditability. state.yaml is the audit trail; JSONL is a derived view. Inconsistent with how per_agent_tokens was structured in HL-273.

## Impact

No breaking changes. The `tools:` field is additive -- `tool_uses` remains unchanged. Old state.yaml files without `tools:` continue to work. The JSONL schema gains one new field (`per_agent_tools`) which consumers can ignore if not needed.

## Decisions

- `tools:` sits under `usage:` alongside `tool_uses:`, not as a sibling of `usage:`. Keeps all usage-derived data co-located.
- `tool_uses` remains the authoritative total. `tools:` is a breakdown whose sum must equal `tool_uses`.
- JSONL field name is `per_agent_tools` (parallels existing `per_agent_tokens`).
- No enum enforcement on tool names. Future-proofs against new tool types.
- awk aggregation uses SUBSEP keys (`tool_count[agent SUBSEP tool_type]`) to extend the existing per-agent block.
- Both execute-next-task and run-simplify merge under "developer" in per_agent_tools (grouped by agent name, consistent with per_agent_tokens).
