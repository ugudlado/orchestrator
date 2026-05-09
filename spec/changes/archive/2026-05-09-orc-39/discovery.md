# Discovery Brief — ORC-39 / HL-304: Metrics Capture and Implement-Phase Streamlining

**Feature:** orc-39  
**Date:** 2026-05-08  
**Phase:** explore  
**Agent:** discoverer  

---

## What I Understand

Three workflow defects are causing incomplete or misleading cost/metrics data and excessive spend in the complete phase. The stated goal is to surface correct per-feature metrics in `compute-swe-metrics.sh` output and reduce the fraction of total feature cost consumed by the learn/simplify closure steps.

Underlying problem: the metrics pipeline was rewritten as part of the workflow-engine-as-state-machine refactor (Phases 1-3), but the backlog descriptions that spawned ORC-39 were written against an older code model. Before any design work begins, the defect descriptions must be reconciled against current code — two of the three do not match what the codebase currently does.

---

## What Already Exists

### Codebase Evidence

**Complete phase ordering** (`~/.config/orchestrator/config/workflows/_complete-phase.yaml`):
```
compute-prediction-accuracy
run-learn-cycle
mark-change-completed
compute-swe-metrics
archive-completed-change
remove-worktree
```

**`compute-swe-metrics.sh`** (`/Users/spidey/code/orchestrator/scripts/inline/compute-swe-metrics.sh`):
- Queries `feature_report` view via `duckdb -readonly -json`
- Reads only `change_id` from state.yaml (not `completed_at`)
- Exits with `ERROR: no events for change_id` if the view returns no rows
- Does NOT call `parse_session_jsonl` anywhere

**`mark-change-completed.sh`** (`/Users/spidey/code/orchestrator/scripts/inline/mark-change-completed.sh`):
- Stamps `completed_at`, `status: completed`, `archive_path` into state.yaml
- Also calls `upsert_feature_complexity` via inline Python
- Already runs BEFORE `compute-swe-metrics` in the current phase ordering

**`feature_report` view** (`/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/migrations/0002_report_views.sql`, lines 13-227):
- Aggregates from: `step_events`, `tool_calls`, `feature_metrics`, `feature_complexity`, `pricing`
- No dependency on `driver_sessions` (confirmed by reading the full view definition)
- The base CTE aggregates `COALESCE(SUM(se.input_tokens), 0)` — returns a row even with all-zero tokens

**`record.py`** (`/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py`):
- Line 1078: `agent = payload.get("agent", "inline")`
- Line 1081: usage validation skipped when `agent == "inline"`
- Lines 1336, 1307, 1366: `upsert_step_event` called for every step (inline and non-inline)
- Inline step `step_events` rows are written with `usage = entry.usage or {}` — NULL tokens when no usage dict provided
- FEATURE boundary fires when step_id is last in last phase's active list (`remove-worktree`)
- `_write_driver_session` and `_write_subagent_events` only called at the FEATURE boundary

**`upsert.py`** (`/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/upsert.py`):
- `upsert_step_event` writes `usage.get("input_tokens")` — NULL if key absent
- `step_events` PK: `(repo_root, change_id, phase, step_id, attempt, status)`

**`execute-next-task.yaml`** (`~/.config/orchestrator/config/steps/execute-next-task.yaml`, lines 146-160):
- The "simplify pass" is NOT a standalone step
- It is the `FINAL-TASK SIMPLIFY PASS` section embedded inside `execute-next-task.yaml`
- Runs in the same developer spawn after the final implementation task

**`run-learn-cycle.yaml`** (`~/.config/orchestrator/config/steps/run-learn-cycle.yaml`):
- `agent: workflow-improver`
- Spawns workflow-evaluator (claude-opus-4-5 or equivalent) + workflow-improver
- Non-blocking (best-effort), but the spawned agents are full LLM invocations

**Project learning** (`/Users/spidey/code/orchestrator/spec/project.yaml`):
- `inline-steps-are-tokenless` (2026-04-18): "Do not attempt per-step token capture for inline-executed steps in a Claude Code session — the parent-context token counter is not exposed to the running conversation."

