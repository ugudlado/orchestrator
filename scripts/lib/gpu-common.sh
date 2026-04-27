#!/usr/bin/env bash
# gpu-common.sh — shared env loading, derived vars, and vendor-agnostic helpers.
# Sourced by gpu.sh (the dispatcher) before vendor files.
# Must not be executed directly.

# SCRIPT_DIR is inherited from gpu.sh (set before sourcing this file).

ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
PROFILE="${PROFILE:-}"
PROFILE_FILE="${PROFILE_FILE:-}"
LAST_POD_FILE="${LAST_POD_FILE:-}"  # finalised after profile loads (see below)

# Default RunPod create args (base values).
DEFAULT_POD_NAME="vllm-qwen"
DEFAULT_RUNPOD_IMAGE="runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
DEFAULT_GPU_TYPE="NVIDIA A40"
DEFAULT_GPU_FALLBACKS=""
DEFAULT_CLOUD_TYPE="COMMUNITY"
DEFAULT_CONTAINER_DISK_GB="30"
DEFAULT_RUNPOD_VOLUME_ID=""
DEFAULT_DATA_CENTER_IDS=""
DEFAULT_VOLUME_NAME="vllm-cache"
DEFAULT_VOLUME_SIZE_GB="60"
DEFAULT_VOLUME_CREATE_DC="EU-SE-1"
DEFAULT_VLLM_MODEL="QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ"
DEFAULT_VLLM_MAX_LEN="65536"

load_env_file() {
  local env_path="$1"
  if [ -f "$env_path" ]; then
    # shellcheck source=/dev/null
    set -a
    . "$env_path"
    set +a
  fi
}

resolve_profile_file() {
  if [ -n "$PROFILE_FILE" ]; then
    if [ ! -f "$PROFILE_FILE" ]; then
      echo "PROFILE_FILE does not exist: $PROFILE_FILE" >&2
      exit 1
    fi
    printf '%s\n' "$PROFILE_FILE"
    return 0
  fi

  if [ -n "$PROFILE" ]; then
    local candidate="$SCRIPT_DIR/profiles/$PROFILE.env"
    if [ ! -f "$candidate" ]; then
      echo "PROFILE '$PROFILE' not found at $candidate" >&2
      exit 1
    fi
    printf '%s\n' "$candidate"
    return 0
  fi

  return 1
}

load_env_file "$ENV_FILE"
if PROFILE_ENV_FILE="$(resolve_profile_file)"; then
  load_env_file "$PROFILE_ENV_FILE"
fi

# Derive per-pod state-file path from POD_NAME so concurrent profiles don't
# collide on a single .runpod-pod-id. Profile/.env can override LAST_POD_FILE.
if [ -z "$LAST_POD_FILE" ]; then
  if [ -n "${POD_NAME:-}" ]; then
    LAST_POD_FILE="$SCRIPT_DIR/.runpod-pod-id-$POD_NAME"
  else
    LAST_POD_FILE="$SCRIPT_DIR/.runpod-pod-id"
  fi
fi

POD_ID="${POD_ID:-}"
LOCAL_PORT="${LOCAL_PORT:-8000}"
REMOTE_PORT="${REMOTE_PORT:-8000}"
VLLM_PORT="${VLLM_PORT:-$REMOTE_PORT}"
VLLM_MODEL="${VLLM_MODEL:-$DEFAULT_VLLM_MODEL}"
VLLM_MAX_LEN="${VLLM_MAX_LEN:-$DEFAULT_VLLM_MAX_LEN}"
TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
TAILSCALE_HOSTNAME="${TAILSCALE_HOSTNAME:-}"
TAILSCALE_STATE_FILE="${TAILSCALE_STATE_FILE:-/workspace/tailscale.state}"
TUNNEL_PID_FILE="${TUNNEL_PID_FILE:-$SCRIPT_DIR/.tunnel-pid}"
SPEND_LOG="${SPEND_LOG:-$SCRIPT_DIR/.spend-log}"
SUPERVISOR_LOG="${SUPERVISOR_LOG:-$SCRIPT_DIR/.supervisor.log}"
SPOT="${SPOT:-0}"
SPOT_BID_PER_GPU="${SPOT_BID_PER_GPU:-0.22}"
DAILY_BUDGET="${DAILY_BUDGET:-5}"
# Hours-per-day cap. Empty = no cap (only $-budget enforced). e.g. DAILY_HOURS=6.
DAILY_HOURS="${DAILY_HOURS:-}"
SUPERVISOR_POLL_SECS="${SUPERVISOR_POLL_SECS:-30}"
SUPERVISOR_GRACE_SECS="${SUPERVISOR_GRACE_SECS:-60}"
NOTIFY_ENABLED="${NOTIFY_ENABLED:-1}"

