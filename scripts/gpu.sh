#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
PROFILE="${PROFILE:-}"
PROFILE_FILE="${PROFILE_FILE:-}"
LAST_POD_FILE="${LAST_POD_FILE:-$SCRIPT_DIR/.runpod-pod-id}"

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
SUPERVISOR_POLL_SECS="${SUPERVISOR_POLL_SECS:-30}"
SUPERVISOR_MAX_FAILS_PER_HOUR="${SUPERVISOR_MAX_FAILS_PER_HOUR:-5}"
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

detect_runpod_cli() {
  if [ -n "$RUNPOD_BIN" ]; then
    return
  fi
  if command -v runpodctl >/dev/null 2>&1; then
    RUNPOD_BIN="runpodctl"
    return
  fi
  if command -v runpod >/dev/null 2>&1; then
    RUNPOD_BIN="runpod"
    return
  fi
  echo "Could not find RunPod CLI. Install runpodctl (preferred) or runpod."
  exit 1
}

usage() {
  echo "Usage: $0 create|ssh|tunnel|terminate|status|logs|restart-vllm|health|spend|supervise"
  echo ""
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
  echo "Env vars (also read from \$ENV_FILE=$ENV_FILE):"
  echo "  PROFILE=<name> or PROFILE_FILE=<path> (loads after $ENV_FILE)"
  echo "  VLLM_MODEL=$VLLM_MODEL"
  echo "  VLLM_MAX_LEN=$VLLM_MAX_LEN"
  echo "  TAILSCALE_AUTHKEY=${TAILSCALE_AUTHKEY:-<unset>}"
  echo "  TAILSCALE_HOSTNAME=${TAILSCALE_HOSTNAME:-<auto>}"
  echo "  LOCAL_PORT=$LOCAL_PORT  REMOTE_PORT=$REMOTE_PORT"
  echo "  SPOT=$SPOT  SPOT_BID_PER_GPU=\$$SPOT_BID_PER_GPU  DAILY_BUDGET=\$$DAILY_BUDGET"
  echo "  SUPERVISOR_POLL_SECS=$SUPERVISOR_POLL_SECS  SUPERVISOR_MAX_FAILS_PER_HOUR=$SUPERVISOR_MAX_FAILS_PER_HOUR"
  echo "  NOTIFY_ENABLED=$NOTIFY_ENABLED (macOS desktop notifications)"
  echo "  POD_NAME, GPU_TYPE, CLOUD_TYPE, CONTAINER_DISK_GB"
  echo "  VOLUME_NAME, VOLUME_SIZE_GB, VOLUME_CREATE_DC"
  echo "  RUNPOD_VOLUME_ID, RUNPOD_DATA_CENTER_IDS (explicit overrides)"
}

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

extract_created_pod_id() {
  local create_output="$1"
  if [ "$RUNPOD_BIN" = "runpodctl" ]; then
    printf "%s\n" "$create_output" | python3 -c '
import json, sys

raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(1)

try:
    data = json.loads(raw)
except Exception:
    raise SystemExit(1)

pod_hints = {
    "name", "imageName", "gpuCount", "containerDiskInGb", "networkVolumeId",
    "desiredStatus", "costPerHr", "machineId", "ports"
}

best = None

def walk(node):
    global best
    if isinstance(node, dict):
        pod_id = node.get("id")
        if isinstance(pod_id, str):
            score = 0
            keys = set(node.keys())
            score += len(keys & pod_hints)
            if score > 0:
                print(pod_id)
                raise SystemExit(0)
            if best is None:
                best = pod_id
        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for item in node:
            walk(item)

try:
    walk(data)
except SystemExit as e:
    if e.code == 0:
        raise

if best:
    print(best)
    raise SystemExit(0)
raise SystemExit(1)
' 2>/dev/null || true
  else
    printf "%s\n" "$create_output" | grep -Eo '[a-z0-9]{8,}(-[a-z0-9]+)*' | head -n 1 || true
  fi
}

