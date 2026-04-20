# Design: sub-agent-token-ingest

## Approach

Mirror `ingest-driver` end-to-end: a new `orchestrator ingest-subagents` subcommand plus a new `ingest-subagents-auto` complete-phase step that resolves the driver session-id and shells out to the subcommand.

## Components

### 1. `_ingest_subagents_main(args)` in `~/.local/bin/orchestrator`

Signature mirrors `_ingest_driver_main`:

```
orchestrator ingest-subagents --change-id <cid> --session-id <driver-uuid>
```

Algorithm:
1. Parse flags; fail `_usage()` if missing.
2. Resolve `repo_root` via `git rev-parse --show-toplevel`, fallback `os.getcwd()`.
3. Call `discover_subagents(repo_root, session_id)` (exists) → list of agent_ids.
4. Open DuckDB at `$METRICS_DB` or `<repo_root>/metrics.duckdb`; `ensure_schema(db)`.
5. For each `agent_id`:
   - Read sidecar `subagents/agent-<id>.meta.json` → `agentType`. Fallback `"subagent-unknown"`.
   - Query existing row: `SELECT input_tokens FROM step_events WHERE repo_root=? AND change_id=? AND phase='meta' AND step_id=? AND attempt=1`. If row exists with `input_tokens > 0`, skip (dedup).
   - Call `extract_agent_usage(repo_root, agent_id, driver_session_hint=session_id)`. If empty, skip (no assistant turns).
   - Compute cost: `_compute_cost_usd(agentType, usage)` — reuses the same route→pricing pipeline driver-loop uses.
   - Call `upsert_synthetic_event(db, ctx, agent_name=agentType, step_id=f"subagent-{agent_id}", phase="meta", usage=usage)`.
6. Print JSON summary `{"ingested": N, "skipped": M, "agents": [...]}`.

### 2. `scripts/inline/ingest-subagents-auto.py`

Identical session-id resolution as `ingest-driver-auto.py` (TMPDIR UUID → JSONL scan). Then shells `orchestrator ingest-subagents` with resolved session_id. Fail-soft: exit 0 with `{"skipped": true, "reason": ...}` on any error, matching driver-auto behavior.

Factoring decision: **duplicate the ~40 lines of session-id resolution rather than extract a shared helper**. Reason: only two callers, and the resolution logic is stable. Extracting now adds an import surface for a shared module that doesn't exist. Acceptable duplication per --light "no scope creep" rule.

### 3. `config/steps/ingest-subagents-auto.yaml`

New inline step contract, structurally identical to `ingest-driver-auto.yaml`:

```yaml
id: ingest-subagents-auto
version: 1
agent: inline
run: $ORCHESTRATOR_HOME/scripts/inline/ingest-subagents-auto.py
inputs: [state_yaml_path]
outputs: [ingest_subagents_result]
rules:
  - Fail-soft. Never block archival if subagent JSONLs missing.
```

### 4. `config/workflows/_complete-phase.yaml`

Add `ingest-subagents-auto` immediately after `ingest-driver-auto`:

```yaml
  - compute-prediction-accuracy
  - run-learn-cycle
  - mark-change-completed
  - ingest-driver-auto
  - ingest-subagents-auto   # NEW
  - ingest-feature-metrics
  - compute-swe-metrics
  ...
```

### 5. Self-hosting

This feature's own state.yaml `workflow_plan.complete.active` must include `ingest-subagents-auto` so we dogfood it. Add it before running the complete phase.

## Data flow

```
complete phase
  → ingest-driver-auto.py   → orchestrator ingest-driver   → upsert_synthetic_event(driver-loop)
  → ingest-subagents-auto.py → orchestrator ingest-subagents → discover_subagents()
                                                            → for each: read meta.json
                                                                        extract_agent_usage()
                                                                        dedup SELECT
                                                                        _compute_cost_usd()
                                                                        upsert_synthetic_event(<agentType>)
```

## Row shape (step_events)

| Column | Value |
|---|---|
| `repo_root` | `$REPO_ROOT` |
| `change_id` | from state.yaml |
| `phase` | `"meta"` |
| `step_id` | `f"subagent-{agent_id}"` (full agent_id, not truncated, since `agent_id` is already content-addressed) |
| `attempt` | `1` |
| `agent_name` | `<agentType>` from meta.json, fallback `"subagent-unknown"` |
| `status` | `"completed"` |
| `input_tokens`, `output_tokens`, `cache_*`, `duration_ms`, `model`, `turns`, `tool_calls_json` | from `extract_agent_usage()` |
| `cost_usd` | from `_compute_cost_usd(agentType, usage)` |
| `started_at`, `ended_at`, `artifacts_json`, `escalation_json` | NULL |

PK `(repo_root, change_id, phase, step_id, attempt)` guarantees idempotency. The dedup SELECT adds an explicit skip rather than relying on INSERT OR REPLACE — we want to preserve richer rows written by future `orchestrator record` callers.

## SQL validation

Verified against `step_events` DDL in `upsert.py`: columns `input_tokens`, `cost_usd`, `phase`, `step_id`, `agent_name`, `attempt` all exist and match driver-loop rows written by `ingest-driver` today. Spot-check:

```sql
SELECT phase, step_id, agent_name, input_tokens, cost_usd
FROM step_events
WHERE change_id='single-source-metrics-via-step-events' AND phase='meta';
-- Returns: phase=meta, step_id=driver-loop-turns, agent_name=driver-loop, ...
```

New rows will sit alongside at `phase=meta, step_id=subagent-<id>, agent_name=<agentType>`.

## Out of scope

- Backfill of archived features (spec Impact §).
- Extracting shared session-id resolver (see §2 factoring decision).
- Per-step sub-agent ingestion via `orchestrator record` (spec Alternative 3).
- A `kind` column on step_events (spec Alternative 1).

## Risks

- **Risk**: a future ingest path could write sub-agent rows with richer fields (e.g., `started_at` from actual step_history). Dedup SELECT avoids overwriting.
- **Risk**: meta.json format drift. Mitigation: single fallback `"subagent-unknown"` keeps ingestion resilient; consumers can still GROUP BY agent_name.
- **Risk**: driver session-id resolution fails for this very feature when run manually. Fail-soft return; user can run `orchestrator ingest-subagents` directly with the session-id they know.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
