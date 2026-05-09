# Diagnosis — ORC-39 / HL-304: Metrics Capture and Implement-Phase Streamlining

**Feature:** orc-39  
**Date:** 2026-05-08  
**Phase:** diagnose  
**Agent:** discoverer  

---

## Defect 1: Zero benchmark metrics (tokens_per_task, cost_per_task_usd, api_calls)

### Symptom

The archived `metrics:` block for hl-303 shows `api_calls: 0`, `tokens_per_task: 0`, `cost_per_task_usd: 0.0` and `cost_per_resolution_usd: 0.0`, while the same feature has `total_tokens: 290298` and `net_usd: 4.45` — clearly non-zero spend.

### Reproduction

```bash
# Confirmed on the live metrics.duckdb:
ORCHESTRATOR_HOME=~/.config/orchestrator
duckdb -readonly -json "$ORCHESTRATOR_HOME/metrics.duckdb" \
  -c "SELECT change_id, total_tokens, turns, tasks_total, tasks_completed, tokens_per_task, cost_per_task_usd FROM feature_report WHERE change_id='hl-303'"
# Result: total_tokens=1244400, turns=303, tasks_total=null, tasks_completed=null, tokens_per_task=0, cost_per_task_usd=0.0

# Confirm feature_metrics has no row for hl-303:
duckdb -readonly -json "$ORCHESTRATOR_HOME/metrics.duckdb" \
  -c "SELECT * FROM feature_metrics WHERE change_id='hl-303'"
# Result: []
```

Two distinct zero paths confirmed:

**Zero A — `tokens_per_task: 0`, `cost_per_task_usd: 0.0`:**  
`feature_report` view lines 189-196 (`0002_report_views.sql`) contain a denom-zero guard: when `fm.tasks_total` is NULL or zero, the CASE expression returns 0. For hl-303, `feature_metrics` has no row at all — `tasks_total` is NULL. The guard fires and all benchmark ratios output zero.

**Zero B — `api_calls: 0` in the archived state.yaml metrics block:**  
The archived `metrics:` block was written by `compute-swe-metrics` running at step 4 of `_complete-phase.yaml`. The FEATURE boundary (where `_write_subagent_events` commits subagent rows with `turns` populated) fires at step 6 (`remove-worktree`). When `compute-swe-metrics` queried `feature_report`, the subagent rows were not yet in the DB. The live DB now shows `turns: 303` because those rows were committed later during the same session. The archived `metrics:` block is a snapshot of the pre-boundary state.

### Root Cause

**Cause A (tasks_total = NULL):**  
`mark-change-completed.sh` directly writes `state.yaml` via Python `yaml.safe_dump` (line 34 of `scripts/inline/mark-change-completed.sh`) and calls `orchestrator done` only implicitly via the inline step runner. However, the Phase 5 `feature_metrics` write in `record.py` (lines 1292-1321) is only triggered when `orchestrator done` receives `step_id=mark-change-completed, status=completed`. Since `mark-change-completed.sh` does NOT call `orchestrator done` — it directly edits the file — the Phase 5 path never fires, and no `feature_metrics` row is written to DuckDB.

- `scripts/inline/mark-change-completed.sh`, lines 15-41: direct state.yaml write via Python (bypasses record.py entirely)
- `config/scripts/orchestrator_next/record.py`, lines 1292-1321: Phase 5 trigger — dead code path for current mark-change-completed

**Cause B (turns = 0 at compute-swe-metrics time):**  
`_complete-phase.yaml` step ordering places `compute-swe-metrics` at position 4 and `remove-worktree` at position 6. The FEATURE boundary in `record.py` (line 1332-1387) — which calls `_write_driver_session` and `_write_subagent_events` — fires only at `remove-worktree`. Subagent rows (which carry `turns` counts) are not in `step_events` when `compute-swe-metrics` queries `feature_report`. The archived metrics snapshot therefore has `api_calls: 0`.

- `~/.config/orchestrator/config/workflows/_complete-phase.yaml`: step 4 = compute-swe-metrics, step 6 = remove-worktree
- `config/scripts/orchestrator_next/record.py`, lines 1332-1387: FEATURE boundary write (subagent rows) fires at remove-worktree, not at compute-swe-metrics

### Impact

- `feature_report` view returns `tasks_total: null`, `tasks_completed: null` for all features completed since Phase 3 rewrite (Phase 5 code exists in record.py but is unreachable via mark-change-completed.sh).
- All benchmark columns (`tokens_per_task`, `tokens_per_resolution`, `cost_per_task_usd`, `cost_per_resolution_usd`) are zero for all archived features.
- `api_calls` / `turns` in the archived metrics block is always 0 or stale because the snapshot is taken before the FEATURE boundary write.
- The live DB has correct `turns` (post-boundary), but the archived `metrics:` key in state.yaml is stale.

