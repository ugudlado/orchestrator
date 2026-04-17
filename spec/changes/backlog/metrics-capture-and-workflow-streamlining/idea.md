# Metrics capture + implement-phase streamlining

Bundled workflow improvement addressing three tightly-coupled issues discovered during HL-282 (autopilot-2026-04-17-001): metrics silently record zeros, per-agent/per-step breakdowns are incomplete, and the implement phase spawns three reviewer agents where one would do.

This ticket is scoped to **what lands in state.yaml and metrics output**. DuckDB ingestion of the new per-step / per-agent fields into normalized tables is a separate ticket (`duckdb-ingest-normalized-metrics-tables`, which depends on this one).

## Problem

### 1. Zero-cost metrics on every feature

`compute-swe-metrics.sh` produces zero input/output tokens and zero cost when run against an active (pre-archive) state.yaml. **Observed**: HL-282 initial write had `input: 0, output: 0, net_usd: 0, total: 124087` (state-only fallback). Re-run against the archived state.yaml after `completed_at` was present: `input: 22947, output: 231650, net_usd: 26.13`. Same JSONL files, both runs — the only difference was presence of `completed_at`.

**Root cause**: step ordering in `config/workflows/_complete-phase.yaml`:
```
compute-prediction-accuracy → run-learn-cycle → compute-swe-metrics → archive-completed-change
```
`archive-completed-change` writes `completed_at`, but `compute-swe-metrics` runs before it. `parse_session_jsonl` requires both `STARTED_AT` and `COMPLETED_AT` to compute the JSONL time window; when `COMPLETED_AT` is empty, `date -j -f` fails, the function returns 1, and the script silently falls through to the state-only token path (which only has aggregated `total_tokens` from the Agent footer).

The prior "fix" (commit a6a2e95) addressed TZ/slug bugs, not this ordering dependency.

### 2. Per-agent and per-step metrics are incomplete

`metrics.per_agent_tokens` shows only 2 of ~20 agents spawned in HL-282. `per_agent_tools` is empty `{}`. There is no `per_step` aggregation at all.

Root causes:
- Orchestrator doesn't validate step_history entries for required fields; hand-written or partial entries silently drop out of aggregation
- Inline steps (create-worktree, load-project-context, validate-artifacts, etc.) never capture timing or tool counts because the dispatch loop only collects usage for `agent:` steps
- `compute-swe-metrics.sh` has no awk pass that groups by `step_id`

### 3. Implement phase spawns three reviewer agents that overlap

Currently: `run-simplify` → `run-phase-review` → `run-feature-verification` — three sequential reviewer spawns, each re-reading the same files. AC verification happens in two of them.

Simplify also runs in the wrong agent's hands: a reviewer-agent simplify pass is an arm's-length evaluation. Simplification belongs with the developer who just wrote the code and has full context.

## Proposal

Three tasks. All three touch the implement→complete boundary and share state.yaml schema concerns.

### Task A: Fix step ordering + split mark-completed

New step `mark-change-completed` (inline, no agent):
- Writes `status: completed`, `completed_at`, `archive_path` to state.yaml
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

1. **Developer simplify pass** — after the last `execute-next-task` iteration, the developer runs a focused simplification pass over the worktree changes (same agent who wrote the code, still in-context). No new agent spawn.

2. **Single `run-implement-review`** step — one reviewer spawn that does:
   - Changed-file read + scoring on 5 dimensions
   - AC verification with evidence
   - Fix-task generation per Fix Task Protocol

Deprecate `run-simplify.yaml` and `run-feature-verification.yaml`.

### Task C: Capture metrics for EVERY step

The undercount problem isn't primarily a script bug — step_history entries are written inconsistently. Inline steps never record timing or tool counts. Agent-spawning steps sometimes miss the Agent footer.

Four sub-parts:

**C.1 — Required schema for every step_history entry**

Every entry (agent-spawning AND inline) records at minimum:
```yaml
- step_id: <id>
  phase: <phase>
  status: completed|skipped|failed
  started_at: <ISO>
  completed_at: <ISO>
  usage:
    duration_ms: <N>              # always
    tool_uses: <N>                # always
    tools: { ToolName: N, ... }   # always (empty map if zero)
    total_tokens: <N>             # agent steps only
    input_tokens: <N>             # agent steps, proxy only
    output_tokens: <N>             # agent steps, proxy only
    cost_usd: <N>                  # agent steps, proxy only
  agent: <name>                   # only when step contract declares agent:
  runtime_agent: <name>            # only on compatibility fallback
```

