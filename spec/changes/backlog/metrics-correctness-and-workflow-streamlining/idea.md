# Metrics correctness + implement-phase streamlining

Bundled workflow improvement addressing three tightly-coupled issues discovered during HL-282 (autopilot-2026-04-17-001): metrics silently record zeros, per-agent/per-step breakdowns are incomplete, and the implement phase spawns three reviewer agents where one would do.

## Problem

### 1. Zero-cost metrics on every feature

`compute-swe-metrics.sh` produces zero input/output tokens and zero cost when run against an active (pre-archive) state.yaml. **Observed**: HL-282 initial write had `input: 0, output: 0, net_usd: 0, total: 124087` (state-only fallback). Re-run against the archived state.yaml after `completed_at` was present: `input: 22947, output: 231650, net_usd: 26.13`. Same JSONL files, both runs — the only difference was presence of `completed_at`.

**Root cause**: step ordering in `config/workflows/_complete-phase.yaml`:
```
compute-prediction-accuracy → run-learn-cycle → compute-swe-metrics → archive-completed-change
```
`archive-completed-change` is the step that writes `completed_at`, but `compute-swe-metrics` runs before it. `parse_session_jsonl` requires both `STARTED_AT` and `COMPLETED_AT` to compute the JSONL time window; when `COMPLETED_AT` is empty, `date -j -f` fails, the function returns 1, and the script silently falls through to the state-only token path (which only has aggregated `total_tokens` from the Agent footer).

The prior "fix" (commit a6a2e95) addressed TZ/slug bugs, not this ordering dependency — it was validated by re-running against already-archived state.

### 2. Per-agent and per-step metrics are incomplete

`metrics.per_agent_tokens` map shows only 2 of ~20 agents spawned in HL-282. `per_agent_tools` is empty `{}`. There is no `per_step` aggregation at all.

**Observed**:
```yaml
per_agent_tokens: '{"architect":{"total_tokens":77031,...},"discoverer":{"total_tokens":47056,...}}'
per_agent_tools: '{}'
```

**Root causes**:
- Orchestrator doesn't validate step_history entries for required fields (`agent:`, `usage: { total_tokens }`, `usage: { tools: {...} }`), so hand-written or partial entries silently drop out of aggregation
- `compute-swe-metrics.sh` has no awk pass that groups by `step_id` — per-step data is never aggregated

### 3. Implement phase spawns three reviewer agents that overlap

Currently: `run-simplify` → `run-phase-review` → `run-feature-verification` — three sequential reviewer spawns, each re-reading the same files. AC verification happens in two of them. In HL-282 I short-circuited `run-feature-verification` because `T-9` + phase review had already covered it.

Simplify also runs in the wrong agent's hands: a reviewer-agent simplify pass is an arm's-length evaluation. Simplification belongs with the developer who just wrote the code and has full context.

## Proposal

One ticket, three tasks. All three touch the implement→complete boundary, share the same state.yaml schema concerns, and benefit from being verified together.

### Task A: Fix step ordering + split mark-completed

New step `mark-change-completed` (inline, no agent):
- Writes `status: completed`, `completed_at: <ISO>`, `archive_path: spec/changes/archive/YYYY-MM-DD-<change-id>/` to state.yaml
- Single responsibility; no file operations

Revised `archive-completed-change`:
- Assumes fields already set
- Becomes a pure move+commit step

New order in `_complete-phase.yaml`:
```
compute-prediction-accuracy → run-learn-cycle → mark-change-completed → compute-swe-metrics → archive-completed-change → remove-worktree
```

Backfill: re-run `compute-swe-metrics` against every `spec/changes/archive/*/state.yaml` with `cost.net_usd: 0` AND a matching JSONL set in `~/.claude/projects/`.

### Task B: Consolidate implement-phase review + move simplify into developer

Replace `run-simplify` + `run-phase-review` + `run-feature-verification` with:

1. **Developer simplify pass** — after the last `execute-next-task` iteration, the developer runs a focused simplification pass over the worktree changes (same agent who wrote the code, still in-context). No new agent spawn; it's a trailing responsibility on `execute-next-task` or a lightweight `developer-simplify-pass` step.

