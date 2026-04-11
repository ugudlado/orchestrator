---
feature-id: hl-277
linear-ticket: HL-277
---

# Discovery Brief: Parse Claude Code session JSONL for full token breakdown in compute-swe-metrics

## Feature Summary

The orchestrator dispatch loop writes step_history entries to state.yaml after each agent step (SKILL.md line 109-114). It attempts to record `usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms}`. However, the Agent tool footer only emits three fields:

```
agentId: a05351da6bb2ee77c (use SendMessage...)
<usage>total_tokens: 83423
tool_uses: 55
duration_ms: 185376</usage>
```

So `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens` in state.yaml are always 0. The compute-swe-metrics.sh script (lines 58-73) reads these zero values, making cost calculations meaningless (MODEL defaults to "unknown", cache_hit_rate is 0, gross/net costs are 0).

The full token breakdown exists in Claude Code's session JSONL files at `~/.claude/projects/<slug>/<session-uuid>.jsonl` and in subagent-specific JONLs at `<session>/subagents/agent-<id>.jsonl`. Each message entry with `message.usage` contains `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` per API call.

This feature has three changes:
1. **compute-swe-metrics.sh**: parse session JSONL to get real token breakdown per subagent, then aggregate into total metrics
2. **SKILL.md**: fix the dispatch loop usage instruction to only list the 3 fields the Agent footer actually provides
3. **compute-swe-metrics.yaml**: drop the feature-metrics.jsonl write step — state.yaml is the audit trail

## Build or Reuse?

Build — this is codebase-internal plumbing. No external libraries are relevant. The session JSONL parsing is a new bash/python3 addition to compute-swe-metrics.sh. The SKILL.md and step contract changes are text edits. No external solution exists.

## Personas & Actors

- **Orchestrator agent (dispatch loop)**: writes step_history entries after each Agent tool call. Currently writes only total_tokens/tool_uses/duration_ms because that's all the footer provides.
- **compute-swe-metrics.sh**: reads state.yaml step_history and git history to produce a metrics block. Currently gets 0 for all granular token fields.
- **workflow-improver agent**: reads feature-metrics.jsonl (and now state.yaml metrics) to identify systemic issues. Needs real cost and cache data to make meaningful observations.
- **telemetry skill**: reads feature-metrics.jsonl for the dashboard. Shows tool_calls as integer; per-tool breakdown works. Token granularity improves cost accuracy.
- **Engineer**: runs /telemetry and reads archived state.yaml files. Wants to know actual cost per feature and cache efficiency.

## Use Cases

### Happy Path

**UC-1: Full token breakdown from subagent JSONL**
A developer agent completes a step. The dispatch loop records `total_tokens: 83423, tool_uses: 55, duration_ms: 185376` in step_history (the three footer fields). Later, the complete phase runs compute-swe-metrics.sh. The script:
1. Reads the parent session JSONL slug from state.yaml (or infers the project dir from cwd)
2. Finds subagent JONLs that correspond to the feature's time window (started_at..completed_at in step_history)
3. Reads each `agent-<id>.jsonl` and sums per-turn usage: input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, and model
4. Reads the matching `.meta.json` to get agentType (developer, architect, reviewer)
5. Emits a populated metrics block with real input/output/cache token counts and correct model for cost calculation

**UC-2: Correct cost calculation with cache discount**
A feature uses claude-sonnet-4-6. The subagent JONLs show 50k cache_read_input_tokens vs 5k fresh input_tokens. Previously, cost was 0 (MODEL=unknown). After the fix: gross_usd uses all tokens at full sonnet price ($3/1M input), net_usd applies the cache_read discount ($0.30/1M). The engineer sees realistic cost data in the telemetry dashboard.

**UC-3: SKILL.md correction prevents aspirational field writes**
The dispatch loop's orchestrate SKILL.md says to parse the agent result footer for `{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms}`. After the fix, the instruction lists only the three real footer fields. Future runs stop writing 0-value fields that create false precision in state.yaml.

### Error & Edge Cases

