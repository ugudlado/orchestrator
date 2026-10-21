#!/usr/bin/env bash
# Run the whole Studio stack on one machine (a laptop): Inngest, the driver
# worker and Studio. Ctrl-C stops all of it.
#
#   spike/inngest_driver/run_local.sh [--stub] [--repo PATH]... [--projects DIR] [--pack PATH]
#
#   --repo PATH     a project folder (git repo) runs may change (repeatable)
#   --projects DIR  every git repo (or folder with a .mcp.json) under DIR is a project too
#                   A project's .mcp.json gives its agents their tools (Codex: .codex/config.toml);
#                   sign in to OAuth servers once with /mcp in Claude Code or `codex mcp login`.
#   --pack PATH  the workflow pack (default: ../workflows/config next to this checkout)
#   --stub       judgment steps write placeholder outputs instead of calling Claude Code,
#                and exec steps are stubbed too; for trying the UI at no cost
#
# Needs: uv, node/npx, and (unless --stub) the `claude` CLI signed in.
# Uses redis-server when it's installed, so runs parked at a gate survive a restart;
# without it Inngest keeps state in memory and a restart loses waiting runs.
# State lives in ~/.orchestrator-studio (runs, Inngest's database, generated keys).
# Studio: http://localhost:8790 · Inngest dashboard: http://localhost:8288
# Written for the bash 3.2 that ships with macOS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOME_DIR="${ORCH_LOCAL_HOME:-$HOME/.orchestrator-studio}"
PACK="$(cd "$ROOT/.." && pwd)/workflows/config"
STUB=0
REPOS=()
PROJECTS_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stub) STUB=1 ;;
    --repo) REPOS+=("$(cd "$2" && pwd)"); shift ;;
    --projects) PROJECTS_ROOT="$(cd "$2" && pwd)"; shift ;;
    --pack) PACK="$(cd "$2" && pwd)"; shift ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)" >&2; exit 1; }; }
need uv "https://docs.astral.sh/uv/"
need npx "install Node.js 20+"
[ "$STUB" = 1 ] || need claude "install Claude Code and sign in, or pass --stub"
[ -d "$PACK/workflows" ] || { echo "no workflow pack at $PACK; clone ugudlado/workflows next to this repo or pass --pack" >&2; exit 1; }
[ ${#REPOS[@]} -gt 0 ] || [ -n "$PROJECTS_ROOT" ] || echo "note: no --repo or --projects given; Studio can show runs but not start one" >&2

mkdir -p "$HOME_DIR/runs" "$HOME_DIR/inngest"
KEYS="$HOME_DIR/keys.env"
if [ ! -f "$KEYS" ]; then
  umask 077
  printf 'INNGEST_SIGNING_KEY=%s\nINNGEST_EVENT_KEY=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 16)" > "$KEYS"
fi
set -a; . "$KEYS"; set +a
export INNGEST_API_BASE_URL=http://127.0.0.1:8288 INNGEST_EVENT_API_BASE_URL=http://127.0.0.1:8288

if [ ! -f "$ROOT/studio/dist/index.html" ]; then
  echo "building Studio…"
  (cd "$ROOT/studio" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
fi

cd "$ROOT"
PIDS=()
cleanup() { trap - INT TERM EXIT; kill ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup INT TERM EXIT
start() { "$@" & PIDS+=($!); }

REDIS_ARGS=()
if command -v redis-server >/dev/null; then
  start redis-server --port 6390 --bind 127.0.0.1 --dir "$HOME_DIR" --appendonly yes --save "" >"$HOME_DIR/redis.log" 2>&1
  REDIS_ARGS=(--redis-uri redis://127.0.0.1:6390)
fi
start npx --yes inngest-cli@1.46.0 start --host 127.0.0.1 --port 8288 \
  --signing-key "$INNGEST_SIGNING_KEY" --event-key "$INNGEST_EVENT_KEY" \
  --sqlite-dir "$HOME_DIR/inngest" ${REDIS_ARGS[@]+"${REDIS_ARGS[@]}"} >"$HOME_DIR/inngest.log" 2>&1

echo "starting Inngest (the first run downloads it)…"
for _ in $(seq 1 120); do curl -s -o /dev/null http://127.0.0.1:8288/ && break; sleep 1; done

PY=(uv run --project "$ROOT" --with 'inngest[connect]==0.5.19' --with pyyaml python)
if [ "$STUB" = 1 ]; then
  export ORCH_STUB_EXEC=1
  unset ORCH_JUDGMENT_CMD
else
  export ORCH_STUB_EXEC=0 ORCH_JUDGMENT_CMD="python $ROOT/spike/inngest_driver/claude_worker.py"
fi
start "${PY[@]}" -m spike.inngest_driver.driver >"$HOME_DIR/worker.log" 2>&1
REPO_LIST="$(IFS=,; echo ${REPOS[@]+"${REPOS[*]}"})"
export ORCH_RUNS_ROOT="$HOME_DIR/runs" ORCH_PACK="$PACK" ORCH_STUDIO_REPOS="$REPO_LIST" ORCH_PROJECTS_ROOT="$PROJECTS_ROOT"
start "${PY[@]}" -m spike.inngest_driver.studio_server >"$HOME_DIR/studio.log" 2>&1

echo "Studio:    http://localhost:8790"
echo "Inngest:   http://localhost:8288"
echo "Logs:      $HOME_DIR/*.log   (Ctrl-C stops everything)"
# bash 3.2 has no `wait -n`: poll until any of them exits.
while :; do
  for p in "${PIDS[@]}"; do kill -0 "$p" 2>/dev/null || { echo "a process exited; see the logs in $HOME_DIR" >&2; exit 1; }; done
  sleep 2
done
