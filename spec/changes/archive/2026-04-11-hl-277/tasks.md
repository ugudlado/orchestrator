---
feature-id: hl-277
linear-ticket: HL-277
---

# Tasks

- [x] T-1: Create test fixture JSONL file for parse_session_jsonl verification
- [x] T-2: Add parse_session_jsonl function to compute-swe-metrics.sh
- [x] T-3: Fix SKILL.md line 110 to list only 3 real footer fields
- [x] T-4: Remove feature-metrics.jsonl write step from compute-swe-metrics.yaml

---

## T-1: Create test fixture JSONL file for parse_session_jsonl verification

Create a minimal mock session JSONL file and directory structure that the parsing function can be tested against.

### Files
- `config/scripts/test-fixtures/session-mock.jsonl` (new)
- `config/scripts/test-fixtures/subagents/agent-abc123.jsonl` (new)
- `config/scripts/test-fixtures/subagents/agent-abc123.meta.json` (new)

### Details

Create a fixture directory under `config/scripts/test-fixtures/` that mimics the Claude Code session structure:

1. **session-mock.jsonl** (parent session): 4-6 entries mixing types:
   - 1 user entry (`type: "user"`)
   - 1 streaming assistant intermediate (`type: "assistant"`, has `message.usage` but NO `message.usage.iterations`)
   - 1 final assistant entry (`type: "assistant"`, has `message.usage` WITH `message.usage.iterations`, model: `claude-sonnet-4-6`)
   - 1 more user entry
   - 1 more final assistant entry (different token counts)

2. **subagents/agent-abc123.jsonl**: 3-4 entries:
   - 1 user entry
   - 1 streaming intermediate (should be filtered out by dedup)
   - 1 final assistant entry with known token values
   - Use model: `claude-sonnet-4-6` to match parent

3. **subagents/agent-abc123.meta.json**: `{"agentType": "developer"}`

Use specific known token values so the expected sums can be verified:
- Parent session final entries: input=1000, output=500, cache_creation=200, cache_read=3000 (entry 1); input=800, output=400, cache_creation=150, cache_read=2500 (entry 2)
- Subagent final entry: input=2000, output=1000, cache_creation=300, cache_read=5000
- Expected totals: input=3800, output=1900, cache_creation=650, cache_read=10500
- Expected turns: 3 (three deduplicated final entries)
- Expected model: claude-sonnet-4-6

### Verify
- Fixture files exist and contain valid JSONL (one JSON object per line)
- `jq '.' config/scripts/test-fixtures/session-mock.jsonl` parses without error
- `jq '.' config/scripts/test-fixtures/subagents/agent-abc123.jsonl` parses without error
- Streaming intermediate entries do NOT have `message.usage.iterations` field
- Final entries DO have `message.usage.iterations` field

---

## T-2: Add parse_session_jsonl function to compute-swe-metrics.sh

Add the jq-based JSONL parsing function and integrate it into the script flow. This is the main implementation task.

### Dependencies
- T-1 (fixture files for verification)

### Files
- `config/scripts/compute-swe-metrics.sh` (modify)

### Details

#### 1. Move wall clock extraction earlier

Move the STARTED_AT/COMPLETED_AT extraction block (currently lines 102-119) to immediately after the state.yaml awk parsing block (after line 83). The time window values are needed by parse_session_jsonl.

#### 2. Add parse_session_jsonl function

Add a function before the main flow (after get_pricing, before the token counting section) with this structure:

```bash
parse_session_jsonl() {
  # Returns 0 and sets token variables on success, 1 on failure/no-data
  
  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
  
  # Compute Claude Code project slug
  local slug="${repo_root//\//-}"
  slug="${slug#-}"
  local project_dir="$HOME/.claude/projects/$slug"
  
  [[ -d "$project_dir" ]] || return 1
  
  # Convert time window to epoch for filtering
  # (use STARTED_AT and COMPLETED_AT already extracted from state.yaml)
  local start_epoch end_epoch
  start_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$STARTED_AT" "+%s" 2>/dev/null \
    || date -j -f "%Y-%m-%dT%H:%M:%S" "${STARTED_AT%Z}" "+%s" 2>/dev/null \
    || date -d "$STARTED_AT" "+%s" 2>/dev/null) || return 1
  end_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$COMPLETED_AT" "+%s" 2>/dev/null \
    || date -j -f "%Y-%m-%dT%H:%M:%S" "${COMPLETED_AT%Z}" "+%s" 2>/dev/null \
    || date -d "$COMPLETED_AT" "+%s" 2>/dev/null) || return 1
  
  # Find all JSONL files in project dir
  local jsonl_files
  jsonl_files=$(find "$project_dir" -name "*.jsonl" 2>/dev/null)
  [[ -n "$jsonl_files" ]] || return 1
  
  # Parse with jq: deduplicate, filter, sum
  # Filter: type==assistant, has message.usage, has message.usage.iterations
  # Dedup: consecutive assistant entries -> take last per run
  # Sum: aggregate token fields across all files
  local result
  result=$(echo "$jsonl_files" | xargs cat 2>/dev/null | jq -s --arg start "$start_epoch" --arg end "$end_epoch" '
    # Filter to final assistant entries only (has iterations = not a streaming intermediate)
    [.[] | select(
      .type == "assistant" and
      .message.usage != null and
      .message.usage.iterations != null
    )]
    # Time filter: keep entries within feature window
    | [.[] | select(
        (.timestamp // 0) >= ($start | tonumber) and
        (.timestamp // 0) <= ($end | tonumber)
      )]
    | if length == 0 then error("no entries") else . end
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
  ' 2>/dev/null) || return 1
  
  # Extract values from jq result
  TOTAL_INPUT=$(echo "$result" | jq -r '.input_tokens')
  TOTAL_OUTPUT=$(echo "$result" | jq -r '.output_tokens')
  TOTAL_CACHE_CREATION=$(echo "$result" | jq -r '.cache_creation')
  TOTAL_CACHE_READ=$(echo "$result" | jq -r '.cache_read')
  TOTAL_TURNS=$(echo "$result" | jq -r '.turns')
  MODEL=$(echo "$result" | jq -r '.model')
  TOTAL_TOKENS=$((TOTAL_INPUT + TOTAL_OUTPUT + TOTAL_CACHE_CREATION))
  
  # Validate we got real data
  [[ "$TOTAL_INPUT" -gt 0 ]] || return 1
  
  return 0
}
```