### Existing Tests

No test asserts that `feature_metrics` has a non-null `tasks_total` row after mark-change-completed runs. `test_feature_metrics_trigger.py` exists in `config/scripts/orchestrator_next/tests/` but covers the Phase 5 record.py path, not the shell script bypass.

### Proposed Approach

Make mark-change-completed invoke `orchestrator done` as its terminal action (so Phase 5 fires), or replicate the `feature_metrics` write directly in mark-change-completed.sh. Separately, defer the compute-swe-metrics step to after remove-worktree, or re-query at archive time.

### Unresolved Questions

- Why does `mark-change-completed.sh` bypass `orchestrator done` when all other inline steps use it? Was this intentional for idempotency? The script has an idempotency guard (line 20-26) that skips re-writing if already completed.
- Can `compute-swe-metrics` be reordered to run after `remove-worktree` without breaking any dependent step?

---

## Defect 2: Inline-step and agent-step token attribution corrupted

### Symptom

All steps in `step_events` for hl-303 with `agent_name='inline'` have `NULL` for `input_tokens`, `output_tokens`, and `turns`. Steps that ran real LLM work (execute-next-task, run-learn-cycle, design-and-draft-artifacts) are attributed to `inline` agent in both step_history and DuckDB, despite their step contracts specifying `agent: developer`, `agent: workflow-improver`, etc.

```bash
# All inline-attributed steps have NULL tokens:
ORCHESTRATOR_HOME=~/.config/orchestrator
duckdb -readonly -json "$ORCHESTRATOR_HOME/metrics.duckdb" \
  -c "SELECT step_id, agent_name, input_tokens, output_tokens FROM step_events WHERE change_id='hl-303' AND agent_name='inline'"
# execute-next-task, run-learn-cycle, design-and-draft-artifacts: all have NULL output_tokens
```

### Root Cause

Two distinct mechanisms:

**Mechanism A — Architectural constraint (documented):**  
`config/scripts/orchestrator_next/record.py`, line 1081: `if status == "completed" and agent != "inline" and not has_agent_id:` — usage validation is bypassed when `agent == "inline"`. Project learning `inline-steps-are-tokenless` (2026-04-18) documents the root cause: the parent-context token counter is not accessible from within a Claude Code session running inline step contracts. `upsert.py` line 515 writes `usage.get("turns")` — which is `None` for inline payloads.

**Mechanism B — Agent misattribution (fixable):**  
`record.py`, line 1194: `"agent": payload.get("agent", "inline")` — the agent recorded in step_history comes from the payload, not from the step contract definition. For hl-303, `execute-next-task` (contract: `agent: developer`), `design-and-draft-artifacts`, and `diagnose` all recorded with `agent: inline` and sent only `input_tokens` (no `output_tokens`). The step contracts for these steps specify non-inline agents, meaning these steps ran within the driver session context and reported themselves as inline callers. This is NOT the architectural tokenless constraint — these steps do have token costs captured (e.g., execute-next-task attempt 1: `input_tokens: 51500, cost_usd: 0.7725`), but output_tokens are NULL because the payload omits them. The result is that `per_agent_tokens` in the feature_report aggregates all non-subagent spend under `inline` rather than the correct agent names.

### Impact

- `per_agent_tokens` JSON in `feature_report` shows all non-subagent costs attributed to `inline`, masking which agents (developer, discoverer, architect) drove which cost.
- `output_tokens` is always NULL for inline-attributed agent steps, making `input_output_ratio` calculations inaccurate.
- Check B validation (record.py line 1081) does not reject agent-step payloads that self-report as `inline` — the validator is bypassed.
- Steps: `execute-next-task`, `design-and-draft-artifacts`, `diagnose`, `run-learn-cycle`, and all complete-phase steps are affected.

### Existing Tests

`test_record_validation.py` line 195 (`test_accepts_inline_step_without_usage`) explicitly asserts that inline steps with empty usage are accepted. This test is correct for the architectural constraint, but there is no test asserting that non-inline step contracts cannot self-report as `agent: inline`.

### Proposed Approach

Cross-validate the payload `agent` field against the step contract's `agent` field in record.py, rejecting payloads where a non-inline contract reports `agent: inline` without an `agent_id`.

### Unresolved Questions

- Is the self-reporting as `inline` intentional for steps running inside the driver session? The driver loop may set `agent: inline` deliberately for steps it executes inline regardless of the contract's declared agent.
- How are steps dispatched that result in `agent: inline` in the payload for `execute-next-task`?

---

## Defect 3: Simplify pass and learn cycle cost impact

### Symptom