2. **Single `run-implement-review`** step — one reviewer spawn that does:
   - Changed-file read + scoring on 5 dimensions
   - AC verification with evidence
   - Fix-task generation per Fix Task Protocol
   - Output the same review_score block + fix tasks

Deprecate `run-simplify.yaml` and `run-feature-verification.yaml`.

### Task C: Enforce step_history usage fields + add per-step aggregation

Two sub-parts:

**C.1 — Validator** (non-blocking, stderr warning):
- Add a state-schema check that runs during `mark-change-completed` (or as part of `compute-swe-metrics` preflight)
- Flags step_history entries that are missing `agent:` (when the step contract declares an `agent:` field) or missing `usage: { total_tokens: <N>, tools: { ... } }`
- Warning to stderr; does not block the complete phase

**C.2 — Per-step aggregation**:
- New awk pass in `compute-swe-metrics.sh` that groups by `step_id`
- Emit:
  ```yaml
  per_step:
    explore: { total_tokens: N, cost_usd: N, duration_ms: N, count: 1 }
    execute-next-task: { total_tokens: N, cost_usd: N, duration_ms: N, count: 9 }
    ...
  ```
- Keep existing `per_agent_tokens` and `per_agent_tools` output

## Scope

**In-scope:**
- `config/steps/mark-change-completed.yaml` (new, inline)
- `config/steps/archive-completed-change.yaml` (remove mutation responsibility)
- `config/workflows/_complete-phase.yaml` (insert mark-change-completed)
- `config/workflows/feature.yaml` (collapse 3 implement-phase review steps to 1)
- `config/steps/run-implement-review.yaml` (new; supersedes run-simplify + run-phase-review + run-feature-verification for the implement phase)
- `config/steps/execute-next-task.yaml` (append developer simplify pass after last task)
- `config/scripts/compute-swe-metrics.sh` (per-step aggregation pass + schema validator warning)
- Backfill archived features with zero-cost metrics (list as evidence in PR)
- Update metrics-query.sh if new fields should be queryable (may add per-step-cost named query later — decide during design)

**Out-of-scope:**
- Changes to the Agent tool itself
- JSONL ingest script changes
- DuckDB schema migration (new fields live inside `payload_json` and are query-accessible via `json_extract`)
- Touching `run-phase-review.yaml` for the specify phase (that stays)

## Acceptance criteria

- AC-1: `mark-change-completed` step exists, writes `status: completed`, `completed_at`, `archive_path`; runs before `compute-swe-metrics`
- AC-2: `archive-completed-change` becomes a pure move+commit step (no state mutation)
- AC-3: End-to-end run of any feature produces non-zero `metrics.cost.net_usd` and `metrics.tokens.input/output` in the archived state.yaml
- AC-4: All archived features with `cost.net_usd: 0` AND matching JSONL files are backfilled (list count + feature IDs in PR)
- AC-5: Implement phase runs exactly one reviewer-agent spawn (not three); AC verification + 5-dimension scoring + fix-task generation all happen in that one spawn
- AC-6: Developer simplify pass runs after last task in the phase; reviewer scores the simplified code
- AC-7: `metrics.per_step` block present in output, one entry per distinct `step_id` that executed; `per_step[*].total_tokens` sums to roughly `metrics.tokens.total`
- AC-8: State-schema validator emits a stderr warning (not an error) when step_history entries are missing `agent:` or `usage` fields; does not block the complete phase
- AC-9: `per_agent_tokens` covers all spawned agents (not just 2-3) on a fresh autopilot run, verified by cross-checking against step_history spawns

## Why one ticket

- All three tasks touch the implement→complete boundary
- B moves work out of reviewer spawns and A+C depend on what lands in state.yaml from those spawns — need to verify B doesn't break A or C
- Metrics backfill after A is the natural regression test for C (per-step aggregation)
- Single PR means one review cycle, one archive, one /learn cycle — not three

## Priority

High — zero-cost metrics (A) is silently corrupting the data warehouse, and C builds directly on the schema established by A. B is medium alone but fits naturally here and reduces per-feature agent cost going forward.

Estimate: ~10-12 tasks across the three areas. Suitable for a single autopilot iteration under --auto --agents.
