#!/usr/bin/env bash
# read-sub-state-metrics.sh — Extract iteration metrics from a sub-feature state.yaml.
#
# Usage: read-sub-state-metrics.sh <slug>
#
# Reads the sub-feature's state.yaml from:
#   1. $HOME/.workflows/<slug>/state.yaml (active path)
#   2. $REPO_ROOT/spec/changes/archive/<slug>/state.yaml (archive fallback)
#
# Outputs a YAML metrics block suitable for appending to the autopilot session
# state.yaml under an iterations[] entry:
#
#   metrics:
#     tokens:
#       total: <sum of step_history[].usage.total_tokens>
#     cost:
#       total: <computed from tokens, or 0 if no pricing data>
#     duration_ms: <sum of step_history[].usage.duration_ms>
#     churn:
#       files_changed: <from metrics.churn.files_changed, or 0>
#
# Exits non-zero if the sub-feature state.yaml cannot be found.

set -uo pipefail

SLUG="${1:?Usage: read-sub-state-metrics.sh <slug>}"

# ── Locate state.yaml ─────────────────────────────────────────────────────
STATE_FILE=""

# 1. Active path
ACTIVE="$HOME/.workflows/$SLUG/state.yaml"
if [[ -f "$ACTIVE" ]]; then
  STATE_FILE="$ACTIVE"
fi

# 2. Archive fallback (if REPO_ROOT is set; otherwise skip)
if [[ -z "$STATE_FILE" && -n "${REPO_ROOT:-}" ]]; then
  ARCHIVE="$REPO_ROOT/spec/changes/archive/$SLUG/state.yaml"
  if [[ -f "$ARCHIVE" ]]; then
    STATE_FILE="$ARCHIVE"
  fi
fi

if [[ -z "$STATE_FILE" ]]; then
  echo "ERROR: state.yaml not found for slug '$SLUG' (tried active and archive paths)" >&2
  exit 1
fi

# ── Sum step_history[].usage fields ──────────────────────────────────────
read TOTAL_TOKENS TOTAL_DURATION <<< $(awk '
  /^step_history:/ { in_sh=1; next }
  in_sh && /^[^ ]/ && !/^  / { in_sh=0 }
  in_sh && /^    usage:/ { in_u=1; next }
  in_sh && in_u && /^      total_tokens:/ { gsub(/.*total_tokens: */, ""); t+=$0+0 }
  in_sh && in_u && /^      duration_ms:/  { gsub(/.*duration_ms: */, "");  d+=$0+0 }
  in_sh && in_u && /^    [a-z]/ && !/^    usage:/ { in_u=0 }
  END { printf "%d %d", t+0, d+0 }
' "$STATE_FILE")

# ── Read churn from metrics block if present ──────────────────────────────
FILES_CHANGED=$(awk '
  /^metrics:/ { in_m=1; next }
  in_m && /^[^ ]/ && !/^  / { in_m=0 }
  in_m && /^  churn:/ { in_c=1; next }
  in_m && in_c && /^    files_changed:/ { gsub(/.*files_changed: */, ""); print $0+0; exit }
  in_m && in_c && /^  [a-z]/ { in_c=0 }
  END { print "0" }
' "$STATE_FILE" | head -1)
FILES_CHANGED=${FILES_CHANGED:-0}

# ── Emit metrics YAML block ───────────────────────────────────────────────
cat <<YAML
metrics:
  tokens:
    total: $TOTAL_TOKENS
  duration_ms: $TOTAL_DURATION
  churn:
    files_changed: $FILES_CHANGED
YAML