notify() {
  local title="$1" msg="$2"
  echo "[$(date +%H:%M:%S)] $title: $msg" | tee -a "$SUPERVISOR_LOG"
  if [ "$NOTIFY_ENABLED" = "1" ] && command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$msg\" with title \"$title\"" 2>/dev/null || true
  fi
}

direct_access_enabled() {
  [ -n "$TAILSCALE_AUTHKEY" ]
}

effective_tailnet_hostname() {
  if [ -n "$TAILSCALE_HOSTNAME" ]; then
    printf '%s' "$TAILSCALE_HOSTNAME"
  elif [ "$SPOT" = "1" ]; then
    printf '%s-spot' "$CREATE_POD_NAME"
  else
    printf '%s' "$CREATE_POD_NAME"
  fi
}

pod_access_url() {
  if direct_access_enabled; then
    printf 'http://%s:%s' "$(effective_tailnet_hostname)" "$REMOTE_PORT"
  else
    printf 'http://localhost:%s' "$LOCAL_PORT"
  fi
}

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i $SSH_KEY"
RUNPOD_BIN="${RUNPOD_BIN:-}"
USE_RUNPODCTL_SSH="${USE_RUNPODCTL_SSH:-0}"
CREATE_POD_NAME="${POD_NAME:-$DEFAULT_POD_NAME}"
CREATE_RUNPOD_IMAGE="${RUNPOD_IMAGE:-$DEFAULT_RUNPOD_IMAGE}"
CREATE_GPU_TYPE="${GPU_TYPE:-$DEFAULT_GPU_TYPE}"
CREATE_GPU_FALLBACKS="${GPU_FALLBACKS:-$DEFAULT_GPU_FALLBACKS}"
CREATE_CLOUD_TYPE="${CLOUD_TYPE:-$DEFAULT_CLOUD_TYPE}"
CREATE_CONTAINER_DISK_GB="${CONTAINER_DISK_GB:-$DEFAULT_CONTAINER_DISK_GB}"
CREATE_RUNPOD_VOLUME_ID="${RUNPOD_VOLUME_ID:-$DEFAULT_RUNPOD_VOLUME_ID}"
CREATE_VOLUME_IN_GB="${VOLUME_IN_GB:-}"
CREATE_DATA_CENTER_IDS="${RUNPOD_DATA_CENTER_IDS:-$DEFAULT_DATA_CENTER_IDS}"
VOLUME_NAME="${VOLUME_NAME:-$DEFAULT_VOLUME_NAME}"
VOLUME_SIZE_GB="${VOLUME_SIZE_GB:-$DEFAULT_VOLUME_SIZE_GB}"
VOLUME_CREATE_DC="${VOLUME_CREATE_DC:-$DEFAULT_VOLUME_CREATE_DC}"

save_pod_id() {
  local pod_id="$1"
  printf "%s\n" "$pod_id" > "$LAST_POD_FILE"
}

resolve_pod_id() {
  if [ -n "${POD_ID:-}" ]; then
    return
  fi
  if [ -f "$LAST_POD_FILE" ]; then
    POD_ID="$(tr -d '[:space:]' < "$LAST_POD_FILE")"
  fi
  if [ -z "${POD_ID:-}" ]; then
    echo "POD_ID is not set and no cached pod ID found at $LAST_POD_FILE."
    echo "Set POD_ID or run: $0 create"
    exit 1
  fi
}

wait_for_ssh() {
  local i
  # 5 min — Vast pods routinely need 30-90s after "running" for sshd to bind.
  echo "Waiting for SSH on $POD_IP:$POD_SSH_PORT..."
  for i in $(seq 1 60); do
    if ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP" "echo ready" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  echo "SSH did not become ready in time."
  return 1
}

build_remote_env_prefix() {
  local var value prefix=""

  if [ -z "$TAILSCALE_HOSTNAME" ]; then
    TAILSCALE_HOSTNAME="$(effective_tailnet_hostname)"
  fi

  for var in VLLM_MODEL VLLM_MAX_LEN VLLM_PORT VLLM_GPU_UTIL VLLM_TOOL_PARSER VLLM_REASONING_PARSER VLLM_EXTRA_ARGS TAILSCALE_AUTHKEY TAILSCALE_HOSTNAME TAILSCALE_STATE_FILE; do
    value="${!var:-}"
    if [ -n "$value" ]; then
      prefix+="$var=$(printf '%q' "$value") "
    fi
  done
  printf '%s' "$prefix"
}