**UC-E1: Session JSONL not found — graceful degradation**
compute-swe-metrics.sh cannot locate the parent session JSONL (e.g., the feature ran in a different user session, or `~/.claude/projects/<slug>/` doesn't exist). The script falls back to the current behavior: reads state.yaml step_history for total_tokens, emits 0 for input/output/cache breakdown. The metrics block still writes with a `token_source: state_yaml_only` annotation. The metrics step remains non-blocking.

**UC-E2: Subagent time window correlation is ambiguous**
Multiple subagent JONLs overlap the step's time window (e.g., two concurrent orchestrator sessions). The script uses the agentId embedded in the Agent footer text to match the specific subagent file (`agent-<id>.jsonl`) directly rather than relying on timestamp proximity. If the agentId is not found in step_history (older state.yaml format), falls back to time-window correlation or emits zeros.

**UC-E3: feature-metrics.jsonl still referenced by run-phase-review.yaml**
Dropping JSONL writes from compute-swe-metrics.yaml would leave run-phase-review.yaml's baseline comparison (which reads `$WORKFLOW_STATE_DIR/feature-metrics.jsonl`) silently with no data. The step contract already handles this: "If file does not exist or no matching entries: skip silently." Out of scope for this feature whether to update run-phase-review.yaml — that's a separate decision.

## Scope

### In Scope

1. **compute-swe-metrics.sh**: add a session JSONL parsing block that:
   - Locates the Claude Code project dir for this repo (`~/.claude/projects/<slug>/`)
   - Enumerates subagent JONLs (`<session>/subagents/agent-<id>.jsonl`) that fall within the feature's time window (state.yaml `created_at` → `completed_at`)
   - Uses `.meta.json` to map agentId → agentType
   - Sums per-turn `message.usage` fields: input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens
   - Extracts model name from `message.model` in the JSONL entries
   - Populates TOTAL_INPUT, TOTAL_OUTPUT, TOTAL_CACHE_CREATION, TOTAL_CACHE_READ, MODEL
   - Falls back to state.yaml values (all zeros) if JSONL path not found

2. **SKILL.md** (`skills/orchestrate/SKILL.md`): fix line 110 to list only 3 available footer fields (total_tokens, tool_uses, duration_ms) — remove aspirational fields that aren't in the footer

3. **compute-swe-metrics.yaml** (`config/steps/compute-swe-metrics.yaml`): remove step 3 ("Write to feature-metrics.jsonl") and the verify assertion checking for a new JSONL entry

### Out of Scope

- Making the dispatch loop parse granular tokens in real time (the footer doesn't have them)
- Adding agentId to state.yaml step_history (useful future work but not required here)
- Updating run-phase-review.yaml's feature-metrics.jsonl baseline comparison behavior
- Updating workflow-improver.md or telemetry SKILL.md to reflect new token data location
- Removing feature-metrics.jsonl from agents/workflow-improver.md or learn/SKILL.md (those are read paths, not write paths — they will silently get no new data; existing historical entries remain)
- Per-session (multi-session) correlation beyond the current feature's time window

## UI Direction

N/A — no UI changes.

## Key Decisions

**1. Correlation strategy: feature-level time window** [DECIDED]

Use feature-level time window correlation. The metrics are per-feature aggregates — no per-step JSONL breakdown needed. Enumerate all session dirs and subagent JONLs in the Claude Code project directory whose entries fall within `state.yaml.created_at`..`completed_at`. This handles multi-session features (resumed after restart) by scanning all sessions in the project dir.

**2. Parent session identification** [DECIDED]

Deterministic slug mapping: `~/.claude/projects/<slug>/` where slug is the repo absolute path with `/` replaced by `-`, leading `-` stripped. For `/Users/spidey/code/orchestrator` the slug is `-Users-spidey-code-orchestrator`. Already confirmed as Claude Code's naming convention.

**3. JSONL entry deduplication** [DECIDED]

Critical finding from JSONL investigation: Claude Code writes multiple assistant entries per API turn. Each turn produces intermediate streaming entries (low output_tokens, no `iterations` field) and one or more final entries (real output_tokens, has `iterations` field). Entries chain via `parentUuid`. Within a single turn, token counts are NOT additive — the final entry contains the cumulative total for that turn.

Dedup strategy: filter to entries where `type=="assistant"` AND `message.usage` exists AND `message.usage.iterations` exists. Then group by turn boundary (consecutive assistant entries between user entries) and take the LAST entry per group. This avoids both streaming intermediates and duplicate finals.

**4. Main session inclusion** [DECIDED — resolves OQ-3]

Include both parent session JSONL and all subagent JONLs. The orchestrator's own tokens (routing, state reads, dispatch logic) are real API costs that should be counted in feature totals. Excluding them would understate true cost by the orchestrator overhead amount.

**5. feature-metrics.jsonl drop — deferred consumer migration** [DECIDED — resolves OQ-1]

Drop the WRITE step from compute-swe-metrics.yaml (step 3 and its verify assertion). Do NOT update telemetry/learn/workflow-improver consumers in this PR. Rationale:
- `run-phase-review.yaml` already handles missing file gracefully (silently skips)
- `telemetry/SKILL.md`, `learn/SKILL.md`, `workflow-improver.md` will read stale historical data — acceptable degradation
- The 19 existing entries in `~/.claude/logs/feature-metrics.jsonl` remain for historical reads
- Updating 4 consumers would expand scope significantly and is better as a follow-up ticket
- New metrics data lives in `state.yaml` archives, which is the canonical audit trail per project.yaml storage contract

**6. Model extraction and pricing — dominant model** [DECIDED — resolves OQ-2]

Extract `message.model` from JSONL entries. Use the model that accounts for the most total tokens (dominant model) for cost calculation in the single-model pricing block. Rationale:
- Most features use a single model across all subagents
- Per-model pricing adds complexity (separate cost lines per model) for marginal accuracy gain
- The existing `get_pricing()` function and output YAML format assume a single `model:` field
- Future work can add per-model cost breakdown if needed; per_agent_tokens already provides attribution

**7. Implementation approach — jq-based parsing in existing script** [DECIDED]

Selected from three evaluated approaches:

| Approach | Complexity | Description |
|----------|-----------|-------------|
| A: jq in compute-swe-metrics.sh | S | Add jq-based JSONL parsing function to existing script. Guard with `command -v jq` check, fallback to state.yaml zeros. |
| B: python3 helper script | M | Separate parse-session-jsonl.py called from bash. New file, new language dep, more testable but higher maintenance. |
| C: awk-based JSON parsing | L | Parse JSON with awk. Extremely fragile, unmaintainable. |

Auto-selected: **Approach A** — lowest complexity (S), reuses existing script, jq is a standard CLI tool already available. Fallback to state.yaml zeros when jq is absent makes this non-breaking.

## Open Questions

All resolved. See Key Decisions 4 (OQ-3), 5 (OQ-1), and 6 (OQ-2) above.

OQ-2 from discovery (summing strategy) was confirmed during discovery: sum all deduplicated entries (see Key Decision 3 for dedup strategy).

5. **Session boundary**: If a feature spans multiple Claude Code sessions (e.g., resumed after a restart), there will be multiple session UUIDs. The parent session dir would be different. How do we identify all sessions that contributed to a feature? Time window across all sessions in the project dir is the safest approach.

## Technical Context

**Files to change:**

- `/Users/spidey/code/feature_worktrees/hl-277/config/scripts/compute-swe-metrics.sh`
  - Lines 49-87: token counting block — add JSONL parsing before/instead of state.yaml reads
  - Lines 89-99: cost calculation — now MODEL will be populated correctly

- `/Users/spidey/code/feature_worktrees/hl-277/skills/orchestrate/SKILL.md`
  - Line 110: `usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, total_tokens, tool_uses, duration_ms}` → `usage: {total_tokens, tool_uses, duration_ms}` (only these 3 are in the Agent footer)

- `/Users/spidey/code/feature_worktrees/hl-277/config/steps/compute-swe-metrics.yaml`
  - Lines 41-70: remove step 3 (Write to feature-metrics.jsonl) and its verify assertion

**Session JSONL structure (confirmed):**

Parent session: `~/.claude/projects/<slug>/<session-uuid>.jsonl`
- Entries with `type: assistant` contain `message.usage` with full token breakdown
- `message.model` contains the model name (e.g., `claude-sonnet-4-6`)

Subagent session: `~/.claude/projects/<slug>/<session-uuid>/subagents/agent-<id>.jsonl`
- Same structure as parent: `type: assistant`, `message.usage` with input/output/cache tokens
- `agentId` field on every entry identifies which subagent

Subagent metadata: `~/.claude/projects/<slug>/<session-uuid>/subagents/agent-<id>.meta.json`
- `{"agentType": "architect", "description": "..."}` — maps agentId → step agent type

**Agent footer format (confirmed):**
```
agentId: a05351da6bb2ee77c (use SendMessage...)
<usage>total_tokens: 83423
tool_uses: 55
duration_ms: 185376</usage>
```
Only 3 fields. No input/output/cache breakdown.

**Project dir slug mapping:**
`/Users/spidey/code/orchestrator` → `-Users-spidey-code-orchestrator`
Formula: replace `/` with `-`, strip leading `-`

**Consumers of feature-metrics.jsonl** (read paths that break if writes stop):
- `skills/telemetry/SKILL.md` line 20 (primary source)
- `skills/learn/SKILL.md` lines 147, 191, 231
- `config/steps/run-phase-review.yaml` (gracefully handles missing file)
- `agents/workflow-improver.md` lines 17, 71
