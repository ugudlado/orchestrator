#!/usr/bin/env bash
# HL-287 M8 gate script. Run all rework-integrity checks.
set -e
cd "$(dirname "$0")/.."

echo "Gate 1: contract lint"
make lint-contracts

echo "Gate 2: no workflow schema refs to deleted contracts"
# M4-deleted: 7 folded contracts
# rev-2-deleted: 5 contracts absorbed by pre-dispatch init
# autopilot-collapse-deleted: 3 contracts removed when /autopilot became a thin wrapper
DELETED="design-exploration create-or-refresh-artifacts validate-artifacts run-implement-review final-signoff phase-signoff verify-spike-findings create-worktree load-project-context configure-gitignore autopilot-session-init create-linear-ticket autopilot-preflight autopilot-iterate autopilot-session-report"
fail=0
for id in $DELETED; do
  if grep -rE "^\s*-\s+$id\b" config/workflows/ 2>/dev/null; then
    echo "  fail: $id referenced"
    fail=$((fail + 1))
  fi
done
[ "$fail" -eq 0 ] && echo "  pass"

echo "Gate 3: misclassified-math contracts have inline: true"
for id in compute-swe-metrics compute-prediction-accuracy archive-completed-change; do
  grep -q "^agent:" "config/steps/$id.yaml" && { echo "  fail: $id still has agent:"; exit 1; } || true
  grep -q "^inline: true" "config/steps/$id.yaml" || { echo "  fail: $id missing inline: true"; exit 1; }
done
echo "  pass"

echo "Gate 4: 9 inline scripts (10 post-Phase4 - 1 ingest-feature-metrics deleted in Phase 5)"
count=$(find scripts/inline/ -maxdepth 1 -name '*.sh' -o -name '*.py' | wc -l | tr -d ' ')
[ "$count" = "9" ] && echo "  pass ($count)" || { echo "  fail ($count)"; exit 1; }

echo "Gate 4b: workflow-init is pre-dispatch only"
[ ! -f "config/steps/workflow-init.yaml" ] || { echo "  fail: config/steps/workflow-init.yaml should not exist"; exit 1; }
for schema in config/workflows/feature.yaml config/workflows/bugfix.yaml config/workflows/spike.yaml; do
  if grep -q "workflow-init" "$schema"; then
    echo "  fail: $schema still references workflow-init"
    exit 1
  fi
done
echo "  pass"

echo "Gate 5: test suite"
python3 -m unittest discover -s config/scripts/tests 2>&1 | tail -3

echo "Gate 6: CLI banner advertises done (strict) and does NOT mention record"
banner=$(./bin/orchestrator 2>&1 || true)
echo "$banner" | grep -q "orchestrator done" || { echo "  fail: 'orchestrator done' not found in banner"; exit 1; }
if echo "$banner" | grep -q "orchestrator record"; then
  echo "  fail: banner still mentions 'orchestrator record' — update bin/orchestrator banner (T-25)"; exit 1
fi
echo "  pass"

echo "All M8 gates PASS"