---

## Defect Reconciliation Against Current Code

### Defect 1: Zero-cost metrics

**Stated description:** "compute-swe-metrics runs before archive-completed-change writes completed_at, causing parse_session_jsonl to produce zeros."

**What the code actually shows:**
1. `archive-completed-change` does NOT write `completed_at`. `mark-change-completed` writes it.
2. `mark-change-completed` already runs BEFORE `compute-swe-metrics` in `_complete-phase.yaml`.
3. `compute-swe-metrics.sh` does NOT call `parse_session_jsonl`. It queries `feature_report` via DuckDB.
4. `compute-swe-metrics.sh` does NOT read `completed_at` from state.yaml. It reads only `change_id`.
5. `feature_report` aggregates from `step_events` (not `driver_sessions`), so all agent step_events rows recorded earlier in the workflow are already present when compute-swe-metrics runs.

**Conclusion:** The stated defect mechanism does not exist in current code. The timing ordering described in the bug report was correct for an older code model but was fixed during the Phase 3 report-views rewrite. Whether zero-cost bugs are occurring in practice, and through what actual mechanism, requires a fresh repro from a real feature run.

### Defect 2: Per-agent/per-step metrics undercounted (inline steps tokenless)

**Stated description:** "inline steps never record a usage block."

**What the code shows:** Confirmed. `record.py` line 1081 bypasses the usage validation check for `agent == "inline"`. `upsert_step_event` writes NULL for `input_tokens` / `output_tokens` when `usage` is absent. Project learning `inline-steps-are-tokenless` documents the root cause: the parent-context token counter is not exposed to in-session inline step execution.

**Conclusion:** This defect description matches current code. Inline steps (mark-change-completed, compute-swe-metrics, archive-completed-change, remove-worktree, etc.) all contribute zero tokens to `step_events`, which means their true cost is unaccounted in per-step metrics. The feature total is correctly captured at the FEATURE boundary via `_write_driver_session` + JSONL parsing, but the per-step breakdown for inline steps is always zero.

### Defect 3: Simplify + learn steps oversized

**Stated description:** "simplify and learn steps routinely consume 30-40% of feature cost for minimal value."

**What the code shows:**
- There is no standalone `simplify.yaml` step contract.
- The simplify pass is embedded as `FINAL-TASK SIMPLIFY PASS` inside `execute-next-task.yaml` (lines 146-160), running in the same developer spawn after the final task.
- `run-learn-cycle` is a real step contract that spawns at least two full LLM agents (evaluator + improver).
- The `run-learn-cycle.yaml` rule explicitly says "Never skip compute-prediction-accuracy or run-learn-cycle steps during autopilot."

**Conclusion:** The defect description is architecturally accurate about the cost impact but inaccurate about scope — the "simplify step" is part of execute-next-task, not a separate step. Any streamlining approach must account for this embedded nature.

---

## Build or Reuse?

This is not a greenfield feature. All three defects are about modifying or correcting existing workflow steps, step contracts, and the inline step recording pipeline.

**Decision: Extend and correct existing code.** No new systems needed. The work is:
- For Defect 1: reproduce the actual zero-cost scenario before prescribing a fix; may be a no-op if already resolved
- For Defect 2: assess whether the tokenless constraint is fundamental (it is, per project learning) or whether an alternative measurement point exists
- For Defect 3: assess whether to make the simplify pass opt-in, extract it as a separate skippable step, or streamline run-learn-cycle's agent spawning

---

## Approaches Considered

### Approach A: Implement as described in the defect report

Core idea: Fix the completed_at timing issue for Defect 1, add usage blocks to inline steps for Defect 2, and create a standalone simplify step that can be skipped.

Build vs reuse: Extend existing shell scripts and record.py.

Pros: Directly addresses the change description.

Cons: Defect 1's stated mechanism does not match current code. Defect 2's root cause (tokenless inline execution) is a fundamental architectural constraint documented as a project learning — patching it requires a different measurement approach, not just adding a usage block. Defect 3's "simplify step" does not exist as a step contract.

