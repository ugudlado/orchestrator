# Design: Metrics capture and implement-phase streamlining

## Context

Three confirmed defects in the orchestrator's metrics pipeline (see diagnose.md):

- **D1b**: `compute-swe-metrics` (step 4 of `_complete-phase.yaml`) queries `feature_report` BEFORE `_write_subagent_events` runs at the FEATURE boundary (step 6, `remove-worktree`). The archived `metrics:` snapshot captures `turns=0` even when subagent work occurred.
- **D2b**: `record.py` line 1194 trusts the caller-supplied `agent` field. Non-inline contracts (e.g. `execute-next-task`, `design-and-draft-artifacts`, `diagnose`) self-report as `agent: inline`. Live DB confirms: ALL rows for back-474 (most-recent feature) are `agent_name='inline'` with no subagent rows present at all.
- **D3**: `run-learn-cycle` and the FINAL-TASK SIMPLIFY PASS embedded in `execute-next-task.yaml` run unconditionally. No flag gate exists.

Discovery and live-DB verification additionally produced these load-bearing facts:

- Phase 5 (`feature_metrics` write triggered by `step_id=mark-change-completed, status=completed`) IS firing for recent features (back-474, orc-37, orc-34, document-or-script-state-seeding all have `feature_metrics` rows). hl-303 is missing one because it predates the worktree-artifact-split fix that landed in commit `a4de3bc`. **D1a as described in diagnose.md is incorrect**: the dispatcher already invokes `orchestrator done` after the inline shell script. The remaining issue is FR-6 (tasks_total non-null), which the HL-303 fix already addressed for new features.
- The flag-gate machinery in `config/flags.yaml` and `generate_plan.py` is the canonical pattern for skippable steps. `gates.<flag>.steps` filters `workflow_plan.<phase>.active`. No new code is needed in `generate_plan.py`.
- `step_events` PK is `(repo_root, change_id, phase, step_id, attempt, status)` (verified via `information_schema.columns`). Subagent rows have stable PKs across writes — re-writing at the FEATURE boundary after Phase 5 already wrote them is an idempotent upsert.

## Goals / Non-Goals

### Goals

- `feature_report.turns` reflects real subagent activity by the time `compute-swe-metrics` runs (FR-1, AC-1, AC-8).
- `step_events.agent_name` matches the step contract's declared agent for non-inline contracts (FR-2, AC-2, AC-3).
- `flags.learn` and `flags.simplify` provide opt-out gates with safe defaults (FR-3, FR-4, AC-4, AC-5, AC-7).
- Default behavior is unchanged for callers that don't set the new flags (NFR-3, AC-7).

### Non-Goals

- Per-step token measurement for inline steps (architectural limit, project learning 2026-04-18).
- Rewriting `compute-swe-metrics.sh` or `feature_report` view (current SQL is correct given the data).
- Rewriting historical `step_events` / `feature_metrics` rows for already-archived features.
- Replacing the `workflow-evaluator (opus)` model in run-learn-cycle (out-of-scope tuning).

## Approaches Considered

### Approach 1: Targeted fixes in record.py + flag-gate registration

Each defect maps to one file:

- D1b: extend Phase 5 (`record.py`, ~line 1305) to ALSO call `_write_subagent_events` (and `_write_driver_session`) inside the same atomic transaction.
- D2b: in `record.py` ~line 1194, look up the step contract's declared `agent` field; when the contract has a non-empty agent and the payload says `inline`, rewrite the entry's `agent` to the contract's agent and emit a stderr warning.
- D3a: register `gates.learn` in `config/flags.yaml`; the existing generate_plan.py gate evaluator filters it.
- D3b: register `behavioral.simplify` in `config/flags.yaml`; gate the prose clause in `execute-next-task.yaml` with a conditional the developer reads from `state.yaml.flags`.

Pros: smallest diff per defect, uses established patterns (gates registry, contract-driven validation), each change independently testable.

Cons: Phase 5 path becomes the de-facto FEATURE boundary, leaving the boundary at remove-worktree as a no-op. Acceptable per `step_events` PK idempotency.

### Approach 2: Reorder `_complete-phase.yaml` to put `compute-swe-metrics` AFTER `remove-worktree`

Pros: no record.py changes for D1b.

Cons: `archive-completed-change` reads `metrics:` from state.yaml and copies it; `remove-worktree` deletes the worktree's state directory. Reordering breaks the archive's metrics block.

Rejected.

### Approach 3: Hard-reject self-reported `agent: inline` payloads

Pros: forces callers to fix self-reporting at the source.

Cons: in-flight workflows break until every caller updates; the self-reporting may be intentional for steps that run inside the driver session (open question raised in diagnose.md).

Rejected — too aggressive for an open question.

### Selected Approach

**Approach 1** is selected. It:

- Uses the existing flag-gate registry (no new dispatcher code).
- Treats the contract as the source of truth for `agent`, surfacing drift via warning rather than rejection (backwards-compatible).
- Keeps record.py's transactional model intact: Phase 5 already runs inside `BEGIN/COMMIT`; we add two more idempotent calls inside the same tx.