Rules:
- Dispatch loop MUST write a complete `usage:` block for every step, agent or inline
- Inline steps: `duration_ms = completed_at - started_at`; tool counts from Bash/Read/Edit/etc calls made during the step
- Agent steps: existing Agent-footer extraction plus inline-step defaults
- Entries with `status: skipped` have empty usage but still include `duration_ms: 0`

**C.2 — Validator** (stderr warning, non-blocking):
- Runs inside `mark-change-completed`
- Flags entries missing required fields
- Prints coverage ratio: "N of M step_history entries have complete usage blocks"

**C.3 — Per-step aggregation in compute-swe-metrics.sh**:
- New awk pass that groups by `step_id`
- Emit:
  ```yaml
  per_step:
    explore: { total_tokens: N, cost_usd: N, duration_ms: N, tool_uses: N, count: 1 }
    execute-next-task: { total_tokens: N, cost_usd: N, duration_ms: N, tool_uses: N, count: 9 }
    validate-artifacts: { duration_ms: N, tool_uses: N, count: 1 }    # inline, no tokens
    ...
  ```
- Keep existing `per_agent_tokens` and `per_agent_tools` output

**C.4 — Orchestrate skill update**:
- Update `orchestrate.md` to codify the full usage schema
- Add a CONVENTIONS.md § entry: "Every step writes a complete usage: block"
- Keep proxy/native extraction logic; add inline-step timing as the default source

## Scope

**In-scope:**
- `config/steps/mark-change-completed.yaml` (new, inline)
- `config/steps/archive-completed-change.yaml` (remove mutation responsibility)
- `config/workflows/_complete-phase.yaml` (insert mark-change-completed)
- `config/workflows/feature.yaml` (collapse 3 implement-phase review steps to 1)
- `config/steps/run-implement-review.yaml` (new)
- `config/steps/execute-next-task.yaml` (append developer simplify pass after last task)
- `config/scripts/compute-swe-metrics.sh` (per-step aggregation + validator warning)
- `config/steps/CONVENTIONS.md` and orchestrate.md (document usage schema)
- Backfill archived features with zero-cost metrics (list as evidence in PR)

**Out-of-scope:**
- Agent tool changes
- JSONL ingest script changes
- DuckDB schema changes (separate ticket `duckdb-ingest-normalized-metrics-tables`)
- Touching specify-phase `run-phase-review.yaml`

## Acceptance criteria

- AC-1: `mark-change-completed` step exists, writes `status: completed`, `completed_at`, `archive_path`; runs before `compute-swe-metrics`
- AC-2: `archive-completed-change` becomes a pure move+commit step (no state mutation)
- AC-3: End-to-end run of any feature produces non-zero `metrics.cost.net_usd` and `metrics.tokens.input/output` in the archived state.yaml
- AC-4: All archived features with `cost.net_usd: 0` AND matching JSONL files are backfilled (list count + feature IDs in PR)
- AC-5: Implement phase runs exactly one reviewer-agent spawn; AC verification + 5-dimension scoring + fix-task generation all happen in that one spawn
- AC-6: Developer simplify pass runs after last task; reviewer scores the simplified code
- AC-7: `metrics.per_step` block present; one entry per distinct `step_id` that executed (agent-spawning AND inline); `per_step[*].total_tokens` sums to roughly `metrics.tokens.total`
- AC-8: Every step_history entry has a complete `usage:` block with at least `duration_ms` and `tool_uses`; agent steps also have token/cost when available
- AC-9: State-schema validator emits a stderr warning when step_history entries are missing required fields; non-blocking; prints coverage ratio
- AC-10: `per_agent_tokens` covers all spawned agents (not just 2-3) on a fresh autopilot run
- AC-11: `orchestrate.md` and `CONVENTIONS.md` document the usage schema

## Why one ticket

- All three tasks touch the implement→complete boundary
- B moves work out of reviewer spawns; A+C depend on what lands in state.yaml from those spawns — verify B doesn't break A or C
- Metrics backfill after A is the natural regression test for C
- Single PR = one review cycle

## Priority

High — zero-cost metrics (A) silently corrupts the data warehouse; C ensures the state.yaml schema is ready for downstream normalization.

Blocks: `duckdb-ingest-normalized-metrics-tables`.