Effort: Medium — but likely wasted effort on Defect 1 without a fresh repro.

### Approach B: Reconcile, then design (recommended)

Core idea: Accept the discovery finding that Defect 1's mechanism is stale. Produce a repro for Defect 1 using a real feature run to identify the actual failure path. For Defect 2, treat tokenless inline steps as a constraint and instead attribute inline costs via the FEATURE boundary driver-loop synthetic row. For Defect 3, make the simplify clause a flag-guarded opt-in within execute-next-task, and convert run-learn-cycle to a skippable step.

Build vs reuse: All modifications to existing step contracts and record.py.

Pros: Only fixes what is actually broken. Defect 2 and Defect 3 have clear, scoped fixes. Defect 1 gets a proper diagnosis first.

Cons: Defect 1 requires a diagnostic step before design.

Effort: Small-to-medium.

### Approach C: Accept inline tokenlessness as a documented constraint

Core idea: Do not attempt per-step token attribution for inline steps at all. Document this as an explicit limitation in the step contracts. Focus effort on Defect 3 (streamlining spend) since that has the highest ROI — reducing the absolute cost is more actionable than improving attribution of existing cost.

Build vs reuse: Mostly step contract documentation; one behavioral change to execute-next-task.

Pros: Smallest scope, highest ROI per developer hour.

Cons: Leaves Defect 1 unresolved. Removes a metric that is currently expected by downstream steps (compute-swe-metrics relies on per-step data).

Effort: Small.

---

## Recommendation

**Approach B, with Defect 1 requiring a diagnostic task before any implementation.**

The most valuable unblocked work is Defect 3 (streamlining run-learn-cycle and the simplify pass) because the cost impact is real and the scope is well-defined. Defect 2 (inline tokenlessness) should be documented as a known constraint with per-step attribution excluded for inline steps — the driver-loop row at FEATURE boundary already captures total session cost. Defect 1 needs a fresh repro to determine if it still exists in current code.

---

## Personas

- **Workflow operator:** runs autopilot on a feature and reviews the generated cost report; expects per-feature total cost and per-step breakdown to be non-zero and accurate
- **Workflow architect:** adjusts step contracts and phase ordering; needs to know which inline steps write to step_events and what their token values are
- **Cost auditor:** reads `metrics.duckdb` and `feature_report` view to compare cost across features; needs consistent data regardless of inline vs agent step composition

---

## Use Cases

**UC-1: Cost report shows non-zero totals for a completed feature** — Operator completes a feature and reads the generated metrics block in state.yaml; expects `tokens.total > 0` and `cost.net_usd > 0`.

**UC-2: Per-step breakdown excludes inline steps** — Operator reads `per_step` JSON in metrics; inline steps (mark-change-completed, compute-swe-metrics, archive-completed-change) show zero tokens; this is expected per constraint; agent steps show accurate token counts.

**UC-3: Simplify pass is opt-in** — Operator runs autopilot with `flags.simplify: false`; the FINAL-TASK SIMPLIFY PASS clause in execute-next-task is skipped; no simplify cost incurred.

**UC-4: Learn cycle is skipped when explicitly disabled** — Operator sets `flags.learn: false`; run-learn-cycle step is skipped (or emits a "skipped" step_history entry); no workflow-evaluator or workflow-improver agents are spawned.

**UC-E1: compute-swe-metrics returns empty rows** — If `feature_report` returns no rows for the change_id (no step_events written yet), the script exits non-zero with "no events for change_id". This is a hard failure, not a zero-cost result. The calling step contract should treat this as a non-blocking warning rather than a fatal error if the feature has only inline steps.

**UC-E2: Inline step writes null tokens** — record.py writes a step_events row for an inline step with NULL input_tokens/output_tokens. DuckDB COALESCE handles this as zero in feature_report. No data corruption; the constraint is architectural.

---

## Scope

### In scope
- Defect 1: Diagnose whether zero-cost bugs actually occur in current code; if so, identify actual failure path
- Defect 2: Document inline-steps-are-tokenless as an explicit step contract constraint; verify driver-loop synthetic row captures total session cost correctly
- Defect 3: Make simplify pass flag-guarded in execute-next-task.yaml; make run-learn-cycle skippable via a flag (e.g., `flags.learn`)