## High-Level Design

### Architecture overview

```
                                 +--------------------------+
                                 |  config/flags.yaml       |
                                 |  + gates.learn           |
                                 |  + behavioral.simplify   |
                                 +-----------+--------------+
                                             |
                                             v
                workflow-init  --->  generate_plan.py
                                             |
                                  (filters run-learn-cycle out
                                   when flags.learn=false)
                                             |
                                             v
                                workflow_plan.complete.active
                                             |
                                             v
                  ... mark-change-completed --> compute-swe-metrics --> ...
                  (Phase 5: writes              (now sees subagent rows
                   feature_metrics +             in step_events)
                   subagent rows +
                   driver_session)
```

### Key abstractions

- **Phase 5 trigger** (`record.py`): the existing transactional write at `step_id=mark-change-completed, status=completed` is the boundary write. Subagent rows + driver session are committed here, in addition to feature_metrics.
- **Contract-agent lookup**: `_load_step_contract_agent(step_id)` returns the contract's declared `agent:` value (or empty string for truly inline contracts). Used as the source of truth for `entry["agent"]`.
- **Gate flag**: `gates.<flag>.steps` already filters `workflow_plan.<phase>.active`. Adding `gates.learn` is a registry edit, no code changes.
- **Behavioral flag for prose**: `behavioral.simplify` is read from `state.yaml.flags` by the developer agent at the FINAL-TASK SIMPLIFY PASS clause. The clause begins with "If `state.yaml.flags.simplify` is false, skip steps 10a-d."

## Low-Level Design

### Components

#### 1. record.py — Phase 5 boundary expansion

Current (lines 1289-1321):
```python
if step_id == "mark-change-completed" and status == "completed":
    fm_data = _resolve_feature_metrics(state_raw, change_id_val)
    db.execute("BEGIN")
    upsert_step_event(db, _step_entry, ctx)
    _write_feature_metrics(db, repo_root_val, change_id_val, fm_data)
    db.execute("COMMIT")
```

Proposed:
```python
if step_id == "mark-change-completed" and status == "completed":
    fm_data = _resolve_feature_metrics(state_raw, change_id_val)
    # Resolve subagent rows + driver session OUTSIDE BEGIN (JSONL parse).
    session = _resolve_driver_session(state_raw, change_id_val, db=db)
    subagent_rows = _resolve_subagent_rows(
        repo_root_val, change_id_val, session.get("session_id", "")
    )
    db.execute("BEGIN")
    upsert_step_event(db, _step_entry, ctx)
    _write_feature_metrics(db, repo_root_val, change_id_val, fm_data)
    _write_driver_session(db, repo_root_val, change_id_val, session)
    _write_subagent_events(db, repo_root_val, change_id_val, subagent_rows)
    db.execute("COMMIT")
```

The existing FEATURE boundary at `remove-worktree` (line 1370) is left unchanged; it re-runs `_write_driver_session` and `_write_subagent_events` as idempotent upserts (PK stability verified).

#### 2. record.py — agent-name rewrite

A new helper:
```python
def _resolve_contract_agent(step_id: str) -> str:
    """Return the step contract's declared 'agent:' field, or '' if absent.
    Looks up $ORCHESTRATOR_HOME/config/steps/<step_id>.yaml and the repo override
    at $REPO_ROOT/.orchestrator/steps/<step_id>.yaml. Repo override wins.
    Returns empty string if the contract has no 'agent:' key (truly inline)."""
```

Insertion point — before `entry` dict is built at line 1190, after `agent = payload.get("agent", "inline")` at line 1078:

```python
contract_agent = _resolve_contract_agent(step_id)
if contract_agent and agent == "inline":
    sys.stderr.write(
        f"[record] agent rewritten: step_id={step_id} "
        f"contract_agent={contract_agent} payload_agent=inline\n"
    )
    agent = contract_agent
```

The `entry["agent"]` line at 1194 changes to use the resolved local:
```python
"agent": agent,
```

The validation at lines 1081-1096 already runs against the resolved `agent`; if a non-inline contract had no usage and no agent_id, it correctly errors. Behaviour for genuine inline steps (no contract `agent:`) is unchanged.

#### 3. config/flags.yaml — gate + behavioral flag registration

Append to `gates:`:
```yaml
learn:        { steps: [run-learn-cycle], default: true }
```

Append to `behavioral:`:
```yaml
simplify:     { default: true,  description: "Run FINAL-TASK SIMPLIFY PASS in execute-next-task" }
```

Append to `cli:`:
```yaml
--no-learn:    { sets: { learn: false } }
--no-simplify: { sets: { simplify: false } }
```

#### 4. run-learn-cycle.yaml — amend learned-rule prose

The rule at line 15 currently reads:
> "Never skip compute-prediction-accuracy or run-learn-cycle steps during autopilot ... A `skipped: true` outcome is only valid when an enumerated gating flag is set (e.g. ux_design=false for ux steps)..."

