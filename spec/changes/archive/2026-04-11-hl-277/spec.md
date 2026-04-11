---
feature-id: hl-277
linear-ticket: HL-277
---

# Spec: Parse Claude Code session JSONL for full token breakdown in compute-swe-metrics

## Motivation

The orchestrator dispatch loop records `total_tokens`, `tool_uses`, and `duration_ms` in state.yaml step_history after each agent step -- these are the only three fields the Agent tool footer provides. The granular breakdown (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) is always 0 in state.yaml because the footer does not emit them. As a result, compute-swe-metrics.sh produces MODEL=unknown, cache_hit_rate=0, and gross/net costs of $0.

The full token breakdown already exists in Claude Code's session JSONL files at `~/.claude/projects/<slug>/`. This feature adds JSONL parsing to compute-swe-metrics.sh so metrics reflect real costs.

Two secondary issues compound the problem: SKILL.md line 110 instructs the dispatch loop to parse seven fields from the footer when only three exist (creating false-precision zeros), and compute-swe-metrics.yaml has a step writing to feature-metrics.jsonl that is no longer needed (state.yaml is the canonical audit trail).

## What Changes

| File | Change |
|------|--------|
| `config/scripts/compute-swe-metrics.sh` | Add jq-based JSONL parsing function; populate TOTAL_INPUT/OUTPUT/CACHE_CREATION/CACHE_READ/MODEL from session JONLs; fallback to state.yaml zeros when jq or JSONL unavailable |
| `skills/orchestrate/SKILL.md` | Fix line 110 to list only 3 real footer fields: total_tokens, tool_uses, duration_ms |
| `config/steps/compute-swe-metrics.yaml` | Remove step 3 (Write to feature-metrics.jsonl) and its verify assertion |

## Requirements

### Functional Requirements

FR-1: compute-swe-metrics.sh MUST parse Claude Code session JSONL files to extract per-API-call token breakdown (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens) and model name.

FR-2: The script MUST locate the Claude Code project directory via deterministic slug mapping: `~/.claude/projects/<slug>/` where slug is the repo absolute path with `/` replaced by `-`, leading `-` stripped.

FR-3: The script MUST scan all session directories and their subagent JONLs within the project directory whose entries fall within the feature's time window (`created_at` to `completed_at` from state.yaml).

FR-4: The script MUST include tokens from both the parent session JSONL (orchestrator overhead) and subagent JONLs (developer/architect/reviewer work).

FR-5: The script MUST deduplicate JSONL entries: filter to `type=="assistant"` with `message.usage` and `message.usage.iterations` present, then take the last entry per turn group (consecutive assistant entries between user entries).

FR-6: The script MUST extract `message.model` and use the dominant model (most total tokens) for the MODEL variable and cost calculation.

FR-7: SKILL.md line 110 MUST list only the 3 fields actually available in the Agent footer: `total_tokens`, `tool_uses`, `duration_ms`.

FR-8: compute-swe-metrics.yaml MUST NOT contain step 3 (Write to feature-metrics.jsonl) or its verify assertion.

### Non-Functional Requirements

NFR-1: JSONL parsing MUST be guarded by `command -v jq` availability check. When jq is absent, the script falls back to state.yaml values (zeros for granular fields) without error.

NFR-2: When session JSONL files are not found (wrong user, different machine, missing directory), the script MUST fall back gracefully to state.yaml values. No errors, no exit.

NFR-3: The metrics step MUST remain non-blocking -- parsing failures produce fallback values, never a non-zero exit code.

NFR-4: The JSONL parsing function MUST handle the edge case where TOTAL_TOKENS from JSONL is 0 (no matching entries) by falling through to state.yaml values.

## Test Strategy

N/A -- bash/yaml project. Verification is via the task-level Verify blocks which check script behavior against fixture data.

## Acceptance Criteria

AC-1: When jq is available and session JONLs exist for the feature time window, the metrics output contains non-zero values for `tokens.input`, `tokens.output`, `tokens.cache_creation`, `tokens.cache_read`, and a real model name under `cost.model`. [traces: UC-1]

AC-2: Cost calculation uses the dominant model's pricing. `cost.gross_usd` applies full input price to all input tokens; `cost.net_usd` applies cache_read discount to cache_read_input_tokens. Both are non-zero when JSONL data is present. [traces: UC-2]

AC-3: SKILL.md line 110 reads `usage: {total_tokens, tool_uses, duration_ms}` -- no mention of input_tokens, output_tokens, cache_creation_input_tokens, or cache_read_input_tokens. [traces: UC-3]

AC-4: When jq is not installed (`command -v jq` fails), the script produces the same output as before this change: zeros for granular token fields, MODEL=unknown, costs at $0. No error output. [traces: UC-E1]

AC-5: When `~/.claude/projects/<slug>/` does not exist or contains no matching JONLs, the script falls back to state.yaml values. The metrics block still writes successfully. [traces: UC-E1]

AC-6: JSONL deduplication correctly filters out streaming intermediates (entries without `message.usage.iterations`), producing accurate sums rather than inflated counts. [traces: UC-1]

AC-7: compute-swe-metrics.yaml no longer contains the feature-metrics.jsonl write step or its verify assertion. The `verify:` block only checks that state.yaml contains a `metrics:` block. [traces: UC-E3]

AC-8: The `turns` metric in output YAML is populated from the count of deduplicated JSONL entries (one per API turn), replacing the previous value of 0. [traces: UC-1]

## Alternatives Considered

| Approach | Complexity | Rejected Because |
|----------|-----------|-----------------|
| Python3 helper script | M | New file, new language dependency, higher maintenance for marginal testability gain |
| awk-based JSON parsing | L | Extremely fragile for nested JSON; unmaintainable |
| Per-model cost breakdown | M | Most features use one model; existing pricing block assumes single model; marginal accuracy gain vs complexity |

## Impact

- **Positive**: Real cost data in telemetry; accurate cache_hit_rate; meaningful cost-per-task benchmarks.
- **Breaking**: feature-metrics.jsonl stops receiving new entries. Existing consumers (telemetry, learn, workflow-improver) will read stale historical data. This is acceptable degradation per Key Decision 5; consumer migration is a follow-up ticket.
- **Dependency**: Requires jq to be installed for full benefit. Graceful degradation when absent.

## Decisions

All decisions are inherited from the Discovery Brief. See discovery.md Key Decisions 1-7 for full rationale. Key highlights:

- Feature-level time window correlation (not per-step)
- Include both parent session and subagent JONLs
- Dominant model for pricing (not per-model breakdown)
- jq-based parsing with `command -v jq` guard
- Drop feature-metrics.jsonl writes; defer consumer migration
