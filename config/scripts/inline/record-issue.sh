#!/usr/bin/env bash
# record-issue.sh — append one workflow issue to .pending-issues.jsonl (non-fatal).
#
# Inline scripts call this to surface anomalies without failing the step.
# Env: CHANGE_ID, WORKTREE_PATH (required); PHASE, STEP_ID (optional, for surfaced_at).
# Flags: --category, --severity, --detail, --dedup-key, --workaround, --fix-direction

trap ':' ERR

record_issue_main() {
  local category="" severity="" detail="" dedup_key="" workaround="" fix_direction=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --category)
        category="${2:-}"
        shift 2
        ;;
      --severity)
        severity="${2:-}"
        shift 2
        ;;
      --detail)
        detail="${2:-}"
        shift 2
        ;;
      --dedup-key)
        dedup_key="${2:-}"
        shift 2
        ;;
      --workaround)
        workaround="${2:-}"
        shift 2
        ;;
      --fix-direction)
        fix_direction="${2:-}"
        shift 2
        ;;
      --)
        shift
        break
        ;;
      -*)
        echo "record-issue.sh: ignoring unknown option: $1" >&2
        shift
        ;;
      *)
        echo "record-issue.sh: ignoring unexpected argument: $1" >&2
        shift
        ;;
    esac
  done

  if [[ -z "${CHANGE_ID:-}" ]]; then
    echo "record-issue.sh: CHANGE_ID is not set; skipping issue record" >&2
    return 0
  fi

  if [[ -z "${WORKTREE_PATH:-}" ]]; then
    echo "record-issue.sh: WORKTREE_PATH is not set; skipping issue record" >&2
    return 0
  fi

  local phase="${PHASE:-unknown}"
  local step_id="${STEP_ID:-unknown}"
  local surfaced_at="${phase}/${step_id}"
  local pending_file="$WORKTREE_PATH/spec/changes/$CHANGE_ID/.pending-issues.jsonl"

  python3 - "$pending_file" "$category" "$severity" "$detail" "$dedup_key" \
    "$workaround" "$fix_direction" "$surfaced_at" <<'PY' || true
import json
import sys
from pathlib import Path

path, category, severity, detail, dedup_key, workaround, fix_direction, surfaced_at = sys.argv[1:9]

obj = {
    "category": category,
    "severity": severity,
    "surfaced_at": surfaced_at,
}
if detail:
    obj["detail"] = detail
if dedup_key:
    obj["dedup_key"] = dedup_key
if workaround:
    obj["workaround"] = workaround
if fix_direction:
    obj["fix_direction"] = fix_direction

pending = Path(path)
pending.parent.mkdir(parents=True, exist_ok=True)
with pending.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
PY
}

record_issue_main "$@"
exit 0