### Out of scope
- Rewriting compute-swe-metrics.sh to add JSONL parsing (the current DuckDB query is correct)
- Adding token measurement to inline step execution (fundamental constraint; parent context not accessible)
- Per-step cost attribution for inline steps (driver-loop synthetic row at FEATURE boundary is the correct aggregation point)
- Changes to the feature_report view schema
- Changes to driver_sessions table structure

---

## UI Direction

N/A — no UI components involved. All changes are to shell scripts, YAML step contracts, and Python dispatch code.

---

## Technical Context

### Files directly affected

| File | Path | Role |
|---|---|---|
| execute-next-task.yaml | `~/.config/orchestrator/config/steps/execute-next-task.yaml` | Contains embedded simplify pass (lines 146-160) |
| run-learn-cycle.yaml | `~/.config/orchestrator/config/steps/run-learn-cycle.yaml` | Controls learn agent spawning |
| compute-swe-metrics.sh | `/Users/spidey/code/orchestrator/scripts/inline/compute-swe-metrics.sh` | Queries feature_report; produces metrics YAML |
| compute-swe-metrics.yaml | `~/.config/orchestrator/config/steps/compute-swe-metrics.yaml` | Step contract; `inline: true` |
| mark-change-completed.yaml | `~/.config/orchestrator/config/steps/mark-change-completed.yaml` | Step contract; writes completed_at |
| record.py | `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/record.py` | Core `orchestrator done` handler; inline bypass at line 1081 |
| upsert.py | `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/upsert.py` | `upsert_step_event`; writes NULL tokens for usage-absent steps |
| 0002_report_views.sql | `/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/migrations/0002_report_views.sql` | feature_report view definition |
| _complete-phase.yaml | `~/.config/orchestrator/config/workflows/_complete-phase.yaml` | Canonical phase ordering |

### Library versions and integration points
- DuckDB: queried via `duckdb -readonly -json` CLI; version pinned in project.yaml
- Python 3: used in record.py and compute-swe-metrics.sh inline blocks
- yq: used in compute-swe-metrics.sh to read change_id from state.yaml
- `orchestrator done` CLI: the single interface for all state updates; validates shape via record.py

### Key architectural constraints
- Inline steps are tokenless by design (project learning, 2026-04-18): parent-context token counter not accessible from within a Claude Code session
- `feature_report` aggregates from `step_events` only (no driver_sessions dependency)
- FEATURE boundary fires on `remove-worktree` completion; driver-loop synthetic row written only then
- state.yaml must be modified only via `orchestrator done` (never direct Write/Edit)

---

## Open Questions

1. **Defect 1 repro gap:** Does the zero-cost bug actually occur with current code? The stated mechanism (completed_at timing, parse_session_jsonl) does not exist in the codebase. Before any fix is designed, a repro from a recent feature run with zero-cost output is needed. Is there a specific feature in the archive that exhibits this?

2. **Defect 1 alternative mechanism:** If zero costs do occur, the most likely remaining path is: `step_events` rows exist but all have NULL tokens (possible if a feature ran entirely through inline steps with no agent steps). Is there a known scenario where this happens?

3. **Defect 3 flag design:** Should `flags.simplify` and `flags.learn` be per-feature flags (set at workflow-init) or per-run flags (passed as CLI args)? The existing flag merge precedence (`cli_flags > state_flags > schema_defaults`) supports both; the decision affects where the defaults live.

4. **run-learn-cycle model cost:** Which model is workflow-evaluator currently using? The learn skill mentions "opus" — if this is claude-opus-4-5, it is the highest-cost model in the stack. Is there a cheaper model that is sufficient for rule effectiveness evaluation?

5. **compute-swe-metrics hard failure:** The script exits non-zero if `feature_report` returns no rows. For features that consist only of inline steps (no agent steps), this would always fail. Should this be changed to a non-blocking warning, or is a feature with zero agent steps not a supported scenario?