get_pod_endpoint() {
  local pod_line pod_info
  detect_runpod_cli
  if [ "$RUNPOD_BIN" = "runpodctl" ]; then
    pod_info="$("$RUNPOD_BIN" pod get "$POD_ID" --include-machine 2>/dev/null || true)"
    if [ -z "$pod_info" ]; then
      echo "Could not fetch pod details for $POD_ID."
      exit 1
    fi

    # RunPod REST returns SSH endpoint at .ssh.ip / .ssh.port (top-level, not nested in portMappings)
    read -r POD_IP POD_SSH_PORT < <(printf "%s" "$pod_info" | jq -r '.ssh.ip + " " + (.ssh.port | tostring)' 2>/dev/null)
    if [ "$POD_IP" = "null" ] || [ -z "$POD_IP" ]; then
      POD_IP=""
      POD_SSH_PORT=""
    fi
  else
    pod_line="$("$RUNPOD_BIN" pod list | awk -v pod="$POD_ID" '$0 ~ pod {print; exit}')"
    if [ -z "$pod_line" ]; then
      echo "Could not find pod $POD_ID in $RUNPOD_BIN pod list output."
      exit 1
    fi

    POD_IP="$(printf "%s\n" "$pod_line" | awk '{print $6}')"
    POD_SSH_PORT="$(printf "%s\n" "$pod_line" | awk '{print $7}')"
  fi

  if [ -z "$POD_IP" ] || [ -z "$POD_SSH_PORT" ]; then
    echo "Failed to parse pod SSH endpoint for pod $POD_ID."
    exit 1
  fi
}

wait_for_ssh() {
  local i
  echo "Waiting for SSH on $POD_IP:$POD_SSH_PORT..."
  for i in $(seq 1 30); do
    if ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP" "echo ready" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  echo "SSH did not become ready in time."
  exit 1
}

build_remote_env_prefix() {
  local var value prefix=""

  if [ -z "$TAILSCALE_HOSTNAME" ]; then
    TAILSCALE_HOSTNAME="$(effective_tailnet_hostname)"
  fi

  for var in VLLM_MODEL VLLM_MAX_LEN VLLM_PORT VLLM_GPU_UTIL VLLM_TOOL_PARSER VLLM_REASONING_PARSER TAILSCALE_AUTHKEY TAILSCALE_HOSTNAME TAILSCALE_STATE_FILE; do
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

ssh_into_pod() {
  resolve_pod_id
  detect_runpod_cli

  if [ "$USE_RUNPODCTL_SSH" = "1" ] && [ "$RUNPOD_BIN" = "runpodctl" ] && runpodctl ssh --help 2>/dev/null | rg -q "connect"; then
    runpodctl ssh connect "$POD_ID"
    return
  fi

  get_pod_endpoint
  wait_for_ssh
  echo "Connecting to pod shell: root@$POD_IP:$POD_SSH_PORT"
  ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP"
}

discover_or_create_volume() {
  # Honors explicit RUNPOD_VOLUME_ID override — skip discovery if user set it.
  if [ -n "$CREATE_RUNPOD_VOLUME_ID" ]; then
    if [ -z "$CREATE_DATA_CENTER_IDS" ]; then
      echo "RUNPOD_VOLUME_ID set but RUNPOD_DATA_CENTER_IDS is empty."
      echo "Looking up volume's data center..."
      local vol_info vol_dc
      vol_info="$("$RUNPOD_BIN" network-volume get "$CREATE_RUNPOD_VOLUME_ID" 2>/dev/null || true)"
      vol_dc="$(printf "%s" "$vol_info" | jq -r '.dataCenterId // empty' 2>/dev/null || true)"
      if [ -n "$vol_dc" ]; then
        CREATE_DATA_CENTER_IDS="$vol_dc"
        echo "Pinned DC to $CREATE_DATA_CENTER_IDS (from volume $CREATE_RUNPOD_VOLUME_ID)."
      fi
    fi
    return
  fi

  # VOLUME_NAME=none skips network volume entirely (ephemeral disk only)
  if [ "$VOLUME_NAME" = "none" ]; then
    echo "VOLUME_NAME=none — skipping network volume, using ephemeral disk."
    return
  fi

  echo "Looking for existing network volume named '$VOLUME_NAME'..."
  local volumes_json matched_id matched_dc
  volumes_json="$("$RUNPOD_BIN" network-volume list 2>/dev/null || true)"

  if [ -z "$volumes_json" ] || ! printf "%s" "$volumes_json" | jq -e . >/dev/null 2>&1; then
    echo "Could not list network volumes (API unreachable?). Falling back to ephemeral disk."
    return
  fi

  matched_id="$(printf "%s" "$volumes_json" | jq -r --arg n "$VOLUME_NAME" '[.[] | select(.name == $n)][0].id // empty')"
  matched_dc="$(printf "%s" "$volumes_json" | jq -r --arg n "$VOLUME_NAME" '[.[] | select(.name == $n)][0].dataCenterId // empty')"

  if [ -n "$matched_id" ] && [ -n "$matched_dc" ]; then
    CREATE_RUNPOD_VOLUME_ID="$matched_id"
    CREATE_DATA_CENTER_IDS="$matched_dc"
    echo "Found volume: $matched_id in $matched_dc"
    return
  fi

  echo "No volume named '$VOLUME_NAME' found. Creating ${VOLUME_SIZE_GB}GB volume in $VOLUME_CREATE_DC..."
  local create_vol_output new_vol_id
  if ! create_vol_output="$("$RUNPOD_BIN" network-volume create \
    --name "$VOLUME_NAME" \
    --size "$VOLUME_SIZE_GB" \
    --data-center-id "$VOLUME_CREATE_DC" 2>&1)"; then
    echo "Volume creation failed:"
    printf "%s\n" "$create_vol_output"
    echo "Falling back to ephemeral disk."
    return
  fi

  new_vol_id="$(printf "%s" "$create_vol_output" | jq -r '.id // empty' 2>/dev/null || true)"
  if [ -z "$new_vol_id" ]; then
    echo "Could not parse new volume ID from:"
    printf "%s\n" "$create_vol_output"
    echo "Falling back to ephemeral disk."
    return
  fi

  CREATE_RUNPOD_VOLUME_ID="$new_vol_id"
  CREATE_DATA_CENTER_IDS="$VOLUME_CREATE_DC"
  echo "Created volume: $new_vol_id in $VOLUME_CREATE_DC"
}

# GraphQL helper — needs RUNPOD_API_KEY in env
runpod_graphql() {
  local query="$1"
  if [ -z "${RUNPOD_API_KEY:-}" ]; then
    echo "ERROR: RUNPOD_API_KEY not set in $ENV_FILE" >&2
    exit 1
  fi
  curl -fsS -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg q "$query" '{query: $q}')"
}

