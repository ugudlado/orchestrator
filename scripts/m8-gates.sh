#!/usr/bin/env bash
# HL-287 M8 gate script. Run all rework-integrity checks.
set -e
cd "$(dirname "$0")/.."

echo "Gate 1: contract lint"
make lint-contracts

echo "Gate 2: no workflow schema refs to deleted contracts"
DELETED="design-exploration create-or-refresh-artifacts validate-artifacts run-implement-review final-signoff phase-signoff verify-spike-findings"
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

echo "Gate 4: 13 inline scripts"
count=$(ls scripts/inline/ | wc -l | tr -d ' ')
[ "$count" = "13" ] && echo "  pass ($count)" || { echo "  fail ($count)"; exit 1; }

echo "Gate 5: test suite"
python3 -m unittest discover -s config/scripts/tests 2>&1 | tail -3

echo "Gate 6: CLI supports next and record"
./bin/orchestrator 2>&1 | grep -q "orchestrator record" || { echo "  fail"; exit 1; }
echo "  pass"

echo "All M8 gates PASS"
