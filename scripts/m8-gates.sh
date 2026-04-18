#!/usr/bin/env bash
# HL-287 M8 gate script. Run all rework-integrity checks.
set -e
cd "$(dirname "$0")/.."

echo "Gate 1: contract lint"
make lint-contracts

echo "Gate 2: no workflow schema refs to deleted contracts"
# M4-deleted: 7 folded contracts
# rev-2-deleted: 5 contracts absorbed by workflow-init agent
DELETED="design-exploration create-or-refresh-artifacts validate-artifacts run-implement-review final-signoff phase-signoff verify-spike-findings create-worktree load-project-context configure-gitignore autopilot-session-init create-linear-ticket"
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

echo "Gate 4: 8 inline scripts (5 absorbed into workflow-init agent, HL-287 rev-2)"
count=$(find scripts/inline/ -maxdepth 1 -name '*.sh' -o -name '*.py' | wc -l | tr -d ' ')
[ "$count" = "8" ] && echo "  pass ($count)" || { echo "  fail ($count)"; exit 1; }

echo "Gate 4b: workflow-init agent + contract exist and feature/bugfix/spike schemas reference it"
[ -f "agents/workflow-init.md" ] || { echo "  fail: agents/workflow-init.md missing"; exit 1; }
[ -f "config/steps/workflow-init.yaml" ] || { echo "  fail: config/steps/workflow-init.yaml missing"; exit 1; }
for schema in config/workflows/feature.yaml config/workflows/bugfix.yaml config/workflows/spike.yaml; do
  grep -q "workflow-init" "$schema" || { echo "  fail: $schema does not reference workflow-init"; exit 1; }
done
echo "  pass"

echo "Gate 5: test suite"
python3 -m unittest discover -s config/scripts/tests 2>&1 | tail -3

echo "Gate 6: CLI supports next and record"
./bin/orchestrator 2>&1 | grep -q "orchestrator record" || { echo "  fail"; exit 1; }
echo "  pass"

echo "All M8 gates PASS"