create_spot_pod() {
  detect_runpod_cli
  discover_or_create_volume

  if [ -z "$CREATE_RUNPOD_VOLUME_ID" ] || [ -z "$CREATE_DATA_CENTER_IDS" ]; then
    echo "Spot pod requires a network volume + data center. Volume discovery failed." >&2
    exit 1
  fi

  # Map GPU friendly name → GraphQL gpuTypeId
  local gpu_type_id
  case "$CREATE_GPU_TYPE" in
    "NVIDIA A40") gpu_type_id="NVIDIA A40" ;;
    "NVIDIA A100 80GB PCIe") gpu_type_id="NVIDIA A100 80GB PCIe" ;;
    "NVIDIA A100-SXM4-80GB") gpu_type_id="NVIDIA A100-SXM4-80GB" ;;
    "NVIDIA H100 80GB HBM3") gpu_type_id="NVIDIA H100 80GB HBM3" ;;
    "NVIDIA RTX A6000") gpu_type_id="NVIDIA RTX A6000" ;;
    *) gpu_type_id="$CREATE_GPU_TYPE" ;;
  esac

  local query
  query=$(cat <<EOF
mutation {
  podRentInterruptable(input: {
    bidPerGpu: $SPOT_BID_PER_GPU,
    cloudType: COMMUNITY,
    gpuCount: 1,
    volumeInGb: 0,
    containerDiskInGb: $CREATE_CONTAINER_DISK_GB,
    minVcpuCount: 2,
    minMemoryInGb: 15,
    gpuTypeId: "$gpu_type_id",
    name: "$CREATE_POD_NAME-spot",
    imageName: "$CREATE_RUNPOD_IMAGE",
    dockerArgs: "",
    ports: "8000/http,22/tcp",
    volumeMountPath: "/workspace",
    networkVolumeId: "$CREATE_RUNPOD_VOLUME_ID",
    dataCenterId: "$CREATE_DATA_CENTER_IDS",
    startSsh: true
  }) {
    id
    desiredStatus
    costPerHr
    machineId
  }
}
EOF
  )

  echo "Creating SPOT pod: bid=\$$SPOT_BID_PER_GPU/hr gpu=$gpu_type_id dc=$CREATE_DATA_CENTER_IDS"
  local response new_pod_id
  response="$(runpod_graphql "$query")"

  new_pod_id="$(printf "%s" "$response" | jq -r '.data.podRentInterruptable.id // empty')"
  if [ -z "$new_pod_id" ]; then
    echo "Spot pod creation failed. Response:" >&2
    printf "%s\n" "$response" >&2
    return 1
  fi

  POD_ID="$new_pod_id"
  save_pod_id "$POD_ID"
  echo "Created spot pod: $POD_ID"
  return 0
}

