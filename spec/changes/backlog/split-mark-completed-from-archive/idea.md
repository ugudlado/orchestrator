# Split mark-completed out of archive step, order before compute-swe-metrics

## Problem

`compute-swe-metrics.sh` produces zero input/output tokens and zero cost when run against an active (pre-archive) state.yaml, even though the JSONL session files contain valid usage data.

**Observed in HL-282** (autopilot-2026-04-17-001):
- During complete phase: `input: 0, output: 0, cost.net_usd: 0` (but `total: 124087` from the state-only fallback path)
- Re-run against the archived state.yaml (where `completed_at` was present): `input: 22947, output: 231650, net_usd: 26.13` — all correct
- Same JSONL files. Only difference: `completed_at` present on the second run.

## Root cause

Step ordering bug in `config/workflows/_complete-phase.yaml`:

```
steps:
  - compute-prediction-accuracy
  - run-learn-cycle
  - compute-swe-metrics       # runs FIRST — state.yaml has no completed_at yet
  - archive-completed-change  # writes completed_at in step 2 of its instruction
  - remove-worktree
```

`compute-swe-metrics.sh:parse_session_jsonl` requires both `STARTED_AT` and `COMPLETED_AT` to compute the JSONL time window. When `COMPLETED_AT` is empty, `date -j -f` fails, the function returns 1, and the script silently falls through to the state-only token path (which only has aggregated `total_tokens` from the Agent footer, not input/output/cost breakdowns).

The prior "fix" (commit a6a2e95) addressed TZ/slug bugs; it did not catch this because it was validated by re-running against already-archived state.

## Fix

**Split the mutation out of `archive-completed-change`** and order it before `compute-swe-metrics`:

New step `mark-change-completed` (inline, no agent):
- Writes `status: completed`, `completed_at: <ISO>`, `archive_path: spec/changes/archive/YYYY-MM-DD-<change-id>/` to state.yaml
- Single-purpose; no file copying

Revised `archive-completed-change`:
- Assumes `completed_at` already set
- Becomes a pure artifact-move step (copy change-dir → archive, commit, remove active dir)

New order:
```
steps:
  - compute-prediction-accuracy
  - run-learn-cycle
  - mark-change-completed       # NEW: writes status + completed_at
  - compute-swe-metrics         # now has valid time window
  - archive-completed-change    # pure move + commit
  - remove-worktree
```

## Why this is the right fix (vs a "fallback to now" hack)

- `compute-swe-metrics` is supposed to measure a completed feature. An empty `completed_at` is a precondition failure, not a case to paper over.
- Splitting mutation from artifact-move makes the archive step idempotent and re-runnable.
- Each step has one clear responsibility (SRP).
- Backfill path stays simple: re-run `compute-swe-metrics` against any archived state.yaml to repair bad metrics.

## Scope

**In-scope:**
- New `config/steps/mark-change-completed.yaml`
- Edit `config/steps/archive-completed-change.yaml` to remove the mutation (step 2 of current instruction)
- Edit `config/workflows/_complete-phase.yaml` to insert `mark-change-completed` before `compute-swe-metrics`
- Backfill metrics for any archived features affected by this bug (grep `metrics.cost.net_usd: 0` and re-run compute against them)
- Light test: a fixture state.yaml without `completed_at` ran through the new step chain produces non-zero token metrics

**Out-of-scope:**
- Changes to `parse_session_jsonl` internals
- Changes to JSONL file format or ingest script
- Per-agent / per-step aggregation improvements (separate ticket)

## Acceptance criteria

- AC-1: `mark-change-completed` step exists and writes `status: completed` + `completed_at` + `archive_path`
- AC-2: `archive-completed-change` no longer mutates those fields — only copies files and commits
- AC-3: New order in `_complete-phase.yaml` is: compute-prediction-accuracy → run-learn-cycle → mark-change-completed → compute-swe-metrics → archive-completed-change → remove-worktree
- AC-4: Running the feature workflow end-to-end produces non-zero `metrics.cost.net_usd` and `metrics.tokens.input/output` on the archived state.yaml
- AC-5: Archived features with `cost.net_usd: 0` AND matching JSONL files in `~/.claude/projects/` are backfilled with correct values (list the affected features as part of the PR)
- AC-6: Re-running `compute-swe-metrics` against any archived state.yaml is idempotent (produces identical output on repeated runs)

## Priority

High — silently corrupts the metrics record for every feature. Blocks accurate cost reporting, the /telemetry dashboard, and the /learn cycle's own cost analysis.
