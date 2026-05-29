#!/bin/bash
# backfill-zero-cost-metrics.sh — Re-run compute-swe-metrics.sh against archived
# features whose metrics.cost.net_usd == 0 and whose JSONL session files exist.
#
# Usage: backfill-zero-cost-metrics.sh [--dry-run] [archive_root]
#
# Arguments:
#   --dry-run      Print what would be done; do not modify any files.
#   archive_root   Path to the archive directory (default: spec/changes/archive).
#
# For each archive in archive_root/*/state.yaml:
#   - Skip if metrics.cost.net_usd != 0 (already has real data)
#   - Skip if no matching JSONL directory found; log 'skip: no-jsonl'
#   - Re-run compute-swe-metrics.sh against the archive directory
#   - Replace the metrics: block in state.yaml atomically via temp file
#
# Exits 0 always (per-archive failures are logged, not fatal).
# Prints a summary: "Summary: updated=N skipped=N failed=N"

set -uo pipefail

DRY_RUN=false
ARCHIVE_ROOT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      ARCHIVE_ROOT="$1"
      shift
      ;;
  esac
done

# Default archive root: spec/changes/archive relative to repo root
if [[ -z "$ARCHIVE_ROOT" ]]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "ERROR: Not in a git repo and no archive_root specified" >&2
    exit 1
  }
  ARCHIVE_ROOT="$REPO_ROOT/spec/changes/archive"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
METRICS_SCRIPT="$SCRIPT_DIR/compute-swe-metrics.sh"

if [[ ! -f "$METRICS_SCRIPT" ]]; then
  echo "ERROR: compute-swe-metrics.sh not found at $METRICS_SCRIPT" >&2
  exit 1
fi

if [[ ! -d "$ARCHIVE_ROOT" ]]; then
  echo "ERROR: archive_root does not exist: $ARCHIVE_ROOT" >&2
  exit 1
fi

updated=0
skipped=0
failed=0

# Walk archive directories
for state_file in "$ARCHIVE_ROOT"/*/state.yaml; do
  [[ -f "$state_file" ]] || continue
  archive_dir="$(dirname "$state_file")"
  archive_name="$(basename "$archive_dir")"

  # Check if net_usd is 0 (backfill candidate)
  net_usd=$(awk '
    /^  cost:/{in_cost=1; next}
    in_cost && /^    net_usd:/{gsub(/.*net_usd: */,""); gsub(/ .*/,""); print; exit}
    in_cost && /^  [a-z]/ && !/^  cost:/{in_cost=0}
  ' "$state_file" 2>/dev/null)
  net_usd="${net_usd:-0}"

  # Skip if net_usd != 0 (already has real data)
  if [[ -n "$net_usd" ]] && (( $(echo "$net_usd != 0" | bc -l 2>/dev/null || echo 0) )); then
    echo "skip: non-zero cost ($net_usd) — $archive_name"
    ((skipped++))
    continue
  fi

  # Check for matching JSONL directory
  # The slug is stored in state.yaml; compute the Claude Code project slug
  repo_root=$(git -C "$archive_dir" rev-parse --show-toplevel 2>/dev/null || \
              git rev-parse --show-toplevel 2>/dev/null || \
              echo "")

  if [[ -z "$repo_root" ]]; then
    echo "skip: no-jsonl (cannot determine repo root) — $archive_name"
    ((skipped++))
    continue
  fi

  slug="${repo_root//\//-}"
  project_dir="$HOME/.claude/projects/$slug"

  if [[ ! -d "$project_dir" ]] || [[ -z "$(ls "$project_dir"/*.jsonl 2>/dev/null)" ]]; then
    echo "skip: no-jsonl — $archive_name"
    ((skipped++))
    continue
  fi

  # Re-run or report
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "dry-run: would update $archive_name (JSONL present at $project_dir)"
    ((updated++))
    continue
  fi

  # Run metrics script and capture output
  new_metrics=$(bash "$METRICS_SCRIPT" "$archive_dir" 2>/dev/null) || {
    echo "failed: metrics script error for $archive_name" >&2
    ((failed++))
    continue
  }

  # Check the result has non-zero tokens/cost
  new_net_usd=$(echo "$new_metrics" | awk '/^  cost:/{in_c=1; next} in_c && /^    net_usd:/{gsub(/.*: */,""); print; exit}')
  if [[ -z "$new_net_usd" ]] || (( $(echo "$new_net_usd == 0" | bc -l 2>/dev/null || echo 1) )); then
    echo "skip: metrics still zero after re-run — $archive_name"
    ((skipped++))
    continue
  fi

  # Atomic in-place replacement via temp file
  # Strategy: remove old metrics: block, append new block
  tmp_file="$(mktemp "${TMPDIR:-/tmp}/backfill-XXXXXX.yaml")"
  # Strip existing metrics: block (from 'metrics:' to next top-level key or EOF)
  awk '
    /^metrics:/{in_metrics=1; next}
    in_metrics && /^[a-z]/{in_metrics=0}
    !in_metrics {print}
  ' "$state_file" > "$tmp_file"

  # Append new metrics block
  echo "$new_metrics" >> "$tmp_file"

  # Atomically replace
  mv "$tmp_file" "$state_file"
  echo "updated: $archive_name (net_usd: 0 → $new_net_usd)"
  ((updated++))
done

echo ""
echo "Summary: updated=$updated skipped=$skipped failed=$failed"

# Always exit 0 (per-archive failures do not make the script fatal)
exit 0