create_pod() {
  local create_output created_pod_id
  local -a create_cmd gpu_candidates fallback_gpus
  local candidate

  # Branch to spot-create path if SPOT=1
  if [ "$SPOT" = "1" ]; then
    create_spot_pod || exit 1
    sleep 10
    get_pod_endpoint
    wait_for_ssh
    run_remote_setup
    if direct_access_enabled; then
      kill_existing_tunnel
    else
      open_tunnel_bg
    fi
    record_spend_start
    echo ""
    if direct_access_enabled; then
      echo "✅ Spot pod ready. vLLM serving $VLLM_MODEL on http://$(effective_tailnet_hostname):$REMOTE_PORT"
      echo "   Tailscale direct access is active; run '$0 tunnel' only for the fallback path."
    else
      echo "✅ Spot pod ready. vLLM serving $VLLM_MODEL on http://localhost:$LOCAL_PORT"
    fi
    echo "   Run '$0 supervise' to auto-recreate on interruption."
    return 0
  fi

  detect_runpod_cli
  discover_or_create_volume

  echo "Creating new RunPod instance via $RUNPOD_BIN..."

  if [ -n "${RUNPOD_CREATE_ARGS:-}" ]; then
    echo "Using explicit RUNPOD_CREATE_ARGS."
    # RUNPOD_CREATE_ARGS is intentionally word-split as CLI flags.
    # shellcheck disable=SC2086
    if ! create_output="$("$RUNPOD_BIN" pod create ${RUNPOD_CREATE_ARGS} 2>&1)"; then
      printf "%s\n" "$create_output"
      exit 1
    fi
  else
    create_cmd=(
      "$RUNPOD_BIN" pod create
      --name "$CREATE_POD_NAME"
      --image "$CREATE_RUNPOD_IMAGE"
      --cloud-type "$CREATE_CLOUD_TYPE"
      --container-disk-in-gb "$CREATE_CONTAINER_DISK_GB"
      --ssh
    )

    if [ -n "$CREATE_RUNPOD_VOLUME_ID" ]; then
      create_cmd+=(--network-volume-id "$CREATE_RUNPOD_VOLUME_ID")
    elif [ -n "$CREATE_VOLUME_IN_GB" ]; then
      create_cmd+=(--volume-in-gb "$CREATE_VOLUME_IN_GB")
    else
      echo "No network volume or persistent volume set — model will live on ephemeral"
      echo "container disk and will be re-pulled on every create."
    fi

    if [ -n "${RUNPOD_PORTS:-}" ]; then
      create_cmd+=(--ports "$RUNPOD_PORTS")
    fi

    if [ -n "$CREATE_DATA_CENTER_IDS" ]; then
      create_cmd+=(--data-center-ids "$CREATE_DATA_CENTER_IDS")
    fi

    gpu_candidates=("$CREATE_GPU_TYPE")
    if [ -n "$CREATE_GPU_FALLBACKS" ]; then
      IFS=',' read -r -a fallback_gpus <<< "$CREATE_GPU_FALLBACKS"
      for candidate in "${fallback_gpus[@]}"; do
        candidate="$(printf "%s" "$candidate" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        if [ -n "$candidate" ]; then
          gpu_candidates+=("$candidate")
        fi
      done
    fi

    echo "Using default create args from env/script."
    create_output=""
    for candidate in "${gpu_candidates[@]}"; do
      echo "Trying GPU type: $candidate"
      if create_output="$("${create_cmd[@]}" --gpu-id "$candidate" 2>&1)"; then
        break
      fi
      printf "%s\n" "$create_output"
      create_output=""
      echo "Create failed for GPU type: $candidate"
    done

    if [ -z "$create_output" ]; then
      echo "Pod creation failed for all configured GPU options."
      echo "Set GPU_TYPE or GPU_FALLBACKS in $ENV_FILE to available SKUs."
      exit 1
    fi
  fi

  printf "%s\n" "$create_output"

  created_pod_id="$(extract_created_pod_id "$create_output")"
  if [ -z "$created_pod_id" ]; then
    echo "Could not parse POD_ID from create output. Set POD_ID manually for follow-up commands."
    exit 1
  fi

  POD_ID="$created_pod_id"
  save_pod_id "$POD_ID"
  echo "Created pod: $POD_ID"

  sleep 10
  get_pod_endpoint
  wait_for_ssh
  run_remote_setup
  if direct_access_enabled; then
    kill_existing_tunnel
  else
    open_tunnel_bg
  fi
  record_spend_start

  echo ""
  if direct_access_enabled; then
    echo "✅ Pod ready. vLLM serving $VLLM_MODEL on http://$(effective_tailnet_hostname):$REMOTE_PORT"
    echo "   Tailscale direct access is active; use '$0 tunnel' only for the legacy fallback."
  else
    echo "✅ Pod ready. vLLM serving $VLLM_MODEL on http://localhost:$LOCAL_PORT"
  fi
  echo ""
  echo "Common commands:"
  if direct_access_enabled; then
    echo "  curl http://$(effective_tailnet_hostname):$REMOTE_PORT/v1/models"
    echo "  $0 tunnel       # open the legacy SSH tunnel fallback"
  else
    echo "  curl http://localhost:$LOCAL_PORT/v1/models"
  fi
  echo "  $0 logs         # tail vLLM log"
  echo "  $0 ssh          # shell on pod"
  echo "  $0 restart-vllm # bounce vLLM without recreating pod"
  echo "  $0 terminate    # delete pod (volume survives)"
}

