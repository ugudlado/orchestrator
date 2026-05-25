---
feature-id: orc-84
linear-ticket: ORC-84
---

# Design: Shell-loop logging — local time, per-step usage/cost, end-of-run summary

## Context

`scripts/run-workflow.sh` is the shell-driven dispatch loop that pumps `orchestrator next` → tool/agent → `orchestrator done` without an LLM in the driver. Before ORC-84 it emitted minimal stderr progress (UTC-only `→`/`✓` lines, baseline d600d16). Operators driving features end-to-end could see what step was running but not what each step cost or what the feature totaled — they had to invoke `scripts/cost-report.sh` out-of-band against `metrics.duckdb`.

The ticket asks the loop to surface the data `record.py` already persists on every `orchestrator done`: each `step_history[i].usage` entry holds `model`, `input_tokens`, `output_tokens`, `cost_usd`, and `duration_ms`. `scripts/cost-report.sh --tail` already produces a one-line feature rollup. All of the building blocks exist; ORC-84 wires them into the loop on stderr only.

## Goals / Non-Goals

### Goals

- Replace UTC-only timestamps in dispatch progress lines with local-time stamps.
- After each completed agent or script step, print a one-line `usage` summary (model, tokens, cost, duration) sourced from the just-written `step_history[-1]` entry.
- On workflow completion (both `complete_workflow` exit-1 and the post-archive "state.yaml not found" branch), print a single feature rollup line via `cost-report.sh --tail`.
- Cover the new behavior with bats tests so regressions are caught in CI.

### Non-Goals

- Modifying `bin/orchestrator next`/`done` stdout JSON or the `done-payload.md` contract.
- Adding fields to `state.yaml` (no running totals; the loop only reads what `record.py` already persists).
- Changing `record.py` or the metrics schema.
- Color/TTY formatting, structured JSON logs, or writing to a file (stderr text only).
- Reworking `cost-report.sh`; we only consume its `--tail` mode.

## Approaches Considered

### Approach 1: State-only reader (selected)

Per-step usage line reads `step_history[-1].usage` from `state.yaml` after each successful `orchestrator done`. Rollup shells out to `cost-report.sh --tail` at workflow exit.

- Pros: single source of truth (state.yaml for per-step, DuckDB-via-cost-report for rollup), no new dependencies, smallest patch, fits the already-merged shape at fbc88d5.
- Cons: silent when `usage` is missing on a step (acceptable — rollup catches the totals).

### Approach 2: Direct DuckDB read for every line

Query `metrics.duckdb` directly from the loop for both per-step and rollup output.

- Pros: uniform code path.
- Cons: extra dependency surface inside the loop, two places (record.py and the loop) reading the DB, more failure modes when DB is empty/locked.

### Approach 3: In-memory accumulator

Keep running totals in shell variables, emit rollup from memory without consulting `cost-report.sh`.

- Pros: avoids the `cost-report.sh` dependency.
- Cons: duplicates aggregation logic already in `cost-report.sh`, diverges if record.py changes, easy to drift.

### Selected Approach

**Approach 1**. Lowest complexity (S), reuses the data record.py already persists, and matches the existing `cost-report.sh --tail` contract. Approaches 2 and 3 (both M complexity) duplicate or bypass that pipeline without buying anything; the auto-selection heuristic picks the XS/S option.

## High-Level Design

### Architecture Overview

```
orchestrator next ──► run-workflow.sh ──► tool/agent or script
        ▲                   │                       │
        │                   ▼                       ▼
        └── orchestrator done ◄──── COMPLETION / exit code
                    │
                    ▼
           record.py writes
           step_history[i].usage
                    │
            ┌───────┴───────┐
            ▼               ▼
   _log_step_usage    cost-report.sh --tail
   (reads state.yaml) (reads metrics.duckdb)
            │               │
            ▼               ▼
         stderr          stderr (on exit)
```

### Key Abstractions

- **`_log_ts`** — `bash` helper. Wraps `date +%H:%M:%S` (no `-u`); local time per system TZ.
- **`_log_step_usage <step_id> <phase>`** — Python heredoc. Reads `state.yaml`, finds the latest terminal `step_history` row for `(step_id, phase)`, prints `  model=… · tokens in=… out=… · cost=$… · duration=…` on stderr. Silent if no `usage` block; emits `  usage: no tokens (inline/script)` only for completed/recovered rows with zero tokens.
- **`_emit_feature_rollup <change_id>`** — locates `cost-report.sh` in conventional dirs, runs `cost-report.sh --change-id <cid> --tail`, prefixes the result with a timestamp and `feature complete:` label. Silent if the script or DB row is unavailable.

## Low-Level Design

### Components

| Component | Responsibility |
|---|---|
| `_log_ts` (run-workflow.sh) | Single source of timestamps for all progress lines. |
| `_log_step_usage` (run-workflow.sh) | Reads `step_history[-1]` matching `(step_id, phase)`, formats one line. |
| `_emit_feature_rollup` (run-workflow.sh) | Shells to `cost-report.sh --tail` at workflow exit. |
| `cost-report.sh --tail` (existing) | Aggregates `metrics.duckdb` rows into one summary line. |
| Bats tests (`config/tests/test_run_workflow.bats`) | Locks behavior for the three helpers using stub binaries. |

### Data Flow

1. Loop calls `orchestrator next`; on action, runs script or tool.
2. On success, builds a done payload (with `usage` from COMPLETION when present, `{input_tokens:0,output_tokens:0,model:'none'}` for inline/script).
3. `orchestrator done` invokes `record.py`, which appends to `state.yaml.step_history` with the canonical `usage` block and `cost_usd`/`duration_ms` derived from token math.
4. Loop calls `_log_step_usage` against the just-updated `state.yaml`.
5. On `complete_workflow` (exit 1) or post-archive "state.yaml not found" branch, loop calls `_emit_feature_rollup` against the captured `WORKFLOW_CHANGE_ID`.

