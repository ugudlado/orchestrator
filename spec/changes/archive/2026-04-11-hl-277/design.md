---
feature-id: hl-277
linear-ticket: HL-277
---

# Design: Parse Claude Code session JSONL for full token breakdown

## Context

compute-swe-metrics.sh reads state.yaml for token data, but the dispatch loop can only write 3 fields from the Agent footer (total_tokens, tool_uses, duration_ms). The granular breakdown lives in Claude Code's session JSONL files. This design adds a JSONL parsing stage to the existing script, before the state.yaml fallback path.

## Goals

- Populate TOTAL_INPUT, TOTAL_OUTPUT, TOTAL_CACHE_CREATION, TOTAL_CACHE_READ, MODEL from real JSONL data
- Populate TOTAL_TURNS from deduplicated JSONL entry count
- Keep the script working identically when jq is absent or JONLs are missing
- Fix SKILL.md's aspirational field list
- Remove dead feature-metrics.jsonl write step

## Non-Goals

- Per-step JSONL breakdown (feature-level aggregation is sufficient)
- Adding agentId to state.yaml step_history
- Updating feature-metrics.jsonl consumers (telemetry, learn, workflow-improver)
- Per-model cost breakdown

## Selected Approach

Add a single bash function `parse_session_jsonl()` to compute-swe-metrics.sh that uses jq to parse JSONL files. Guard the entire block with `command -v jq`. On success, it overwrites the token variables; on failure or absence, the existing state.yaml path remains the fallback.

## High-Level Design

```
compute-swe-metrics.sh execution flow:

1. Read state.yaml for step_history (existing) -- sets TOTAL_TOKENS, TOTAL_TOOL_CALLS, etc.
2. [NEW] Extract STARTED_AT and COMPLETED_AT from state.yaml (moved earlier)
3. [NEW] Call parse_session_jsonl() -- attempts to overwrite token vars from JSONL
4. Cost calculation (existing) -- now MODEL is populated correctly
5. Git churn, task resolution, etc. (existing, unchanged)
6. Output YAML (existing, unchanged -- variables already populated)
```

## Low-Level Design

### Component: parse_session_jsonl()

**Location**: config/scripts/compute-swe-metrics.sh, inserted between the state.yaml token reading block and the cost calculation block.

**Guard**: The function is only called inside an `if command -v jq >/dev/null 2>&1; then ... fi` block. When jq is absent, execution skips the entire JSONL parsing path.

**Steps**:

1. **Compute project slug**: Take the git repo root (`git rev-parse --show-toplevel`), replace `/` with `-`, strip leading `-`. Build path: `~/.claude/projects/<slug>/`.

2. **Validate directory exists**: If the project directory does not exist, return 1 (caller treats as "no JSONL data").

3. **Convert time window to epoch**: Parse STARTED_AT and COMPLETED_AT (already extracted from state.yaml) to Unix epoch using `date`. These bound which JSONL entries to include.

4. **Find all JSONL files**: Enumerate `<project_dir>/*.jsonl` (parent sessions) and `<project_dir>/*/subagents/agent-*.jsonl` (subagent sessions). Use `find` with glob patterns.

5. **Parse and deduplicate with jq**: For each JSONL file, run a single jq pipeline that:
   - Filters to entries where `.type == "assistant"` AND `.message.usage` exists AND `.message.usage.iterations` exists (this eliminates streaming intermediates)
   - Filters to entries within the time window (`.timestamp` between start and end epochs, or `.createdAt` ISO timestamp comparison)
   - Extracts `.message.usage.input_tokens`, `.output_tokens`, `.cache_creation_input_tokens`, `.cache_read_input_tokens` and `.message.model`
   - Sums all fields across all qualifying entries in all files

6. **Dominant model**: Collect model names with their total token counts. The model with the highest sum becomes MODEL.

7. **Populate variables**: Set TOTAL_INPUT, TOTAL_OUTPUT, TOTAL_CACHE_CREATION, TOTAL_CACHE_READ, MODEL, TOTAL_TURNS. Recompute TOTAL_TOKENS as `input + output + cache_creation`.

8. **Return**: Return 0 on success. The caller checks: if TOTAL_INPUT is still 0 after parsing, the JSONL data was empty and state.yaml values remain.

**jq pipeline** (single pass over concatenated JSONL):

