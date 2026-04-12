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

# Sum tokens.total across all iterations
TOKENS_TOTAL=$(yq '.iterations[].metrics.tokens.total // 0' "$SESSION_STATE" 2>/dev/null \
  | awk '{s+=$1} END {print s+0}')
TOKENS_TOTAL=$((TOKENS_TOTAL + 0))

# Sum duration_ms across all iterations
DURATION_TOTAL=$(yq '.iterations[].metrics.duration_ms // 0' "$SESSION_STATE" 2>/dev/null \
  | awk '{s+=$1} END {print s+0}')
DURATION_TOTAL=$((DURATION_TOTAL + 0))

# Sum churn.files_changed across all iterations
CHURN_TOTAL=$(yq '.iterations[].metrics.churn.files_changed // 0' "$SESSION_STATE" 2>/dev/null \
  | awk '{s+=$1} END {print s+0}')
CHURN_TOTAL=$((CHURN_TOTAL + 0))

# Count iterations by status
ITERS_COMPLETED=$(yq '.iterations[] | select(.status == "completed") | .status' "$SESSION_STATE" 2>/dev/null \
  | wc -l | tr -d ' ')
ITERS_COMPLETED=$((ITERS_COMPLETED + 0))

ITERS_FAILED=$(yq '.iterations[] | select(.status == "failed") | .status' "$SESSION_STATE" 2>/dev/null \
  | wc -l | tr -d ' ')
ITERS_FAILED=$((ITERS_FAILED + 0))

ITERS_EMPTY=$(yq '.iterations[] | select(.status == "empty_backlog") | .status' "$SESSION_STATE" 2>/dev/null \
  | wc -l | tr -d ' ')
ITERS_EMPTY=$((ITERS_EMPTY + 0))

COMPLETED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ── Write finalized metrics block using yq ────────────────────────────────
# Use yq to write each field atomically into the existing file.
# Schema and change_id remain as-is (already set when file was created).

yq -i '.status = "completed"' "$SESSION_STATE"
yq -i ".completed_at = \"$COMPLETED_AT\"" "$SESSION_STATE"

# Write top-level metrics block
yq -i ".metrics.category = \"autopilot\"" "$SESSION_STATE"
yq -i ".metrics.tokens.total = $TOKENS_TOTAL" "$SESSION_STATE"
yq -i ".metrics.duration_ms = $DURATION_TOTAL" "$SESSION_STATE"
yq -i ".metrics.churn.files_changed = $CHURN_TOTAL" "$SESSION_STATE"

# Resolution block: spike/autopilot fields are explicit null; iteration counts are real values
yq -i '.metrics.resolution.resolve_rate = null' "$SESSION_STATE"
yq -i '.metrics.resolution.pass_at_1 = null' "$SESSION_STATE"
yq -i '.metrics.resolution.pass_at_2 = null' "$SESSION_STATE"
yq -i '.metrics.resolution.regression_rate = null' "$SESSION_STATE"
yq -i '.metrics.resolution.tasks_total = null' "$SESSION_STATE"
yq -i ".metrics.resolution.iterations_completed = $ITERS_COMPLETED" "$SESSION_STATE"
yq -i ".metrics.resolution.iterations_failed = $ITERS_FAILED" "$SESSION_STATE"
yq -i ".metrics.resolution.iterations_empty = $ITERS_EMPTY" "$SESSION_STATE"

# review_scores is intentionally omitted for autopilot

echo "Session $SESSION_ID rollup complete: tokens=$TOKENS_TOTAL completed=$ITERS_COMPLETED failed=$ITERS_FAILED empty=$ITERS_EMPTY" >&2