#### 3. Call the function (guarded)

After the state.yaml parsing block and wall clock extraction, add:

```bash
# ── Session JSONL Token Enrichment ──────────────────────────────────────
# Attempt to read full token breakdown from Claude Code session JONLs.
# Falls back to state.yaml values (zeros for granular fields) if unavailable.
if command -v jq >/dev/null 2>&1 && [[ -n "$STARTED_AT" && -n "$COMPLETED_AT" ]]; then
  parse_session_jsonl || true  # failure is non-blocking
fi
```

#### 4. Update comments

Update the script header comment (lines 10-11) to reflect the new primary source:
```
# Token/cost data: primary source is Claude Code session JSONL files (via jq).
# Fallback: state.yaml step_history (only has total_tokens from Agent footer).
```

#### 5. Reuse wall clock epoch calculation

The wall clock section (now moved earlier) already computes START_EPOCH/END_EPOCH. Consider reusing these in parse_session_jsonl instead of recomputing. Alternatively, the function can compute its own epochs -- the implementer should choose the cleaner approach.

### Verify
- Run the script against the test fixture directory structure with a mock state.yaml: output should show non-zero input/output/cache_creation/cache_read tokens and model=claude-sonnet-4-6
- When jq is not in PATH (simulate with `PATH= command`), the script produces identical output to before (zeros, unknown model)
- When project dir does not exist, the script produces fallback output without errors
- The `|| true` guard means parse_session_jsonl failure never causes script exit under `set -uo pipefail`
- TOTAL_TURNS in output equals the count of deduplicated entries (not 0)

---

## T-3: Fix SKILL.md line 110 to list only 3 real footer fields

### Files
- `skills/orchestrate/SKILL.md` (modify)

### Details

Change line 110 from:
```
    usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms}
```

To:
```
    usage: {total_tokens, tool_uses, duration_ms}
```

This removes the four fields (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens) that the Agent tool footer does not actually provide. The dispatch loop was writing 0 for these fields, creating false precision in state.yaml.

### Verify
- Line 110 of SKILL.md lists exactly 3 fields: total_tokens, tool_uses, duration_ms
- No other references to the removed fields exist in the usage instruction block (lines 108-115)
- The tools: instruction block (lines 111-115) is unchanged

---

## T-4: Remove feature-metrics.jsonl write step from compute-swe-metrics.yaml

### Files
- `config/steps/compute-swe-metrics.yaml` (modify)

### Details

1. Update the `intent:` line: change "Compute SWE metrics and persist to state.yaml and feature-metrics.jsonl." to "Compute SWE metrics and persist to state.yaml."

2. Remove step 3 entirely (lines 41-64): the "Write to feature-metrics.jsonl" block including sub-step 3a (per-agent token and tool attribution for JSONL). This content starts at `  3. **Write to feature-metrics.jsonl**:` and ends before `  4. Update state.yaml`.

3. Renumber step 4 to step 3: `4. Update state.yaml step_history` becomes `3. Update state.yaml step_history`.

4. Update the `verify:` block: remove the second assertion `- feature-metrics.jsonl has a new entry for this feature`. Only keep `- state.yaml contains a metrics: block (even if placeholder)`.

5. Update the `outputs:` list if needed (keep `swe_metrics`).

### Verify
- compute-swe-metrics.yaml has no mention of "feature-metrics.jsonl"
- The instruction section has 3 steps (numbered 1, 2, 3), not 4
- The verify block has exactly 1 assertion (state.yaml metrics block)
- The intent line mentions only state.yaml, not feature-metrics.jsonl
