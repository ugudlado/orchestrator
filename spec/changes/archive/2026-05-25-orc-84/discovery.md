---
feature-id: orc-84
linear-ticket: ORC-84
---

# Discovery Brief: Shell-loop logging — local time, per-step usage/cost, end-of-run summary

## Feature Summary

The shell-driven dispatch loop (`scripts/run-workflow.sh`) currently prints minimal stderr progress (timestamped `→`/`✓` lines in UTC, baseline commit d600d16). Operators driving features end-to-end can't see, at a glance, what each agent step cost or how much the whole feature consumed without separately running `cost-report.sh`. ORC-84 extends in-loop logging to: (1) use local time for timestamps, (2) emit per-step model/tokens/cost after each agent step completes using data already recorded in `step_history.usage`, and (3) print a compact feature-level rollup on workflow completion by reusing the existing `cost-report.sh --tail` summary. All changes are confined to stderr in the dispatch script; the orchestrator next/done stdout JSON contract and state.yaml shape stay untouched.

## Personas & Actors

- **Operator / developer** running `orchestrator run` or `scripts/run-workflow.sh` directly and watching stderr to monitor progress, cost, and step health.
- **`scripts/run-workflow.sh`** — the dispatch loop; the only component that changes.
- **`bin/orchestrator` (next/done)** — unchanged producer of the JSON action contract and the authority that writes `step_history.usage` via `config/scripts/orchestrator_next/record.py`.
- **`scripts/cost-report.sh`** — existing helper; new consumer call is `--tail` for the one-line rollup.
- **`metrics.duckdb`** — read indirectly through `cost-report.sh`; not queried directly from the loop.

## Use Cases

### Happy Path

UC-1: Local-time progress — operator runs a workflow in a non-UTC timezone (e.g. IST) and every `→`/`✓` progress line in stderr shows local wall-clock time, so timestamps line up with their terminal clock.
UC-2: Per-agent-step usage line — after each agent step's `orchestrator done` succeeds, stderr shows a one-line summary with model, input/output tokens, estimated `cost_usd`, and duration when those fields exist in the just-recorded `step_history` entry.
UC-3: Feature rollup on complete — when the loop exits via `complete_workflow` (exit 1) or via the post-archive "state.yaml not found" path, stderr prints a single rollup line for the feature (change_id, total cost, aggregate tokens, completed step count, duration) sourced from `cost-report.sh --tail`.
UC-4: Inline/script step handled cleanly — `run_step` (zero-token script steps) and inline orchestrator-side steps still log a `✓ done` line and an informational "no tokens (inline/script)" note when no usage is present, without aborting the loop.

### Error & Edge Cases

UC-E1: Missing usage fields — agent step completes but `step_history[-1].usage` is absent or partially populated (e.g. model only). The usage line is suppressed or shows only the available parts; the loop never errors.
UC-E2: `cost-report.sh` unavailable or DB empty — feature-rollup block is skipped silently; workflow still exits with the correct code and message.
UC-E3: yaml/python module unavailable for the usage helper — helper exits 0 with no output; the loop continues to the next step.
UC-E4: Workflow ends via archive (state.yaml gone after `complete-workflow`) — rollup must still fire from the "state archived" branch, not just the explicit exit-1 branch.

## Scope

### In Scope

- Replace UTC `date -u` timestamp formatter with a local-time formatter inside `scripts/run-workflow.sh`.
- Add a `_log_step_usage` helper that reads the latest `step_history` row for `(step_id, phase)` and prints a single stderr line with model/tokens/cost/duration when present.
- Call the usage helper after every successful `orchestrator done`, including the healed-payload branch and `run_step` script steps.
- Add an `_emit_feature_rollup` helper that shells out to `cost-report.sh --tail --change-id <cid>` and prints the result on stderr at workflow completion.
- Cover the new behavior with bats tests under `config/tests/test_run_workflow.bats`.

### Out of Scope

- Any change to `bin/orchestrator next` / `done` stdout JSON or to the `done-payload.md` contract — the ticket explicitly forbids it.
- Adding new fields to `state.yaml` (e.g. running totals) — the loop reads what `record.py` already persists.
- Modifying `record.py` or the metrics schema — usage capture upstream is assumed correct as of the ORC-84 baseline.
- Color/TTY formatting, structured JSON logs, or a separate log file — stderr text only.
- Reworking `cost-report.sh` itself; only its `--tail` mode is consumed.

## UI Direction

N/A — no UI components. All changes are stderr text from a shell dispatcher.

## Key Decisions

- **Selected approach**: State-only reader (S complexity). The dispatch loop reads `step_history[-1].usage` for per-step output and shells to `cost-report.sh --tail` for the feature rollup. Rejected DuckDB-direct (M) and in-memory accumulator (M) on simplicity grounds.
- **OQ-1 resolution**: When `step_history.usage` is empty for an agent step, stay silent (no DuckDB fallback). The rollup at workflow exit covers the gap.
- **OQ-2 resolution**: Always include `duration_ms` when present and `>0`. Format as `ms` (<1s), `s` (<1m), or `m` (>=1m). No sub-second suppression — symmetric treatment of fast and slow steps.

## Open Questions

- OQ-1: Should the per-step usage line fall back to querying `metrics.duckdb` directly when `step_history.usage` is empty for an agent step, or is it acceptable to stay silent and rely on the rollup? (Ticket AC-3 says "when metrics DB or done payload provides it" — design phase should pick one source to keep the loop simple.)
- OQ-2: How should we display duration for very fast steps — always include it, or omit when sub-second to reduce noise? AC-4 lists duration as "if cheap to compute"; design phase to decide the threshold.
