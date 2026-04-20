# Tasks: sub-agent-token-ingest

Light mode — TDD not required. Tasks are small, verifiable, in order.

- [x] T-1 — completed 2026-04-20 via developer agent (a07b79d6d91a9e3cb)
- [x] T-2 — completed 2026-04-20 via developer agent
- [x] T-3 — completed 2026-04-20 via developer agent
- [x] T-4 — completed 2026-04-20 inline
- [x] T-5 — completed 2026-04-20 inline (pre-seeded at specify phase)
- [ ] T-6 — self-host verification (runs during complete phase)

## T-1: Add `_ingest_subagents_main` to orchestrator CLI

**File**: `~/.local/bin/orchestrator`

**Change**:
- Add `_ingest_subagents_main(args)` function — structure mirrors `_ingest_driver_main`.
- Algorithm per design.md §1.
- Use `discover_subagents()`, `extract_agent_usage()` from `orchestrator_next.jsonl_usage`.
- Use `upsert_synthetic_event`, `ensure_schema` from `orchestrator_next.upsert`.
- Use `_compute_cost_usd` from `orchestrator_next.record`.
- Dedup: SELECT existing row before upsert; skip when `input_tokens > 0`.
- Read `agentType` from `subagents/agent-<id>.meta.json`; fallback `"subagent-unknown"`.
- Print JSON summary to stdout; exit 0 on success.
- Update `_usage()` to list the new subcommand.
- Update `main()` dispatch to route `ingest-subagents` → `_ingest_subagents_main`.

**Verify**:
- `orchestrator --help` shows new subcommand.
- Dry-run: pick any recent driver session-id with a populated subagents/ dir; run subcommand; inspect DuckDB rows.
- Rerun confirms idempotency (second run reports agents as skipped).

**Traces**: FR-1, FR-2, FR-3, FR-4, FR-6, AC-1, AC-2.

## T-2: Add `ingest-subagents-auto.py` inline script

**File**: `scripts/inline/ingest-subagents-auto.py`

**Change**:
- Copy structure of `ingest-driver-auto.py`.
- Reuse identical session-id resolution (TMPDIR UUID → JSONL scan with started_at/completed_at window).
- Shell out to `orchestrator ingest-subagents --change-id <cid> --session-id <sid>`.
- Fail-soft: exit 0 with `{"skipped": true, "reason": ...}` on any error.
- Print `{"ingest_subagents_result": {...}}` on success.

**Verify**:
- Script runs standalone: `python scripts/inline/ingest-subagents-auto.py .state/sub-agent-token-ingest/state.yaml`.
- Output is valid JSON.

**Traces**: FR-5.

## T-3: Add step contract `ingest-subagents-auto.yaml`

**File**: `config/steps/ingest-subagents-auto.yaml`

**Change**:
- Structurally identical to `ingest-driver-auto.yaml`.
- `agent: inline`, `run: $ORCHESTRATOR_HOME/scripts/inline/ingest-subagents-auto.py`.
- `inputs: [state_yaml_path]`, `outputs: [ingest_subagents_result]`.
- One rule: "Fail-soft. Never block archival if subagent JSONLs missing."

**Verify**:
- YAML parses.
- `orchestrator next` on a state.yaml with `next_step.step_id=ingest-subagents-auto` returns `action: run_inline` with the correct `run` path.

**Traces**: FR-5.

## T-4: Wire into `_complete-phase.yaml`

**File**: `config/workflows/_complete-phase.yaml`

**Change**: insert `- ingest-subagents-auto` immediately after `- ingest-driver-auto` in the `steps:` list.

**Verify**:
- YAML parses.
- New feature workflows (future) include the step automatically.

**Traces**: FR-5.

## T-5: Add `ingest-subagents-auto` to this feature's state.yaml

**File**: `.state/sub-agent-token-ingest/state.yaml`

**Change**: add `- ingest-subagents-auto` to `workflow_plan.complete.active` immediately after `ingest-driver-auto`. Required for self-hosting verification (AC-3).

**Verify**:
- `orchestrator next` dispatches it during complete phase.

**Traces**: AC-3.

## T-6: Self-host verification

**Action**: during complete phase of this feature, `ingest-subagents-auto` runs and ingests this feature's own sub-agent JSONLs. Then:

```sql
SELECT agent_name, COUNT(*) AS rows, SUM(input_tokens) AS in_tok, SUM(cost_usd) AS cost
FROM step_events
WHERE change_id='sub-agent-token-ingest' AND phase='meta'
GROUP BY agent_name ORDER BY cost DESC;
```

Expect ≥1 row beyond `driver-loop` (will include at least `ideator` for step 1 artifact drafting, possibly more).

**Verify**:
- Query returns ≥2 distinct `agent_name` values.
- `orchestrator cost --change-id sub-agent-token-ingest --by agent` shows the breakdown.

**Traces**: AC-3.
