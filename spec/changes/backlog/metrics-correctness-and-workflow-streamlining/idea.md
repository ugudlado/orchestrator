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

### Task C: Capture metrics for EVERY step + per-step aggregation

The zero/undercount problem isn't primarily a script bug — it's that
step_history entries are written inconsistently. Agent-spawning steps
sometimes capture the Agent footer, sometimes don't. Inline steps
(create-worktree, load-project-context, validate-artifacts, etc.)
never record timing or tool counts because the dispatch loop only
collects usage for `agent:` steps. Result: aggregates cover maybe
30% of actual work.

Three sub-parts:

**C.1 — Required schema for every step_history entry**

Every entry (agent-spawning AND inline) records at minimum:
```yaml
- step_id: <id>
  phase: <phase>
  status: completed|skipped|failed
  started_at: <ISO>
  completed_at: <ISO>
  usage:
    duration_ms: <N>              # always (completed_at - started_at)
    tool_uses: <N>                # always (inline steps count Bash/Read/Edit/etc)
    tools: { ToolName: N, ... }   # always, empty map if zero
    total_tokens: <N>             # agent steps only (from Agent footer)
    input_tokens: <N>             # agent steps, proxy only
    output_tokens: <N>             # agent steps, proxy only
    cost_usd: <N>                  # agent steps, proxy only
  agent: <name>                   # only when step contract declares agent:
  runtime_agent: <name>            # only on compatibility fallback
```

Rules:
- The dispatch loop MUST write a complete `usage:` block for every step, agent or inline
- For inline steps: `duration_ms = completed_at - started_at`; `tool_uses` + `tools` from the Bash/Read/Edit/etc calls made during the step
- For agent steps: the existing Agent-footer extraction plus the inline-step defaults (so a failed agent spawn still gets duration)
- Entries with `status: skipped` have empty `usage` but still include `duration_ms: 0`

**C.2 — Validator** (stderr warning, non-blocking):
- Runs inside `mark-change-completed` before the state is locked
- Flags any step_history entry missing required fields per the schema above
- Warning to stderr; does not block the complete phase
- Emit a one-line summary: "N of M step_history entries have complete usage blocks"

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
- Sum check: `sum(per_step[*].total_tokens)` ≈ `metrics.tokens.total` (document the math — cache tokens aren't per-step, they're session-level)

**C.4 — Orchestrate skill update**:
- Update `orchestrate.md` to codify the full usage schema as the contract for step_history writes
- Add a CONVENTIONS.md § entry: "Every step writes a complete usage: block"
- Keep the existing proxy/native extraction logic; add inline-step timing as the default source

### Task D: Persist metrics in normalized DuckDB tables

Currently `features.payload_json` is the only place step-level and
per-agent metrics live. Extracting them via `json_extract` on every
query works but is fragile: `payload_json` is a convenience blob,
not durable truth. If disk-cleanup ever trims or drops that column
(realistic once the fleet grows), historical metrics vanish silently.

Normalized tables populated at ingest time make metrics a first-class
asset. Four deliverables:

**D.1 — New tables**:

