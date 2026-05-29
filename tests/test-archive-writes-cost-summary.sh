#!/usr/bin/env bash
# Test: archive-completed-change.sh writes cost-summary.md into the archive dir.
#
# Bug (observed on orc-78, 2026-05-22): COST_REPORT_SCRIPT was resolved as
# "$(dirname "$0")/../cost-report.sh" → config/scripts/cost-report.sh, which
# does not exist (the script lives at repo-root scripts/cost-report.sh). The
# `[ -f ... ]` guard silently failed, so cost-summary.md was never written to
# any archive. Fix: resolve against $REPO_ROOT/scripts/cost-report.sh.
#
# This test runs the real script against a fake repo with a stub cost-report.sh
# at the correct location and asserts cost-summary.md lands in the archive.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/config/steps/archive-completed-change/script.sh"

TMPDIR_BASE="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

WT_ROOT="$TMPDIR_BASE/worktree"
FAKE_REPO="$TMPDIR_BASE/repo"
CHANGE_ID="demo"
ARCHIVE_REL="spec/changes/archive/2099-01-01-demo"
SRC="$WT_ROOT/spec/changes/$CHANGE_ID"
DST="$WT_ROOT/$ARCHIVE_REL"

# Seed the worktree source.
mkdir -p "$SRC"
printf 'status: completed\n' > "$SRC/state.yaml"
echo "design" > "$SRC/design.md"

mkdir -p "$FAKE_REPO/scripts"
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" config user.email test@test
git -C "$FAKE_REPO" config user.name test
git -C "$WT_ROOT" init -q
git -C "$WT_ROOT" config user.email test@test
git -C "$WT_ROOT" config user.name test

# Stub cost-report.sh at the CORRECT location: repo-root scripts/.
# The buggy path (config/scripts/cost-report.sh) would not find this.
cat > "$FAKE_REPO/scripts/cost-report.sh" <<'STUB'
#!/usr/bin/env bash
echo "# Cost Summary (stub)"
STUB
chmod +x "$FAKE_REPO/scripts/cost-report.sh"

# Run the real archive script. ORCHESTRATOR_HOME must be set for the
# cost-summary branch to fire.
OUT=$(REPO_ROOT="$FAKE_REPO" CHANGE_ID="$CHANGE_ID" ARCHIVE_PATH="$ARCHIVE_REL" \
  WORKTREE_ROOT="$WT_ROOT" ORCHESTRATOR_WORKFLOW_DIR="$WT_ROOT" \
  ORCHESTRATOR_HOME="$FAKE_REPO" \
  bash "$SCRIPT" 2>/dev/null)

fail=0
check() {
  local desc="$1" result="$2"
  if [[ "$result" -eq 0 ]]; then echo "PASS: $desc"
  else echo "FAIL: $desc"; ((fail++))
  fi
}

check "archive dir created"        "$([ -d "$DST" ] && echo 0 || echo 1)"
check "state.yaml archived"        "$([ -f "$DST/state.yaml" ] && echo 0 || echo 1)"
check "cost-summary.md written"    "$([ -f "$DST/cost-summary.md" ] && echo 0 || echo 1)"
check "cost-summary.md non-empty"  "$([ -s "$DST/cost-summary.md" ] && echo 0 || echo 1)"
check "script reported archived"   "$(echo "$OUT" | grep -q '"archived_at"' && echo 0 || echo 1)"

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "OK: archive writes cost-summary.md from repo-root scripts/cost-report.sh"
else
  echo "FAIL: $fail assertion(s) failed"
  exit 1
fi