run_remote_setup() {
  local setup_dest="/workspace/setup-vllm.sh"
  local remote_env

  if [ -z "$CREATE_RUNPOD_VOLUME_ID" ]; then
    echo "WARNING: no network volume attached — model will not persist across pod recreations."
  fi

  echo "Copying setup script to pod ($setup_dest)..."
  scp $SSH_OPTS -P "$POD_SSH_PORT" "$SCRIPT_DIR/setup-vllm.sh" "root@$POD_IP:$setup_dest"

  remote_env="$(build_remote_env_prefix)"
  if direct_access_enabled; then
    echo "Running remote vLLM setup (model=$VLLM_MODEL, access=tailnet)..."
  else
    echo "Running remote vLLM setup (model=$VLLM_MODEL, access=legacy tunnel fallback)..."
  fi
  ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP" \
    "chmod +x $setup_dest && ${remote_env}bash $setup_dest"
}

kill_existing_tunnel() {
  if [ -f "$TUNNEL_PID_FILE" ]; then
    local old_pid
    old_pid="$(cat "$TUNNEL_PID_FILE" 2>/dev/null)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      echo "Killing existing tunnel (pid $old_pid)..."
      kill "$old_pid" 2>/dev/null || true
    fi
    rm -f "$TUNNEL_PID_FILE"
  fi
}

open_tunnel_bg() {
  kill_existing_tunnel
  echo "Opening tunnel in background: localhost:$LOCAL_PORT -> pod:$REMOTE_PORT"
  ssh $SSH_OPTS -T -N -L "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" -p "$POD_SSH_PORT" "root@$POD_IP" &
  echo $! > "$TUNNEL_PID_FILE"
  sleep 2
  if kill -0 "$(cat "$TUNNEL_PID_FILE")" 2>/dev/null; then
    echo "Tunnel up. Test: curl http://localhost:$LOCAL_PORT/v1/models"
  else
    echo "Tunnel failed to start — run '$0 tunnel' to try foreground."
    rm -f "$TUNNEL_PID_FILE"
  fi
}

start_tunnel() {
  kill_existing_tunnel
  echo "Opening tunnel: localhost:$LOCAL_PORT -> pod:$REMOTE_PORT"
  echo "Press Ctrl+C to close tunnel."
  ssh $SSH_OPTS -T -N -L "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" -p "$POD_SSH_PORT" "root@$POD_IP"
}

record_spend_start() {
  # Usage: record_spend_start [explicit_rate]
  # If explicit_rate is provided, use it (vendor-supplied $/hr). Otherwise fall
  # back to RunPod's runpodctl query — the original behavior.
  local now cost_per_hr
  now="$(date +%s)"
  if [ -n "${1:-}" ]; then
    cost_per_hr="$1"
  elif [ "$SPOT" = "1" ]; then
    cost_per_hr="${SPOT_BID_PER_GPU:-0.44}"
  else
    cost_per_hr="$(runpodctl pod get "$POD_ID" 2>/dev/null | jq -r '.costPerHr // empty' 2>/dev/null)"
    [ -z "$cost_per_hr" ] && cost_per_hr="0.44"
  fi
  echo "$now $POD_ID $cost_per_hr" >> "$SPEND_LOG"
}

record_spend_stop() {
  local now end_pod
  now="$(date +%s)"
  end_pod="${1:-$POD_ID}"
  echo "$now $end_pod STOP" >> "$SPEND_LOG"
}

compute_spend_today() {
  # Sum cost for the current local day.
  # Log format: <epoch> <pod_id> <rate_or_STOP>
  local today_start now
  today_start="$(date -v0H -v0M -v0S +%s 2>/dev/null || date -d 'today 00:00:00' +%s)"
  now="$(date +%s)"
  awk -v start="$today_start" -v now="$now" '
    {
      if ($3 != "STOP") {
        active[$2] = $1
        rate[$2] = $3
      } else if (($2) in active) {
        begin = (active[$2] < start ? start : active[$2])
        duration_h = ($1 - begin) / 3600.0
        if (duration_h > 0) total += duration_h * rate[$2]
        delete active[$2]
      }
    }
    END {
      # pods still active: count from their start (or day-start) to now
      for (p in active) {
        begin = (active[p] < start ? start : active[p])
        duration_h = (now - begin) / 3600.0
        if (duration_h > 0) total += duration_h * rate[p]
      }
      printf "%.2f", (total ? total : 0)
    }
  ' "$SPEND_LOG" 2>/dev/null || echo "0.00"
}