```sql
CREATE TABLE IF NOT EXISTS step_history (
  repo_root     VARCHAR NOT NULL,
  change_id     VARCHAR NOT NULL,
  step_ord      INTEGER NOT NULL,          -- position in step_history array (0-indexed)
  step_id       VARCHAR NOT NULL,
  phase         VARCHAR,
  status        VARCHAR,
  agent         VARCHAR,                    -- NULL for inline steps
  runtime_agent VARCHAR,                    -- set on compatibility fallback
  started_at    VARCHAR,
  completed_at  VARCHAR,
  duration_ms   BIGINT,
  tool_uses     BIGINT,
  tools_json    VARCHAR,                    -- {"Read": 5, "Bash": 2, ...} — per-tool counts
  total_tokens  BIGINT,
  input_tokens  BIGINT,
  output_tokens BIGINT,
  cost_usd      DOUBLE,
  retry_round   INTEGER,                    -- NULL unless a retry
  ingested_at   TIMESTAMP DEFAULT(current_timestamp),
  PRIMARY KEY (repo_root, change_id, step_ord),
  FOREIGN KEY (repo_root, change_id) REFERENCES features(repo_root, change_id)
);

CREATE TABLE IF NOT EXISTS per_agent_metrics (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  agent        VARCHAR NOT NULL,
  total_tokens BIGINT,
  cost_usd     DOUBLE,
  duration_ms  BIGINT,
  tool_uses    BIGINT,
  step_count   INTEGER,
  ingested_at  TIMESTAMP DEFAULT(current_timestamp),
  PRIMARY KEY (repo_root, change_id, agent),
  FOREIGN KEY (repo_root, change_id) REFERENCES features(repo_root, change_id)
);

CREATE TABLE IF NOT EXISTS per_step_metrics (
  repo_root    VARCHAR NOT NULL,
  change_id    VARCHAR NOT NULL,
  step_id      VARCHAR NOT NULL,
  total_tokens BIGINT,
  cost_usd     DOUBLE,
  duration_ms  BIGINT,
  tool_uses    BIGINT,
  exec_count   INTEGER,
  ingested_at  TIMESTAMP DEFAULT(current_timestamp),
  PRIMARY KEY (repo_root, change_id, step_id),
  FOREIGN KEY (repo_root, change_id) REFERENCES features(repo_root, change_id)
);
```

Tool breakdowns stay as JSON in `step_history.tools_json` — a per-tool
table (one row per step+tool) is over-normalization for the current
query patterns. If we ever need `SELECT tool_name, SUM(count)`, split
it then.

**D.2 — Ingest logic in `register-repo.sh`**:

For each archived state.yaml being ingested:
1. Parse state.yaml into JSON (existing yq step)
2. Upsert the `features` row (existing logic, including `payload_json` for backward compat)
3. **New**: DELETE then INSERT into `step_history` for this `(repo_root, change_id)` — every entry in `step_history[]` becomes one row, indexed by `step_ord`
4. **New**: DELETE then INSERT into `per_agent_metrics` — derived from `metrics.per_agent_tokens`
5. **New**: DELETE then INSERT into `per_step_metrics` — derived from `metrics.per_step`

The DELETE+INSERT pattern keeps re-ingest idempotent (same input → same
rows). A feature's row count in each table is deterministic.

**D.3 — `payload_json` stays for now, can be dropped later**:

Keep `features.payload_json` for this ticket — it's still the fallback
for fields we haven't normalized (spec references, prediction accuracy,
review findings text, etc.). A follow-up ticket can normalize those and
drop the column when every consumer has migrated.

**D.4 — Backfill existing archived features**:

