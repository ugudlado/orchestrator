# Backfill step_history Coverage from JSONL

## Idea
Re-run JSONL enrichment across archived features to backfill missing tokens and `tools_json` on `step_history` rows, then add an invariant preventing the gap from reopening.

## Evidence
- `step_history` has 193 rows; only 39 have `total_tokens`, only 3 have `tools_json`.
- `per_agent_tool_uses` has 2 rows across 20 ingested features — per-agent-per-tool breakdown is effectively empty.
- JSONL session data exists for most archives but the initial enrichment script missed many rows (path-resolution bugs, quoted-timestamp bugs — several fixed after the bulk of archives were ingested).

## Fix
1. Re-run JSONL enrichment pass over every archived `state.yaml` with JSONL sessions available.
2. Re-run `register-repo.sh` to reingest.
3. Add invariant in register-repo: any `step_history` row with `agent != NULL AND status = completed` must have `total_tokens > 0` OR be marked `agent: inline`.

## Why Now
Unblocks per-step token/cost analysis for 80% of history. Prerequisite for regression detection (needs a full baseline).

## Priority
- User value: 8/10
- Strategic fit: 9/10
- Technical leverage: 9/10
- Effort: medium
- **Score: 8.5**
