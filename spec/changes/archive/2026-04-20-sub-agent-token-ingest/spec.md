---
feature-id: sub-agent-token-ingest
linear-ticket: null
---

# Specification: Auto-ingest sub-agent JSONL tokens into step_events

## Motivation

`orchestrator ingest-driver` captures the parent Claude Code session's tokens (driver-loop) at complete phase, but every `Agent({subagent_type: ...})` call also produces a JSONL at `~/.claude/projects/<slug>/<driver-uuid>/subagents/agent-<id>.jsonl`. These sub-agent JSONLs are not ingested, so step_events undercounts real feature cost by 1.5–2× whenever sub-agents are spawned. Example: single-source-metrics-via-step-events reported $193.96 but had 36 uningested sub-agent spawns.

## What Changes

A new `orchestrator ingest-subagents` subcommand walks the subagents/ directory for a given driver session, reads each `agent-<id>.meta.json` sidecar for the `agentType`, aggregates the paired `agent-<id>.jsonl` usage via the existing `extract_agent_usage()`, and upserts one synthetic step_events row per sub-agent. A new complete-phase step (`ingest-subagents-auto`) invokes the subcommand alongside `ingest-driver-auto`.

## Requirements

### Functional

1. **FR-1**: `orchestrator ingest-subagents --change-id <cid> --session-id <driver-uuid>` discovers every `agent-<id>.jsonl` under `~/.claude/projects/<slug>/<driver-uuid>/subagents/` and upserts one step_events row per sub-agent.
2. **FR-2**: Each upserted row uses `phase="meta"`, `step_id=f"subagent-{agent_id}"`, `agent_name=<agentType from meta.json>` (falling back to `"subagent-unknown"` when meta.json is missing or malformed).
3. **FR-3**: The subcommand is idempotent via the step_events primary key `(repo_root, change_id, phase, step_id, attempt)` — rerunning is a no-op with identical JSONL state.
4. **FR-4**: When a sub-agent JSONL has no usable assistant turns, the row is skipped (no empty rows written). The subcommand prints a JSON summary `{"ingested": N, "skipped": M, "agents": [...]}` to stdout.
5. **FR-5**: A new inline step `ingest-subagents-auto` runs in the complete phase after `ingest-driver-auto`. It resolves the driver session-id the same way `ingest-driver-auto.py` does (two-path resolution: state.yaml then env/cwd-derived fallback), then invokes the subcommand.
6. **FR-6**: Dedup — if a step_events row already exists for `(phase="meta", step_id="subagent-<id>")` with non-zero `input_tokens`, skip the upsert for that agent. This protects against future callers that record sub-agent usage via `orchestrator record` directly.

### Non-Functional

1. **NFR-1**: Pure-read on JSONL; no mutation of `~/.claude/projects/`.
2. **NFR-2**: Adds ≤ ~150 LOC across CLI, inline script, and step contract.

## Architecture

- New function `_ingest_subagents_main(args)` in `~/.local/bin/orchestrator` (mirrors `_ingest_driver_main` structure).
- New inline script `scripts/inline/ingest-subagents-auto.py` — resolves driver session-id, shells out to `orchestrator ingest-subagents`.
- New step contract `config/steps/ingest-subagents-auto.yaml`.
- Schema registration for step ordering: add `ingest-subagents-auto` to `_complete-phase.yaml` immediately after `ingest-driver-auto`, and to every active state.yaml workflow_plan.complete.active list via schema default (backfill not required for archived changes).

### File modification table

| File | Change |
|---|---|
| `~/.local/bin/orchestrator` | New `_ingest_subagents_main`; dispatch in `main()`; update `_usage()` |
| `scripts/inline/ingest-subagents-auto.py` | New inline step script |
| `config/steps/ingest-subagents-auto.yaml` | New step contract |
| `config/workflows/_complete-phase.yaml` | Add `ingest-subagents-auto` after `ingest-driver-auto` |
| `.state/sub-agent-token-ingest/state.yaml` | Add `ingest-subagents-auto` to complete.active (self-host) |

## Test Strategy

Light mode — TDD not required. Verification is evidence-based:

1. Manual: run `orchestrator ingest-subagents --change-id sub-agent-token-ingest --session-id <current-driver-uuid>` and confirm DuckDB rows exist for this feature's own sub-agent spawns.
2. SQL check: `SELECT agent_name, COUNT(*), SUM(input_tokens) FROM step_events WHERE change_id='sub-agent-token-ingest' AND phase='meta' GROUP BY agent_name` — expect at least one non-driver-loop row.
3. Idempotency: run the subcommand twice, confirm row count unchanged and `input_tokens` identical.
4. End-to-end: complete phase runs `ingest-subagents-auto` successfully; final `orchestrator cost --change-id sub-agent-token-ingest --by agent` lists sub-agent roles distinct from driver-loop.

## Acceptance Criteria

- AC-1: Given a driver session with N sub-agent JSONLs, when `orchestrator ingest-subagents` is invoked, then N step_events rows exist with matching `agent_name` values derived from meta.json. [traces: FR-1, FR-2]
- AC-2: Given a sub-agent row already present with non-zero tokens, when the subcommand reruns, then that row is not overwritten and the summary reports it as skipped. [traces: FR-3, FR-6]
- AC-3: Given this feature's own complete phase, when `ingest-subagents-auto` runs, then `orchestrator cost --change-id sub-agent-token-ingest --by agent` shows ≥1 sub-agent role besides `driver-loop`. [traces: FR-5, NFR-1]

## Alternatives Considered

**Alternative 1: add a `kind="subagent"` column to step_events**
Rejected. `phase="meta"` already partitions synthetic rows from real step history; adding a column means a schema migration and updating every rollup query in cost_report.py/metrics_report.py. The phase-based split is free.

**Alternative 2: fold sub-agent ingestion into `ingest-driver-auto`**
Rejected. Keeping them as separate steps preserves single-responsibility — a failure in one doesn't block the other, and the dispatch log names which ingest ran.

**Alternative 3: capture sub-agent usage at `orchestrator record` time (as suggested in the ingest-driver docstring)**
Rejected for this change. Requires teaching every Agent-spawning caller to pass agent_id into its record payload — much broader blast radius than scoped for a Medium chore. Complete-phase batch ingestion is adequate until a per-step use case emerges.

## Impact

- No breaking changes. Additive CLI subcommand and additive complete-phase step.
- Historical data (archived features) remain uningested. Backfill is explicitly out of scope.
- `orchestrator cost` reports for new features will show higher totals as sub-agent cost is now counted — this is the intended correction, not a regression.

## Decisions

- **Row shape**: `phase="meta"`, `step_id=f"subagent-{agent_id}"`, `agent_name=<agentType>`. Mirrors driver-loop convention.
- **Agent name source**: `agent-<id>.meta.json` sidecar, key `agentType`. Fallback `"subagent-unknown"` when sidecar missing.
- **Dedup**: skip upsert when existing row has non-zero `input_tokens`.
- **Backfill**: out of scope. Only forward-looking ingestion.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