Re-run `register-repo.sh --rebuild` against `/Users/spidey/code/orchestrator`
after Task C lands. The 12 existing archived features get their step_history
rows populated (agent-spawning steps only, since inline steps pre-Task-C
don't have the data in state.yaml — that's expected; they're historical).

**D.5 — New named queries in metrics-query.sh**:
- `step-cost-hotspots` — `SELECT step_id, SUM(cost_usd) FROM per_step_metrics GROUP BY step_id ORDER BY 2 DESC LIMIT 10`
- `agent-cost-hotspots` — same for per_agent_metrics
- `agent-duration-outliers` — agents with avg duration > 2x fleet median
- Keep existing 5 named queries untouched (they still work — `features` table is unchanged)

**Why normalized tables (not views)**

- Durability: metrics survive even if `payload_json` is dropped/cleaned
- Joinability: `SELECT f.schema, SUM(s.cost_usd) FROM features f JOIN step_history s ...`
- Performance: simple column scans vs nested JSON extraction on every query
- Clear schema: consumers see typed columns, not "parse this JSON path"
- Standard SQL: works with any tool that connects to DuckDB (external dashboards, notebooks)

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
- `config/scripts/register-repo.sh` — add table DDL (idempotent `CREATE TABLE IF NOT EXISTS`); add ingest logic for `step_history`, `per_agent_metrics`, `per_step_metrics`
- `config/scripts/metrics-query.sh` — add 3 new named queries against new tables
- `config/scripts/metrics-query.test.sh` — fixture DB now populates new tables; tests for new queries
- Backfill via `register-repo.sh --rebuild` after Task C lands — existing 12 archived features re-ingest into new tables

**Out-of-scope:**
- Changes to the Agent tool itself
- JSONL ingest script changes
- Dropping `payload_json` column (that's a follow-up once all consumers have migrated)
- Normalizing spec/design/review text fields into tables (follow-up)
- Touching `run-phase-review.yaml` for the specify phase (that stays)

## Acceptance criteria

- AC-1: `mark-change-completed` step exists, writes `status: completed`, `completed_at`, `archive_path`; runs before `compute-swe-metrics`
- AC-2: `archive-completed-change` becomes a pure move+commit step (no state mutation)
- AC-3: End-to-end run of any feature produces non-zero `metrics.cost.net_usd` and `metrics.tokens.input/output` in the archived state.yaml
- AC-4: All archived features with `cost.net_usd: 0` AND matching JSONL files are backfilled (list count + feature IDs in PR)
- AC-5: Implement phase runs exactly one reviewer-agent spawn (not three); AC verification + 5-dimension scoring + fix-task generation all happen in that one spawn
- AC-6: Developer simplify pass runs after last task in the phase; reviewer scores the simplified code
- AC-7: `metrics.per_step` block present in output; one entry per distinct `step_id` that executed (agent-spawning AND inline); `per_step[*].total_tokens` sums to roughly `metrics.tokens.total` (minus session-level cache tokens)
- AC-8: Every step_history entry (agent AND inline) has a complete `usage:` block with at least `duration_ms` and `tool_uses`; agent steps also have token/cost fields when available
- AC-9: State-schema validator emits a stderr warning (not an error) when step_history entries are missing required fields; does not block the complete phase; prints coverage ratio ("N/M entries complete")
- AC-10: `per_agent_tokens` covers all spawned agents (not just 2-3) on a fresh autopilot run — verified by cross-checking against `grep '^    agent:' state.yaml | sort -u`
- AC-11: `orchestrate.md` and `CONVENTIONS.md` document the required usage: schema so future step writers know the contract
- AC-12: `register-repo.sh` creates `step_history`, `per_agent_metrics`, `per_step_metrics` tables via `CREATE TABLE IF NOT EXISTS`; repeated runs are idempotent
- AC-13: Each archived feature ingest populates the three new tables; re-ingest (DELETE+INSERT by PK) produces identical row counts and values
- AC-14: Queries against the new tables work without `json_extract`: `SELECT agent, total_tokens FROM per_agent_metrics WHERE change_id = 'X'` returns one row per agent
- AC-15: `metrics-query.sh` supports `step-cost-hotspots`, `agent-cost-hotspots`, `agent-duration-outliers` named queries backed by the new tables
- AC-16: All existing metrics-query.sh tests still pass (27+); new tests added for the 3 new queries + table population
- AC-17: Backfill run (`register-repo.sh --rebuild` on orchestrator repo) populates `step_history`, `per_agent_metrics`, `per_step_metrics` for all 12 existing archived features; row counts documented in PR

## Why one ticket

- All three tasks touch the implement→complete boundary
- B moves work out of reviewer spawns and A+C depend on what lands in state.yaml from those spawns — need to verify B doesn't break A or C
- Metrics backfill after A is the natural regression test for C (per-step aggregation)
- Single PR means one review cycle, one archive, one /learn cycle — not three

## Priority

High — zero-cost metrics (A) is silently corrupting the data warehouse, and C builds directly on the schema established by A. B is medium alone but fits naturally here and reduces per-feature agent cost going forward.

Estimate: ~10-12 tasks across the three areas. Suitable for a single autopilot iteration under --auto --agents.