The `run-learn-cycle` step spawns a `workflow-improver` agent (which itself spawns `workflow-evaluator` running on opus), consuming real LLM budget on every feature completion. The FINAL-TASK SIMPLIFY PASS is embedded in `execute-next-task.yaml` lines 146-160 and runs in the same developer spawn, adding additional context-window cost on every final task execution.

### Reproduction

```bash
# Confirm workflow-improver subagent cost for hl-303:
ORCHESTRATOR_HOME=~/.config/orchestrator
duckdb -readonly -json "$ORCHESTRATOR_HOME/metrics.duckdb" \
  -c "SELECT step_id, agent_name, cost_usd FROM step_events WHERE change_id='hl-303' AND agent_name='workflow-improver'"
# Result: subagent-a1f27d6af12ab0c0b, workflow-improver, cost_usd=0.86

# Total hl-303 feature cost:
duckdb -readonly -json "$ORCHESTRATOR_HOME/metrics.duckdb" \
  -c "SELECT SUM(cost_usd) FROM step_events WHERE change_id='hl-303'"
# Result: 11.56 (workflow-improver = 7.4% of total)
```

The `run-learn-cycle.yaml` instruction (line 27) says `/learn` spawns `workflow-evaluator (opus)`. For hl-303, only one `workflow-improver` subagent row appears in `step_events` — no `workflow-evaluator` subagent is present. Either the workflow-evaluator ran without an `agent_id` being captured (so it was absorbed into the driver session totals), or it was not spawned. This makes the true learn-cycle cost higher than the 7.4% attributed to workflow-improver alone.

The FINAL-TASK SIMPLIFY PASS (execute-next-task.yaml lines 146-160) runs in the same developer agent spawn with no separate cost attribution. Its actual token cost is folded into the execute-next-task inline bucket.

### Root Cause

- `~/.config/orchestrator/config/steps/run-learn-cycle.yaml`, line 27: `/learn` spawns `workflow-evaluator (opus)` — the most expensive model in the stack.
- `~/.config/orchestrator/config/steps/execute-next-task.yaml`, lines 146-160: FINAL-TASK SIMPLIFY PASS is prose instruction embedded in the developer spawn, not a separate step contract; cannot be gated by a flag at the step level.
- `run-learn-cycle.yaml` line 15: an explicit learned rule states "Never skip compute-prediction-accuracy or run-learn-cycle steps during autopilot" — the learn cycle cannot be skipped via flag without modifying this contract.

### Impact

- Every feature completion unconditionally spawns at least one full LLM agent (workflow-improver) plus a potential opus-class workflow-evaluator.
- The simplify pass runs unconditionally on every final execute-next-task invocation with no mechanism to opt out.
- The 30-40% cost claim from the backlog description is not confirmed by hl-303 data: workflow-improver alone is 7.4% of feature cost. However, if workflow-evaluator (opus) ran without attribution, total learn-cycle cost may be higher. The 35% of cost attributed to inline steps includes all non-subagent work (explore, design, execute) — it is not specific to simplify/learn.

### Existing Tests

No test asserts that run-learn-cycle is skippable or that the simplify pass is flag-gated.

### Proposed Approach

Introduce a `flags.learn` guard in `run-learn-cycle.yaml` and a `flags.simplify` guard in `execute-next-task.yaml` (lines 146-160), with defaults that preserve current behavior but allow opt-out via workflow flags.

### Unresolved Questions

- Did `workflow-evaluator (opus)` actually run for hl-303? No `workflow-evaluator` subagent row appears in step_events. Was it absorbed into the workflow-improver session (nested spawn without agent_id capture), or was it skipped?
- Which model is currently resolved as `workflow-evaluator`? The step contract says opus, but this is prose, not a config field.
- Is the 30-40% cost claim based on older pre-Phase-3 data where the mechanism was different?

---

## Summary Table

| Defect | Status | Confirmed Via | Exact Location |
|--------|--------|---------------|----------------|
| D1a: tasks_total = NULL | Confirmed | live DB query + code trace | `mark-change-completed.sh:34` bypasses `record.py` Phase 5 |
| D1b: api_calls = 0 at snapshot | Confirmed | live DB vs archived metrics divergence | `_complete-phase.yaml` step ordering (compute-swe-metrics at step 4, FEATURE boundary at step 6) |
| D2a: inline-tokenless constraint | Confirmed (known) | `record.py:1081`, project learning 2026-04-18 | architectural — parent context not accessible |
| D2b: agent misattribution (fixable) | Confirmed | DB query (`agent_name=inline` on non-inline steps) | `record.py:1194` trusts payload agent over contract |
| D3: learn/simplify no flag gate | Confirmed | step contract text | `run-learn-cycle.yaml:15`, `execute-next-task.yaml:146-160` |