Amendment: add `flags.learn=false` to the enumerated examples:
> "...e.g. ux_design=false for ux steps, learn=false for run-learn-cycle..."

This is a prose-only edit. The actual filtering happens in generate_plan.py via the gates registry; the rule update prevents future learn cycles from flagging `flags.learn=false` as a violation.

#### 5. execute-next-task.yaml — gate FINAL-TASK SIMPLIFY PASS

Insert at the top of the FINAL-TASK SIMPLIFY PASS section (line 146):

```yaml
  FINAL-TASK SIMPLIFY PASS (runs only when no unchecked tasks remain after step 6):
  If state.yaml flags.simplify is false (read flags from $WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml),
  skip steps 10a-d below and return without making a simplify commit.
  Otherwise:
  ... (existing 10a-d unchanged) ...
```

The developer agent reads flags from state.yaml in the same way it reads `flags.tdd_required` today — no new code path, just a prose conditional.

### Data flow

1. Workflow-init resolves flags (CLI > state > registry default), filters `run-learn-cycle` out of `workflow_plan.complete.active` if `flags.learn=false`.
2. Complete phase steps execute in order: `compute-prediction-accuracy → run-learn-cycle (if active) → mark-change-completed → compute-swe-metrics → archive-completed-change → remove-worktree`.
3. At `mark-change-completed`, Phase 5 fires: feature_metrics + subagent rows + driver_session all committed in one transaction.
4. At `compute-swe-metrics`, `feature_report` returns correct `turns` (sum of subagent rows now in step_events).
5. The metrics block is written to state.yaml; `archive-completed-change` copies state.yaml with the correct snapshot.
6. At `remove-worktree`, the FEATURE boundary write re-runs (no-op upsert).

### State management

- `flags.learn` and `flags.simplify` live in `state.yaml.flags` after workflow-init resolves them.
- `step_events`, `feature_metrics`, `driver_sessions` are written via `record.py` Phase 5 and the FEATURE boundary; both paths use `INSERT OR REPLACE` upserts keyed on stable PKs.
- No new state surface — all existing tables and registries.

### Error handling

- Phase 5 transaction rollback preserves: if `_resolve_subagent_rows` raises, the entire transaction is rolled back and `mark-change-completed` returns `feature_metrics_write_failed` (existing path). No partial writes.
- Agent-rewrite is fail-soft: if `_resolve_contract_agent` raises (e.g., contract file unreadable), it returns empty string and the original payload `agent` is preserved. A stderr warning logs the failure.
- Gate flag missing from registry: existing generate_plan.py path treats unrecognized flags as "always active" — no regression.

## Constraints

- `step_events` PK is `(repo_root, change_id, phase, step_id, attempt, status)` — boundary writes must remain idempotent under this key.
- Phase 5 write is fatal on failure (existing contract); adding subagent + driver_session writes inside the same tx makes their failure also fatal. This is acceptable: any failure here means the metrics are wrong, and silently swallowing them is what we're trying to fix.
- Repo overrides under `.orchestrator/steps/` must take precedence in `_resolve_contract_agent` per CLAUDE.md repo-wiring rules.
- Inline-tokenlessness remains: per-step tokens for inline contracts stay NULL.

## Trade-offs

- The Phase 5 transaction window grows by one JSONL parse and two more upserts. Acceptable: JSONL parsing happens OUTSIDE BEGIN; only upserts run inside. Tx window remains short.
- Agent rewrite emits a warning per affected step. For features with many inline-self-reported steps, this produces stderr noise. Acceptable: the noise is the point — it surfaces the drift until callers stop self-reporting.
- The FEATURE boundary at remove-worktree becomes redundant for subagent/driver writes. Acceptable: removing it requires deeper refactor (BoundaryKind logic); leaving it as no-op upsert is simpler and proves PK idempotency.

## Decisions

- **Trigger the FEATURE-equivalent write at Phase 5 (mark-change-completed)** → first complete-phase step where the workflow is logically done and JSONL is parseable → compute-swe-metrics sees correct subagent data.
- **Rewrite-with-warning for agent mismatch** → backwards-compatible, surfaces drift, fixes per_agent_tokens → no in-flight workflow breaks.
- **Use `gates.<flag>.steps` registry for `learn`** → reuses established machinery; no new code in generate_plan.py → small diff, no new test surface for plan filtering.
- **Behavioral flag (not gate) for `simplify`** → simplify is prose embedded in execute-next-task, not a separate step → behavioral flags are exactly the right primitive.
- **Amend run-learn-cycle's learned rule rather than remove it** → preserves the autopilot-must-not-skip principle; only carves out the explicit flag case → consistent with existing carve-out (`ux_design=false`).

## Open Questions

- Should the agent-rewrite emit a step_history warning (visible to operators) in addition to stderr? Current design is stderr-only; can be revisited if drift persists.