terminate_pod() {
  resolve_pod_id
  detect_runpod_cli
  kill_existing_tunnel
  record_spend_stop
  echo "Terminating pod $POD_ID..."
  if "$RUNPOD_BIN" pod delete "$POD_ID" >/dev/null 2>&1; then
    echo "Pod terminated."
  elif "$RUNPOD_BIN" pod terminate "$POD_ID" >/dev/null 2>&1; then
    echo "Pod terminated."
  else
    # Backward compatibility for older CLIs.
    "$RUNPOD_BIN" pod stop "$POD_ID"
    echo "Pod stopped (delete/terminate command not available in this CLI)."
  fi
  rm -f "$LAST_POD_FILE"
  echo "Network volume $CREATE_RUNPOD_VOLUME_ID (if any) survives — delete via console to stop storage billing."
}

tail_vllm_logs() {
  resolve_pod_id
  detect_runpod_cli
  get_pod_endpoint
  wait_for_ssh
  echo "Tailing /workspace/vllm.log (Ctrl+C to stop)..."
  ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP" "tail -f /workspace/vllm.log"
}

restart_vllm_on_pod() {
  resolve_pod_id
  detect_runpod_cli
  get_pod_endpoint
  wait_for_ssh
  local remote_env
  remote_env="$(build_remote_env_prefix)"
  echo "Restarting vLLM on pod (model=$VLLM_MODEL)..."
  ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP" \
    "pkill -9 -f 'vllm serve' 2>/dev/null; sleep 3; ${remote_env}bash /workspace/setup-vllm.sh"
  echo "vLLM restarted. Tunnel (if any) should resume automatically once server is up."
}