```bash
find "$PROJECT_DIR" -name "*.jsonl" -print0 | xargs -0 cat | jq -s '
  [.[] | select(
    .type == "assistant" and
    .message.usage != null and
    .message.usage.iterations != null
  )]
  | group_by(.sessionId // "default")
  | [.[] | # within each session, take last assistant entry per turn
     reduce .[] as $entry (
       {result: [], last_role: null};
       if $entry.type == "assistant" then
         .result[-1] = $entry  # overwrite last in current run
       else
         .result += [$entry] | .last_role = $entry.type
       end
     ) | .result[] | select(.type == "assistant")
  ]
  | {
      input_tokens: (map(.message.usage.input_tokens // 0) | add // 0),
      output_tokens: (map(.message.usage.output_tokens // 0) | add // 0),
      cache_creation: (map(.message.usage.cache_creation_input_tokens // 0) | add // 0),
      cache_read: (map(.message.usage.cache_read_input_tokens // 0) | add // 0),
      turns: length,
      model: (group_by(.message.model)
              | map({model: .[0].message.model, total: (map(.message.usage.input_tokens // 0) | add)})
              | sort_by(-.total) | .[0].model // "unknown")
    }
'
```

[ASSUMPTION] The jq pipeline above is a conceptual sketch. The implementer should adjust field paths based on actual JSONL structure (e.g., `.timestamp` vs `.createdAt` for time filtering). The dedup logic may need refinement based on how turn boundaries appear in real data -- the key invariant is: take the last assistant entry per consecutive run of assistant entries.

**Time window filtering**: Rather than filtering inside jq (which requires epoch comparison that varies by jq version), use a simpler approach: the `find` command filters JSONL files by modification time (`-newer` flag with temp marker files), and jq processes all entries in qualifying files. This is acceptable because a single feature's time window typically spans minutes to hours, and session files within that window are small.

[ASSUMPTION] Session JSONL files have modification times that fall within or near the feature's time window. If a session file was modified after the feature completed (e.g., by a subsequent feature), the jq time filter on individual entries handles the precision.

### Component: SKILL.md fix

**Location**: skills/orchestrate/SKILL.md, line 110.

**Change**: Replace `usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms}` with `usage: {total_tokens, tool_uses, duration_ms}`.

This is a documentation-only change. The dispatch loop already only writes what the footer provides; this just stops the instruction from claiming otherwise.

### Component: compute-swe-metrics.yaml cleanup

**Location**: config/steps/compute-swe-metrics.yaml.

**Changes**:
- Remove step 3 (lines 41-64, "Write to feature-metrics.jsonl" and its sub-step 3a)
- Update verify block: remove the `feature-metrics.jsonl has a new entry` assertion
- Update intent line to remove "and feature-metrics.jsonl"

### Data Flow

```
Session JSONL files                 state.yaml
  ~/.claude/projects/<slug>/          step_history[].usage
  *.jsonl + */subagents/*.jsonl       (total_tokens, tool_uses, duration_ms only)
       |                                    |
       v                                    v
  parse_session_jsonl()              awk parser (existing)
  [jq-based, guarded]               [always runs first]
       |                                    |
       +--- overwrites if data found -------+
       |                                    |
       v                                    v
  TOTAL_INPUT, TOTAL_OUTPUT,         TOTAL_TOKENS, TOTAL_TOOL_CALLS
  TOTAL_CACHE_CREATION,              (from state.yaml -- used as fallback
  TOTAL_CACHE_READ, MODEL,            or for fields JSONL doesn't provide)
  TOTAL_TURNS
       |
       v
  Cost Calculation (get_pricing)
       |
       v
  Output YAML (stdout)
```

### Error Handling

| Scenario | Behavior |
|----------|----------|
| jq not installed | Skip JSONL parsing entirely; state.yaml values used (all zeros for granular fields) |
| Project dir not found | parse_session_jsonl returns 1; state.yaml fallback |
| No JSONL files match time window | parse_session_jsonl returns 1; state.yaml fallback |
| jq parse error (malformed JSONL) | jq returns non-zero; caught by `|| return 1`; state.yaml fallback |
| JSONL data sums to 0 | Treated as "no data"; state.yaml fallback applies |

In all fallback cases, the script continues to completion and produces valid YAML output. The metrics step remains non-blocking.

## Constraints

- jq is the only new dependency; it must be optional (guarded)
- No changes to output YAML schema -- same fields, better values
- No changes to state.yaml write format -- this script only reads state.yaml, the dispatch loop writes it
- TOTAL_TOOL_CALLS and TOTAL_DURATION_MS continue to come from state.yaml (JSONL does not provide equivalent tool-call counts per the dispatch loop's format)

## Trade-offs

1. **Single jq pass vs per-file iteration**: A single `find | xargs cat | jq -s` is simpler but loads all JSONL into memory. For typical features (a few MB of JSONL), this is fine. If JSONL files grow very large, per-file streaming with `jq --slurp` per file would be needed. [ASSUMPTION] Feature JSONL data is under 50MB total.

2. **Dominant model vs per-model pricing**: Using a single dominant model loses precision when a feature mixes models. Accepted trade-off: most features are single-model, and the output YAML schema has a single `model:` field.

3. **Time window precision**: Using file modification time for initial filtering (`find -newer`) is coarse. Individual entry timestamps in jq provide precision. The two-stage approach balances performance (skip old files) with correctness (filter entries).

## Decisions

Inherited from discovery.md. No new decisions required at design time.
