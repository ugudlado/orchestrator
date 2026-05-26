#!/usr/bin/env bash
# append-retro.sh — append workflow issues to spec/changes/<change_id>/retro.md.
#
# Invoked by record.py when a step's payload includes workflow_issues: [...].
# Each issue becomes an H2 block with category, severity, phase/step,
# detail, workaround, fix_direction, and timestamp. IDs auto-increment
# per-feature: inspect existing ISSUE-N headings to find next N.
#
# Env inputs:  WORKTREE_PATH, CHANGE_ID, ISSUES_JSON (a JSON array)
# Outputs:     {appended: N, retro_path: "..."} on stdout

set -uo pipefail

WORKTREE="${WORKTREE_PATH:-}"
CHANGE_ID="${CHANGE_ID:-}"
ISSUES_JSON="${ISSUES_JSON:-[]}"

if [[ -z "$WORKTREE" ]] || [[ -z "$CHANGE_ID" ]]; then
  printf '%s\n' '{"appended": 0, "error": "missing WORKTREE_PATH or CHANGE_ID"}'
  exit 0
fi

RETRO_DIR="$WORKTREE/spec/changes/$CHANGE_ID"
RETRO_FILE="$RETRO_DIR/retro.md"
mkdir -p "$RETRO_DIR"

if [[ ! -f "$RETRO_FILE" ]]; then
  cat > "$RETRO_FILE" <<EOF
# Retro: workflow issues surfaced during $CHANGE_ID

<!-- Appended by record.py when step payloads include workflow_issues.
     Workflow-improver reads this during run-learn-cycle.
     Format: one H2 per issue, auto-numbered ISSUE-N. -->

EOF
fi

# Find next issue number by scanning existing headings.
NEXT_N=$(grep -oE '^## ISSUE-[0-9]+' "$RETRO_FILE" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)
NEXT_N=${NEXT_N:-0}
NEXT_N=$((NEXT_N + 1))

# Use python3 to parse JSON and emit markdown blocks — robust to multi-line detail.
APPENDED=$(python3 - "$RETRO_FILE" "$NEXT_N" "$ISSUES_JSON" <<'PY'
import json
import sys
from datetime import datetime, timezone

path = sys.argv[1]
start_n = int(sys.argv[2])
raw = sys.argv[3]
try:
    issues = json.loads(raw)
except json.JSONDecodeError:
    print(0)
    sys.exit(0)
if not isinstance(issues, list):
    print(0)
    sys.exit(0)

ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
appended = 0
with open(path, 'r') as rf:
    existing = rf.read()
with open(path, 'a') as f:
    for i, issue in enumerate(issues):
        if not isinstance(issue, dict):
            continue
        dedup_key = (issue.get('dedup_key') or '').strip()
        if dedup_key:
            marker = f"- **dedup_key**: {dedup_key}"
            if marker in existing:
                continue
        n = issue.get('id') or f"ISSUE-{start_n + appended}"
        title = issue.get('title', '(no title)').strip()
        f.write(f"## {n} — {title}\n")
        f.write(f"- **category**: {issue.get('category', 'other')}\n")
        f.write(f"- **severity**: {issue.get('severity', 'workaround-applied')}\n")
        surfaced = issue.get('surfaced_at') or issue.get('phase_step') or 'unknown'
        f.write(f"- **surfaced_at**: {surfaced}\n")
        f.write(f"- **recorded_at**: {ts}\n")
        if dedup_key:
            f.write(f"- **dedup_key**: {dedup_key}\n")
        detail = (issue.get('detail') or '').strip()
        if detail:
            f.write(f"- **detail**: {detail}\n")
        workaround = (issue.get('workaround') or '').strip()
        if workaround:
            f.write(f"- **workaround**: {workaround}\n")
        fix = (issue.get('fix_direction') or '').strip()
        if fix:
            f.write(f"- **fix_direction**: {fix}\n")
        ticket = issue.get('ticket_linear')
        if ticket:
            f.write(f"- **ticket_linear**: {ticket}\n")
        f.write("\n")
        if dedup_key:
            existing += f"- **dedup_key**: {dedup_key}\n"
        appended += 1

print(appended)
PY
)

printf '{"appended": %s, "retro_path": "%s"}\n' "$APPENDED" "$RETRO_FILE"