pod_status() {
  resolve_pod_id
  detect_runpod_cli
  echo "Pod: $POD_ID"
  if [ "$RUNPOD_BIN" = "runpodctl" ]; then
    "$RUNPOD_BIN" pod get "$POD_ID" 2>&1 | jq '{status: .desiredStatus, uptime_s: .uptimeSeconds, gpu: .machine.gpuId, dc: .machine.dataCenterId, cost: .costPerHr, ssh_ip: .ssh.ip, ssh_port: .ssh.port}' 2>/dev/null || echo "Failed to fetch pod status."
  else
    "$RUNPOD_BIN" pod list | awk -v pod="$POD_ID" 'NR==1 || $0 ~ pod'
  fi

  echo ""
  echo "Tunnel (local):"
  if [ -f "$TUNNEL_PID_FILE" ]; then
    local tp
    tp="$(cat "$TUNNEL_PID_FILE")"
    if kill -0 "$tp" 2>/dev/null; then
      echo "  pid=$tp alive on :$LOCAL_PORT"
    else
      echo "  pid=$tp DEAD (run '$0 tunnel' to reopen)"
    fi
  else
    echo "  no tunnel recorded (run '$0 tunnel')"
  fi

  echo ""
  if direct_access_enabled; then
    echo "vLLM (direct tailnet):"
    if curl -fsS --max-time 3 "$(pod_access_url)/v1/models" 2>/dev/null | jq -r '.data[0].id' 2>/dev/null | head -1; then
      :
    else
      echo "  unreachable on $(pod_access_url)"
    fi
  else
    echo "vLLM (via tunnel):"
    if curl -fsS --max-time 3 "http://localhost:$LOCAL_PORT/v1/models" 2>/dev/null | jq -r '.data[0].id' 2>/dev/null | head -1; then
      :
    else
      echo "  unreachable on localhost:$LOCAL_PORT"
    fi
  fi
}

record_spend_start() {
  local now cost_per_hr
  now="$(date +%s)"
  cost_per_hr="${SPOT_BID_PER_GPU:-0.44}"
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
  local today_start
  today_start="$(date -v0H -v0M -v0S +%s 2>/dev/null || date -d 'today 00:00:00' +%s)"
  awk -v start="$today_start" '
    {
      if ($3 != "STOP") {
        active[$2] = $1
        rate[$2] = $3
      } else if (active[$2]) {
        begin = (active[$2] < start ? start : active[$2])
        duration_h = ($1 - begin) / 3600.0
        if (duration_h > 0) total += duration_h * rate[$2]
        delete active[$2]
      }
    }
    END {
      # pods still active: count from their start (or day-start) to now
      now = systime()
      for (p in active) {
        begin = (active[p] < start ? start : active[p])
        duration_h = (now - begin) / 3600.0
        if (duration_h > 0) total += duration_h * rate[p]
      }
      printf "%.2f", (total ? total : 0)
    }
  ' "$SPEND_LOG" 2>/dev/null || echo "0.00"
}

spend_status() {
  local today
  today="$(compute_spend_today)"
  echo "Spend today: \$$today (budget: \$$DAILY_BUDGET)"
  echo "Log: $SPEND_LOG"
}

# One-shot health check; returns 0 if healthy, non-zero otherwise
health_check() {
  resolve_pod_id 2>/dev/null || { echo "no-pod"; return 1; }
  detect_runpod_cli

  local info status uptime
  info="$("$RUNPOD_BIN" pod get "$POD_ID" 2>/dev/null || true)"
  if [ -z "$info" ]; then echo "pod-gone"; return 1; fi
  status="$(printf "%s" "$info" | jq -r '.desiredStatus // "?"')"
  uptime="$(printf "%s" "$info" | jq -r '.uptimeSeconds // 0')"

  if [ "$status" != "RUNNING" ]; then echo "status=$status"; return 1; fi

  # Probe vLLM directly when tailnet access is enabled; otherwise probe the local tunnel URL.
  if direct_access_enabled; then
    if ! curl -fsS --max-time 5 "$(pod_access_url)/v1/models" >/dev/null 2>&1; then
      echo "vllm-unreachable (uptime=${uptime}s)"
      return 2
    fi
  else
    if ! curl -fsS --max-time 5 "http://localhost:$LOCAL_PORT/v1/models" >/dev/null 2>&1; then
      echo "vllm-unreachable (uptime=${uptime}s)"
      return 2
    fi
  fi

  echo "healthy uptime=${uptime}s"
  return 0
}

