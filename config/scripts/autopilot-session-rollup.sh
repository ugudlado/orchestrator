#!/usr/bin/env bash
# autopilot-session-rollup.sh — Aggregate iteration metrics into autopilot session state.yaml.
#
# Usage: autopilot-session-rollup.sh <session_id>
#
# Reads:  spec/changes/archive/autopilot-<session_id>/state.yaml (iterations[].metrics)
# Writes: same file — adds top-level metrics: block, sets status: completed, completed_at.
#
# The finalized state.yaml shape matches the archived feature state.yaml contract
# (change_id, schema, status, metrics) so existing telemetry/learn/workflow-improver
# glob consumers pick it up with no code change.

set -uo pipefail

SESSION_ID="${1:?Usage: autopilot-session-rollup.sh <session_id>}"
REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo ".")}"

SESSION_STATE="$REPO_ROOT/spec/changes/archive/autopilot-$SESSION_ID/state.yaml"

if [[ ! -f "$SESSION_STATE" ]]; then
  echo "ERROR: session state.yaml not found at $SESSION_STATE" >&2
  exit 1
fi

# ── Aggregate iteration metrics ───────────────────────────────────────────
# Uses yq to extract values then awk to sum (yq v4 mikefarah doesn't support
# reduce in this version; pipe to awk is the portable approach).
sum_iteration_field() {
  local yq_path="$1"
  yq ".iterations[].metrics.$yq_path // 0" "$SESSION_STATE" 2>/dev/null \
    | awk '{s+=$1} END {print s+0}'
}

TOKENS_TOTAL=$(sum_iteration_field "tokens.total")
DURATION_TOTAL=$(sum_iteration_field "duration_ms")
CHURN_TOTAL=$(sum_iteration_field "churn.files_changed")

# Count iterations by status
count_iterations() {
  local status="$1"
  yq ".iterations[] | select(.status == \"$status\") | .status" "$SESSION_STATE" 2>/dev/null \
    | wc -l | tr -d ' '
}

ITERS_COMPLETED=$(count_iterations "completed")
ITERS_FAILED=$(count_iterations "failed")
ITERS_EMPTY=$(count_iterations "empty_backlog")

COMPLETED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ── Write finalized metrics block using yq ────────────────────────────────
# Single yq invocation chains all assignments with `|` — schema and change_id
# remain untouched (already set when the file was created).
# Resolution block: spike/autopilot null fields mirror compute-swe-metrics.sh;
# iteration counts carry real values. review_scores is intentionally omitted.
yq -i "
  .status = \"completed\" |
  .completed_at = \"$COMPLETED_AT\" |
  .metrics.category = \"autopilot\" |
  .metrics.tokens.total = $TOKENS_TOTAL |
  .metrics.duration_ms = $DURATION_TOTAL |
  .metrics.churn.files_changed = $CHURN_TOTAL |
  .metrics.resolution.resolve_rate = null |
  .metrics.resolution.pass_at_1 = null |
  .metrics.resolution.pass_at_2 = null |
  .metrics.resolution.regression_rate = null |
  .metrics.resolution.tasks_total = null |
  .metrics.resolution.iterations_completed = $ITERS_COMPLETED |
  .metrics.resolution.iterations_failed = $ITERS_FAILED |
  .metrics.resolution.iterations_empty = $ITERS_EMPTY
" "$SESSION_STATE"

echo "Session $SESSION_ID rollup complete: tokens=$TOKENS_TOTAL completed=$ITERS_COMPLETED failed=$ITERS_FAILED empty=$ITERS_EMPTY" >&2