### State Management

No new persisted state. `WORKFLOW_CHANGE_ID` is captured once at loop start (before state can be archived) so the rollup still works after archive removes the state file. All other inputs are read fresh from `state.yaml` per call.

### Error Handling

- `yaml`/`python3` import failure inside `_log_step_usage` → `sys.exit(0)` with no output; trailing `|| true` in the bash caller keeps `set -e` happy.
- `state.yaml` missing or unreadable → silent exit 0.
- Empty/missing `usage` block → silent (no "no tokens" noise for agent steps; only emit the inline/script note when status=completed and both token counts are zero).
- `cost-report.sh` not found or empty output → no rollup line; workflow exit code unchanged.
- All three helpers MUST NOT abort the loop. They are stderr-only, fire-and-forget.

## Constraints

- Stderr text only — no color, no TTY-detection, no file output.
- No change to `bin/orchestrator next`/`done` stdout JSON. No change to `state.yaml` schema.
- Local time uses `date +%H:%M:%S` (respects `TZ` env). No timezone label printed — operators are watching their own clock.

## Trade-offs

- **Per-step silence when usage is missing.** Acceptable because the rollup at exit shows the aggregate; making every step verbose ("no usage data") clutters the common case where script/inline steps legitimately have no tokens.
- **Duration always shown when `>0`.** A sub-second filter (e.g. hide `<1s`) was rejected — operators benefit from seeing fast steps too, and the formatter already keeps them compact (`42ms` vs `1.3s` vs `2.1m`).
- **`cost-report.sh` as a black box.** If `--tail`'s format changes, the rollup line changes too. Acceptable because both are operator-facing summaries and ship from the same repo.

## Acceptance Criteria

- AC-1: Progress timestamps emitted by `_log_ts` use local time (no `-u`), and remain on stderr only. Setting `TZ=UTC` and `TZ=Asia/Kolkata` yields different `HH:MM:SS` values for the same wall-clock instant. [traces: UC-1]
- AC-2: After each agent step's `orchestrator done` succeeds, `_log_step_usage` prints a stderr line containing `model=<m>` and `tokens in=<i> out=<o>` whenever the matching `step_history` row's `usage` block has those fields. [traces: UC-2]
- AC-3: When the matching `step_history` row's `usage.cost_usd` is a number, the same line includes `cost=$<x.xxxx>`. [traces: UC-2]
- AC-4: When the matching `step_history` row's `usage.duration_ms > 0`, the same line includes `duration=…` formatted as `ms`/`s`/`m` by magnitude. [traces: UC-2]
- AC-5: On the `complete_workflow` exit-1 branch AND on the post-archive "state.yaml not found" branch, the loop prints a `feature complete: <cost-report --tail output>` line on stderr before exiting 1. [traces: UC-3, UC-E4]
- AC-6: For `run_step` (script) steps that record `usage={input_tokens:0,output_tokens:0,model:'none'}`, `_log_step_usage` emits exactly `  usage: no tokens (inline/script)` and does not abort the loop. [traces: UC-4]
- AC-7: When `step_history[-1].usage` is missing or has neither tokens nor cost nor duration nor a usable model, `_log_step_usage` emits no usage line and the loop continues. [traces: UC-E1]
- AC-8: When `cost-report.sh` is missing or its `--tail` output is empty, `_emit_feature_rollup` is silent and the workflow still exits with the correct code. [traces: UC-E2]
- AC-9: `bin/orchestrator next`/`done` stdout JSON is unchanged from baseline; no new state.yaml top-level keys are introduced. Verified by `git diff` review against `config/scripts/orchestrator_next/record.py` and `bin/orchestrator`. [traces: ticket AC-6]

## Decisions

- Read `step_history[-1]` directly from `state.yaml` rather than from the orchestrator done payload → Rationale: `record.py` is the canonical writer of `usage` (token math, cost lookup); reading the just-written row avoids re-implementing that logic in the driver → Consequence: the line trails by one syscall (`state.yaml` re-read) per step; negligible.
- Capture `WORKFLOW_CHANGE_ID` once at loop start → Rationale: the archive path deletes `state.yaml` before the rollup branch fires → Consequence: relying on a shell variable instead of a fresh state read at exit.
- Silent on missing `usage` for agent steps; explicit `no tokens (inline/script)` only for zero-token completed rows → Rationale: keeps signal/noise high for the common case → Consequence: an agent step with missing `usage` is indistinguishable from a step that emitted no line at all; the rollup at exit is the safety net.

### T-3 contract verification (2026-05-26)

Verified on branch `feature/orc-84` at `28cbd9e` (merge-base with `main`: `16a4753`, `main` tip: `f6f7a38`). **`bin/orchestrator`**: no diff vs `main` in either two-dot or three-dot comparison. **`config/scripts/orchestrator_next/record.py`**: `git diff main...HEAD` and `git diff 16a4753..HEAD` are empty — this branch introduced zero functional edits to `record.py`. The task verify one-liner (`git diff main -- … | wc -l`) reports 25 changed lines because `main` advanced after the branch point (reverted `_STATUS_TO_STATE_STATUS` / FR-2 halt mapping exists on `main` but not on the older branch tip); that is upstream drift, not ORC-84 scope. No architect escalation: loop helpers `_log_step_usage` / `_emit_feature_rollup` delegate to read-only `state_inspect.py log-step-usage` and `cost-report.sh --tail`; neither writes `state.yaml` or adds top-level keys. `bats config/tests/test_run_workflow.bats`: 10/10 pass.

## Open Questions

(None — OQ-1 and OQ-2 resolved in discovery Key Decisions.)