compute_hours_today() {
  # Total pod-uptime hours within the current local day (same log, ignore rate).
  local today_start now
  today_start="$(date -v0H -v0M -v0S +%s 2>/dev/null || date -d 'today 00:00:00' +%s)"
  now="$(date +%s)"
  awk -v start="$today_start" -v now="$now" '
    {
      if ($3 != "STOP") {
        active[$2] = $1
      } else if (($2) in active) {
        begin = (active[$2] < start ? start : active[$2])
        duration_h = ($1 - begin) / 3600.0
        if (duration_h > 0) total += duration_h
        delete active[$2]
      }
    }
    END {
      for (p in active) {
        begin = (active[p] < start ? start : active[p])
        duration_h = (now - begin) / 3600.0
        if (duration_h > 0) total += duration_h
      }
      printf "%.2f", (total ? total : 0)
    }
  ' "$SPEND_LOG" 2>/dev/null || echo "0.00"
}

spend_status() {
  local today hours
  today="$(compute_spend_today)"
  hours="$(compute_hours_today)"
  echo "Spend today: \$$today (budget: \$$DAILY_BUDGET)"
  echo "Hours today: ${hours}h (cap: ${DAILY_HOURS:-none})"
  echo "Log: $SPEND_LOG"
}

# ---------------------------------------------------------------------------
# Supervise — vendor-agnostic cost/hours watchdog.
#
# supervise_cap_hit_grace <reason> <value> <cap> <label>
#   Notify + countdown for SUPERVISOR_GRACE_SECS. Ctrl+C aborts via outer trap.
#
# supervise_loop <terminate_fn> <label>
#   Polls every SUPERVISOR_POLL_SECS. On budget/hours cap hit, runs the grace
#   countdown then calls <terminate_fn> and exits (3 = budget, 5 = hours).
#   <label> is the vendor name shown in notifications ("Vast", "RunPod").
# ---------------------------------------------------------------------------

supervise_cap_hit_grace() {
  local reason="$1" value="$2" cap="$3" label="$4"
  notify "$label Supervisor" "$reason cap reached ($value >= $cap) — terminating in ${SUPERVISOR_GRACE_SECS}s. Ctrl+C to abort."
  local remaining="$SUPERVISOR_GRACE_SECS"
  local step=15
  while [ "$remaining" -gt 0 ]; do
    local s=$(( remaining < step ? remaining : step ))
    sleep "$s"
    remaining=$(( remaining - s ))
    [ "$remaining" -gt 0 ] && notify "$label Supervisor" "${remaining}s until terminate"
  done
}

supervise_loop() {
  local terminate_fn="$1" label="${2:-Pod}"

  echo "$label supervisor starting (poll=${SUPERVISOR_POLL_SECS}s budget=\$$DAILY_BUDGET/day hours=${DAILY_HOURS:-none}/day grace=${SUPERVISOR_GRACE_SECS}s)."
  echo "Ctrl+C to stop. Logs: $SUPERVISOR_LOG"
  notify "$label Supervisor" "Started — \$$DAILY_BUDGET/day, ${DAILY_HOURS:-no}h/day, grace=${SUPERVISOR_GRACE_SECS}s"

  trap 'notify "'"$label"' Supervisor" "Stopped by user"; exit 0' INT TERM

  while true; do
    local spent hours
    spent="$(compute_spend_today)"

    if awk -v s="$spent" -v b="$DAILY_BUDGET" 'BEGIN { exit !(s+0 >= b+0) }'; then
      supervise_cap_hit_grace "budget" "$spent" "$DAILY_BUDGET" "$label"
      notify "$label Supervisor" "BUDGET HIT (\$$spent >= \$$DAILY_BUDGET) — terminating"
      "$terminate_fn" 2>/dev/null || true
      exit 3
    fi

    if [ -n "$DAILY_HOURS" ]; then
      hours="$(compute_hours_today)"
      if awk -v h="$hours" -v c="$DAILY_HOURS" 'BEGIN { exit !(h+0 >= c+0) }'; then
        supervise_cap_hit_grace "hours" "$hours" "$DAILY_HOURS" "$label"
        notify "$label Supervisor" "HOURS HIT (${hours}h >= ${DAILY_HOURS}h) — terminating"
        "$terminate_fn" 2>/dev/null || true
        exit 5
      fi
    fi

    echo "[$(date +%H:%M:%S)] OK — \$$spent today" >> "$SUPERVISOR_LOG"
    sleep "$SUPERVISOR_POLL_SECS"
  done
}
