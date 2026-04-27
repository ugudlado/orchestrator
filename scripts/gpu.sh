#!/usr/bin/env bash
# gpu.sh — vendor dispatcher for GPU pod management.
#
# Usage:
#   gpu.sh <vendor> <command> [args]   explicit vendor
#   gpu.sh <command> [args]            vendor defaults to $DEFAULT_GPU_VENDOR (fallback: vast)
#
# Vendors: runpod  (vast coming in stage 2)
# Commands: create | ssh | tunnel | terminate | status | health | spend | logs | restart-vllm | supervise

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse vendor + command from positional args.
# If $1 matches a known command name, treat it as the command and use the
# default vendor. Otherwise treat $1 as the vendor and $2 as the command.
# ---------------------------------------------------------------------------
KNOWN_COMMANDS="create|ssh|tunnel|terminate|status|health|spend|logs|restart-vllm|supervise"

_is_command() {
  printf '%s' "$1" | grep -qE "^($KNOWN_COMMANDS)$"
}

if [ $# -eq 0 ]; then
  VENDOR=""
  COMMAND=""
elif _is_command "${1:-}"; then
  # gpu.sh <command> — use default vendor
  VENDOR="${DEFAULT_GPU_VENDOR:-vast}"
  COMMAND="$1"
  shift
else
  # gpu.sh <vendor> [command]
  VENDOR="$1"
  shift
  COMMAND="${1:-}"
  [ $# -gt 0 ] && shift || true
fi

# ---------------------------------------------------------------------------
# Source shared library (runs env loading + var defaulting as side-effects)
# ---------------------------------------------------------------------------
set +e
# shellcheck source=lib/gpu-common.sh
source "$SCRIPT_DIR/lib/gpu-common.sh"
_src_rc=$?
set -e
if [ $_src_rc -ne 0 ]; then
  echo "ERROR: failed to source $SCRIPT_DIR/lib/gpu-common.sh (exit $_src_rc)" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Usage (vendor-aware)
# ---------------------------------------------------------------------------
usage() {
  echo "Usage: $0 [<vendor>] <command>"
  echo ""
  echo "Vendors:  runpod  (vast — stage 2, not yet implemented)"
  echo "          Default vendor: \${DEFAULT_GPU_VENDOR:-vast}"
  echo ""
  echo "Commands:"
  echo "  create         Create pod, attach volume, install vLLM, open tunnel"
  echo "                   SPOT=1 prefix → interruptible bid pricing"
  echo "  ssh            Interactive shell on pod"
  echo "  tunnel         Open SSH tunnel localhost:$LOCAL_PORT -> pod:$REMOTE_PORT"
  echo "  status         Show pod state + tunnel + vLLM health"
  echo "  health         One-shot health probe (for scripting)"
  echo "  spend          Show today's accumulated pod spend"
  echo "  logs           Tail /workspace/vllm.log on the pod"
  echo "  restart-vllm   Restart vLLM without recreating pod"
  echo "  terminate      Delete the pod (network volume survives)"
  echo "  supervise      Watchdog loop: auto-recreate on spot interruption,"
  echo "                   auto-restart vLLM if it dies, kill on budget hit"
  echo ""
  echo "Examples:"
  echo "  $0 runpod create"
  echo "  $0 runpod status"
  echo "  $0 runpod terminate"
  echo ""
  echo "Env vars (also read from \$ENV_FILE=$ENV_FILE):"
  echo "  PROFILE=<name> or PROFILE_FILE=<path> (loads after $ENV_FILE)"
  echo "  VLLM_MODEL=$VLLM_MODEL"
  echo "  VLLM_MAX_LEN=$VLLM_MAX_LEN"
  echo "  TAILSCALE_AUTHKEY=${TAILSCALE_AUTHKEY:-<unset>}"
  echo "  TAILSCALE_HOSTNAME=${TAILSCALE_HOSTNAME:-<auto>}"
  echo "  LOCAL_PORT=$LOCAL_PORT  REMOTE_PORT=$REMOTE_PORT"
  echo "  SPOT=$SPOT  SPOT_BID_PER_GPU=\$$SPOT_BID_PER_GPU  DAILY_BUDGET=\$$DAILY_BUDGET"
  echo "  SUPERVISOR_POLL_SECS=$SUPERVISOR_POLL_SECS  SUPERVISOR_GRACE_SECS=$SUPERVISOR_GRACE_SECS"
  echo "  NOTIFY_ENABLED=$NOTIFY_ENABLED (macOS desktop notifications)"
  echo "  POD_NAME, GPU_TYPE, CLOUD_TYPE, CONTAINER_DISK_GB"
  echo "  VOLUME_NAME, VOLUME_SIZE_GB, VOLUME_CREATE_DC"
  echo "  RUNPOD_VOLUME_ID, RUNPOD_DATA_CENTER_IDS (explicit overrides)"
}

# ---------------------------------------------------------------------------
# Validate vendor + command; load vendor module
# ---------------------------------------------------------------------------
if [ -z "$VENDOR" ] || [ -z "$COMMAND" ]; then
  usage
  exit 1
fi

VENDOR_FILE="$SCRIPT_DIR/vendors/${VENDOR}.sh"
if [ ! -f "$VENDOR_FILE" ]; then
  echo "ERROR: $VENDOR vendor not implemented yet." >&2
  echo "       Use: $0 runpod <command>" >&2
  exit 1
fi

set +e
# shellcheck source=vendors/runpod.sh
source "$VENDOR_FILE"
_src_rc=$?
set -e
if [ $_src_rc -ne 0 ]; then
  echo "ERROR: failed to source $VENDOR_FILE (exit $_src_rc)" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Dispatch: translate restart-vllm → restart_vllm, then call <vendor>_<fn>
# ---------------------------------------------------------------------------
FN="${VENDOR}_${COMMAND//-/_}"

if ! declare -f "$FN" >/dev/null 2>&1; then
  echo "ERROR: command '$COMMAND' not supported by vendor '$VENDOR' (expected function $FN)" >&2
  usage
  exit 1
fi

"$FN" "$@"