supervise_pod() {
  echo "Supervisor starting (poll=${SUPERVISOR_POLL_SECS}s budget=\$$DAILY_BUDGET/day)."
  echo "Ctrl+C to stop. Logs: $SUPERVISOR_LOG"
  notify "Supervisor" "Started — watching pod with \$$DAILY_BUDGET/day cap"

  local consecutive_fails=0
  local failures_this_hour=0
  local hour_window_start
  hour_window_start="$(date +%s)"

  # Cleanup on Ctrl+C
  trap 'notify "Supervisor" "Stopped by user"; exit 0' INT TERM

  while true; do
    local now spent
    now="$(date +%s)"

    # Reset hourly failure counter
    if [ $((now - hour_window_start)) -gt 3600 ]; then
      failures_this_hour=0
      hour_window_start="$now"
    fi

    # Budget check
    spent="$(compute_spend_today)"
    if awk -v s="$spent" -v b="$DAILY_BUDGET" 'BEGIN { exit !(s+0 >= b+0) }'; then
      notify "Supervisor" "BUDGET HIT (\$$spent >= \$$DAILY_BUDGET) — terminating pod"
      terminate_pod 2>/dev/null || true
      exit 3
    fi

    local status
    status="$(health_check)"
    local rc=$?

    if [ $rc -eq 0 ]; then
      consecutive_fails=0
      echo "[$(date +%H:%M:%S)] OK — $status (spend: \$$spent)" >> "$SUPERVISOR_LOG"
    else
      consecutive_fails=$((consecutive_fails + 1))
      failures_this_hour=$((failures_this_hour + 1))
      notify "Supervisor" "Health fail: $status (consec=$consecutive_fails, hourly=$failures_this_hour)"

      if [ "$failures_this_hour" -ge "$SUPERVISOR_MAX_FAILS_PER_HOUR" ]; then
        notify "Supervisor" "ABORT: $failures_this_hour failures in 1hr — stopping"
        terminate_pod 2>/dev/null || true
        exit 4
      fi

      # Only try to recreate on pod-gone / terminated states
      if echo "$status" | grep -qE "no-pod|pod-gone|status=EXITED|status=TERMINATED"; then
        local backoff
        case "$consecutive_fails" in
          1) backoff=30 ;;
          2) backoff=60 ;;
          *) backoff=120 ;;
        esac
        notify "Supervisor" "Pod gone — recreating in ${backoff}s"
        sleep "$backoff"

        # Stop stale tunnel
        kill_existing_tunnel
        record_spend_stop

        # Recreate
        if [ "$SPOT" = "1" ]; then
          create_spot_pod || { notify "Supervisor" "Recreate failed"; continue; }
        else
          notify "Supervisor" "Non-spot mode — manual recreate needed, pausing supervisor"
          exit 5
        fi
        sleep 15
        get_pod_endpoint 2>/dev/null || { notify "Supervisor" "Endpoint fetch failed"; continue; }
        wait_for_ssh 2>/dev/null || { notify "Supervisor" "SSH wait failed"; continue; }
        run_remote_setup
        if direct_access_enabled; then
          kill_existing_tunnel
        else
          open_tunnel_bg
        fi
        record_spend_start
        notify "Supervisor" "Pod back online"
        consecutive_fails=0
      elif echo "$status" | grep -q "vllm-unreachable"; then
        # vLLM is down but pod is alive — try restarting vLLM without recreating
        notify "Supervisor" "vLLM unreachable, restarting on pod"
        restart_vllm_on_pod 2>/dev/null || true
      fi
    fi

    sleep "$SUPERVISOR_POLL_SECS"
  done
}

case "${1:-}" in
  create) create_pod ;;
  ssh) ssh_into_pod ;;
  tunnel)
    resolve_pod_id
    get_pod_endpoint
    wait_for_ssh
    start_tunnel
    ;;
  terminate) terminate_pod ;;
  status) pod_status ;;
  health) health_check ;;
  spend) spend_status ;;
  logs) tail_vllm_logs ;;
  restart-vllm) restart_vllm_on_pod ;;
  supervise) supervise_pod ;;
  *) usage; exit 1 ;;
esac
