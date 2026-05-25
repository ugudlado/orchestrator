#!/usr/bin/env bash
# Launch the live orchestrator dashboard on http://localhost:8765
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8765}"
exec "$HERE/.venv/bin/uvicorn" \
  --app-dir "$HERE" \
  --host 127.0.0.1 \
  --port "$PORT" \
  server:app
