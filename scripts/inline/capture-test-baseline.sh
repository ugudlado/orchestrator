#!/usr/bin/env bash
# capture-test-baseline.sh — run project's test command, parse counts,
# emit baseline JSON dict on stdout's last line.
#
# Env inputs:  REPO_ROOT (required)
# Outputs:     {baseline: {captured_at, test_command, passing, failing,
#                          skipped, total, exit_code}} or
#              {baseline: {skipped: true, reason: "..."}}

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PROJECT_YAML="$REPO_ROOT/spec/project.yaml"

if [ ! -f "$PROJECT_YAML" ]; then
  printf '%s\n' '{"baseline": {"skipped": true, "reason": "spec/project.yaml not found"}}'
  exit 0
fi

TEST_CMD=$(python3 -c "
import yaml
try:
    d = yaml.safe_load(open('$PROJECT_YAML'))
    vc = d.get('verify_commands') or {}
    if isinstance(vc, dict):
        print(vc.get('test', ''))
    elif isinstance(vc, list) and vc and isinstance(vc[0], str):
        print(vc[0])
    else:
        print('')
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$TEST_CMD" ]; then
  printf '%s\n' '{"baseline": {"skipped": true, "reason": "no test command in project.yaml"}}'
  exit 0
fi

CAPTURED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TMPOUT=$(mktemp)
cd "$REPO_ROOT"
bash -c "$TEST_CMD" > "$TMPOUT" 2>&1
EXIT_CODE=$?

python3 <<PY
import json, re
out = open("$TMPOUT").read()
exit_code = $EXIT_CODE
patterns = [
    (r'(\d+)\s+passed', r'(\d+)\s+failed', r'(\d+)\s+skipped'),
    (r'Tests:.*?(\d+)\s+passed', r'Tests:.*?(\d+)\s+failed', r'Tests:.*?(\d+)\s+skipped'),
    (r'test result:.*?(\d+)\s+passed', r'test result:.*?(\d+)\s+failed', None),
]
result = None
for (pp, fp, sp) in patterns:
    mp = re.search(pp, out)
    if mp:
        passing = int(mp.group(1))
        mf = re.search(fp, out) if fp else None
        failing = int(mf.group(1)) if mf else 0
        ms = re.search(sp, out) if sp else None
        skipped = int(ms.group(1)) if ms else 0
        result = {"baseline": {
            "captured_at": "$CAPTURED_AT",
            "test_command": """$TEST_CMD""",
            "passing": passing, "failing": failing, "skipped": skipped,
            "total": passing + failing + skipped, "exit_code": exit_code,
        }}
        break
if result is None:
    tail = "\n".join(out.splitlines()[-20:])
    result = {"baseline": {"skipped": True, "reason": "unparseable",
                           "raw_tail": tail, "exit_code": exit_code}}
print(json.dumps(result))
PY

rm -f "$TMPOUT"
