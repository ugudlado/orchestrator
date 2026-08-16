#!/usr/bin/env bash
# Run the A2A dispatcher spike end-to-end.
#   ./run_spike.sh            # unsigned card
#   ./run_spike.sh --signed   # signed card + signature verification demo
set -euo pipefail

PORT="${PORT:-41999}"
BASE_URL="http://127.0.0.1:${PORT}"
EXTRA="${1:-}"
PY="python3"

echo "==> starting worker agent on ${BASE_URL} ${EXTRA}"
"$PY" worker_agent.py --port "$PORT" $EXTRA > /tmp/a2a_spike_worker.log 2>&1 &
WORKER_PID=$!
trap 'kill "$WORKER_PID" 2>/dev/null || true' EXIT

echo "==> waiting for Agent Card ..."
for i in $(seq 1 40); do
    if curl -fsS "${BASE_URL}/.well-known/agent-card.json" > /dev/null 2>&1; then
        break
    fi
    sleep 0.25
done
curl -fsS "${BASE_URL}/.well-known/agent-card.json" > /dev/null \
    || { echo "worker failed to start; log:"; tail -30 /tmp/a2a_spike_worker.log; exit 1; }
echo "==> card is live; running dispatcher"
if [ "$EXTRA" = "--signed" ]; then
    "$PY" dispatcher.py --base-url "$BASE_URL" --verify
else
    "$PY" dispatcher.py --base-url "$BASE_URL"
fi
echo "==> done (exit $?)"