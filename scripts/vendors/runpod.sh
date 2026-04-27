#!/usr/bin/env bash
# vendors/runpod.sh — RunPod-specific functions and command entry points.
# Sourced by gpu.sh after gpu-common.sh. Requires SCRIPT_DIR to be set.

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

create_ondemand_pod_graphql() {
  # Creates an on-demand pod via GraphQL podFindAndDeployOnDemand.
  # The runpodctl CLI silently drops --network-volume-id for Secure pods,
  # so we go to the GraphQL API directly (same path create_spot_pod uses).
  # Args: $1 = gpu_type_id (RunPod-friendly name)
  # Returns 0 on success and sets POD_ID; non-zero on failure.
  local gpu_type_id="$1"
  local volume_field="" dc_field="" ports_field="8000/http,22/tcp"

  if [ -n "$CREATE_RUNPOD_VOLUME_ID" ]; then
    volume_field="networkVolumeId: \"$CREATE_RUNPOD_VOLUME_ID\","
  fi
  if [ -n "$CREATE_DATA_CENTER_IDS" ]; then
    dc_field="dataCenterId: \"$CREATE_DATA_CENTER_IDS\","
  fi
  if [ -n "${RUNPOD_PORTS:-}" ]; then
    ports_field="$RUNPOD_PORTS"
  fi

  local query
  query=$(cat <<EOF
mutation {
  podFindAndDeployOnDemand(input: {
    cloudType: $CREATE_CLOUD_TYPE,
    gpuCount: 1,
    volumeInGb: 0,
    containerDiskInGb: $CREATE_CONTAINER_DISK_GB,
    minVcpuCount: 2,
    minMemoryInGb: 15,
    gpuTypeId: "$gpu_type_id",
    name: "$CREATE_POD_NAME",
    imageName: "$CREATE_RUNPOD_IMAGE",
    dockerArgs: "",
    ports: "$ports_field",
    volumeMountPath: "/workspace",
    $volume_field
    $dc_field
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

  local response new_pod_id err_msg
  response="$(runpod_graphql "$query")"

  new_pod_id="$(printf "%s" "$response" | jq -r '.data.podFindAndDeployOnDemand.id // empty' 2>/dev/null)"
  if [ -n "$new_pod_id" ]; then
    POD_ID="$new_pod_id"
    return 0
  fi

  err_msg="$(printf "%s" "$response" | jq -r '.errors[0].message // empty' 2>/dev/null)"
  if [ -n "$err_msg" ]; then
    echo "  └─ $err_msg" >&2
  else
    printf "%s\n" "$response" >&2
  fi
  return 1
}

get_pod_endpoint() {
  # Polls until RunPod publishes the pod's SSH ip/port (up to ~3 min).
  # Newly-created pods report `{"ssh": {"error": "pod not ready"}}` for 30-90s
  # before the endpoint becomes available.
  local pod_line pod_info i
  detect_runpod_cli
  if [ "$RUNPOD_BIN" = "runpodctl" ]; then
    echo "Waiting for SSH endpoint to be published for pod $POD_ID..."
    POD_IP=""
    POD_SSH_PORT=""
    for i in $(seq 1 36); do
      pod_info="$("$RUNPOD_BIN" pod get "$POD_ID" --include-machine 2>/dev/null || true)"
      if [ -n "$pod_info" ]; then
        # RunPod REST returns SSH endpoint at .ssh.ip / .ssh.port when ready.
        read -r POD_IP POD_SSH_PORT < <(printf "%s" "$pod_info" | jq -r '(.ssh.ip // "") + " " + ((.ssh.port // "") | tostring)' 2>/dev/null)
        if [ "$POD_IP" = "null" ] || [ -z "$POD_IP" ]; then
          POD_IP=""
          POD_SSH_PORT=""
        fi
      fi
      if [ -n "$POD_IP" ] && [ -n "$POD_SSH_PORT" ]; then
        return 0
      fi
      sleep 5
    done
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
    echo "Failed to parse pod SSH endpoint for pod $POD_ID after 3 minutes." >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Command entry points: runpod_<command>
# ---------------------------------------------------------------------------

runpod_create() {
  local -a gpu_candidates fallback_gpus
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

  if [ -z "$CREATE_RUNPOD_VOLUME_ID" ] && [ "$VOLUME_NAME" != "none" ]; then
    echo "No network volume attached — model will re-download on every boot."
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

  echo "Creating on-demand pod via GraphQL (cloud=$CREATE_CLOUD_TYPE, dc=${CREATE_DATA_CENTER_IDS:-any}, volume=${CREATE_RUNPOD_VOLUME_ID:-none})..."
  POD_ID=""
  for candidate in "${gpu_candidates[@]}"; do
    echo "Trying GPU type: $candidate"
    if create_ondemand_pod_graphql "$candidate"; then
      break
    fi
    echo "Create failed for GPU type: $candidate"
  done

  if [ -z "$POD_ID" ]; then
    echo "Pod creation failed for all configured GPU options."
    echo "Set GPU_TYPE or GPU_FALLBACKS in profile/env to available SKUs."
    exit 1
  fi

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

runpod_ssh() {
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

runpod_tunnel() {
  resolve_pod_id
  get_pod_endpoint
  wait_for_ssh
  start_tunnel
}

runpod_terminate() {
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

runpod_status() {
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

runpod_health() {
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

runpod_spend() {
  spend_status
}

runpod_logs() {
  resolve_pod_id
  detect_runpod_cli
  get_pod_endpoint
  wait_for_ssh
  local log_file="/workspace/vllm-${CREATE_POD_NAME}.log"
  echo "Tailing $log_file (Ctrl+C to stop)..."
  ssh $SSH_OPTS -p "$POD_SSH_PORT" "root@$POD_IP" "test -f $log_file && tail -f $log_file || tail -f /workspace/vllm.log"
}

runpod_restart_vllm() {
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

runpod_supervise() {
  supervise_loop runpod_terminate "RunPod"
}
