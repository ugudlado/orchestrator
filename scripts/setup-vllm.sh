#!/usr/bin/env bash
# Install vLLM (if absent) and serve a model on the pod.
# Runs on the RunPod pod, invoked by gpu.sh create / restart-vllm.

set -euo pipefail

VLLM_MODEL="${VLLM_MODEL:-QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ}"
VLLM_MAX_LEN="${VLLM_MAX_LEN:-65536}"
VLLM_PORT="${VLLM_PORT:-8000}"
VOLUME_MOUNT_PATH="${VOLUME_MOUNT_PATH:-/workspace}"
VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.9}"
VLLM_TOOL_PARSER="${VLLM_TOOL_PARSER:-qwen3_coder}"
VLLM_REASONING_PARSER="${VLLM_REASONING_PARSER:-}"
# Free-form extra flags appended to vllm serve. Used for hardware-specific
# workarounds (e.g. --gdn-prefill-backend triton on CUDA<12.6 + Hopper/Ampere).
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"
TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
TAILSCALE_HOSTNAME="${TAILSCALE_HOSTNAME:-$(hostname -s 2>/dev/null || hostname)}"
# Per-pod files so multiple pods sharing this network volume don't overwrite each other.
VLLM_LOG="${VLLM_LOG:-$VOLUME_MOUNT_PATH/vllm-${TAILSCALE_HOSTNAME}.log}"
TAILSCALE_STATE_FILE="${TAILSCALE_STATE_FILE:-$VOLUME_MOUNT_PATH/tailscale-${TAILSCALE_HOSTNAME}.state}"
TAILSCALE_LOG="${TAILSCALE_LOG:-$VOLUME_MOUNT_PATH/tailscaled-${TAILSCALE_HOSTNAME}.log}"
VLLM_BIND_HOST="${VLLM_BIND_HOST:-127.0.0.1}"
VLLM_PROBE_HOST="${VLLM_PROBE_HOST:-127.0.0.1}"

verify_volume_mount() {
  if awk -v p="$VOLUME_MOUNT_PATH" '$2 == p {found=1} END {exit !found}' /proc/mounts; then
    echo "Verified $VOLUME_MOUNT_PATH is mounted."
  else
    echo "WARNING: $VOLUME_MOUNT_PATH is not a mounted filesystem."
    echo "Running on ephemeral disk — caches will not persist across pod recreates."
    mkdir -p "$VOLUME_MOUNT_PATH"
  fi
}

ensure_cache_dirs() {
  mkdir -p \
    "$VOLUME_MOUNT_PATH/hf_cache" \
    "$VOLUME_MOUNT_PATH/triton_cache" \
    "$VOLUME_MOUNT_PATH/torch_compile_cache" \
    "$VOLUME_MOUNT_PATH/vllm_cache" \
    "$VOLUME_MOUNT_PATH/tmp"
}

install_tailscale_if_missing() {
  if command -v tailscale >/dev/null 2>&1 && command -v tailscaled >/dev/null 2>&1; then
    return 0
  fi

  echo "Installing Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
}

start_tailscale_if_configured() {
  if [ -z "$TAILSCALE_AUTHKEY" ]; then
    echo "Tailscale auth key not set — using localhost bind."
    return 0
  fi

  if [ -z "$TAILSCALE_HOSTNAME" ]; then
    TAILSCALE_HOSTNAME="$(hostname -s 2>/dev/null || hostname)"
  fi

  install_tailscale_if_missing
  mkdir -p "$(dirname "$TAILSCALE_STATE_FILE")"

  if ! tailscale status >/dev/null 2>&1; then
    local tailscaled_bin
    tailscaled_bin="$(command -v tailscaled 2>/dev/null || true)"
    if [ -z "$tailscaled_bin" ] && [ -x /usr/sbin/tailscaled ]; then
      tailscaled_bin="/usr/sbin/tailscaled"
    fi
    if [ -z "$tailscaled_bin" ]; then
      echo "ERROR: tailscaled binary not found after install."
      return 1
    fi

    echo "Starting tailscaled (userspace networking)..."
    nohup "$tailscaled_bin" --tun=userspace-networking --state="$TAILSCALE_STATE_FILE" > "$TAILSCALE_LOG" 2>&1 &
    sleep 5
  fi

  echo "Joining Tailscale as $TAILSCALE_HOSTNAME..."
  tailscale up \
    --authkey="$TAILSCALE_AUTHKEY" \
    --hostname="$TAILSCALE_HOSTNAME" \
    --ssh

  VLLM_BIND_HOST="0.0.0.0"
  echo "Tailscale ready: http://$TAILSCALE_HOSTNAME:$VLLM_PORT"
}

install_vllm_if_missing() {
  if python3 -c "import vllm" 2>/dev/null; then
    echo "vLLM already installed ($(python3 -c 'import vllm; print(vllm.__version__)'))"
    return
  fi
  echo "Installing vLLM (this takes a few minutes)..."
  pip install --upgrade --timeout 120 --retries 5 'vllm>=0.15.0' 2>&1 | tail -5
}

stop_existing_vllm() {
  local killed=0
  if pgrep -f 'vllm serve' >/dev/null 2>&1; then
    pkill -9 -f 'vllm serve' 2>/dev/null || true
    killed=1
  fi
  # EngineCore subprocess holds VRAM and uses a different process name
  # that doesn't match 'vllm' — must match "VLLM::EngineCore" explicitly.
  if pgrep -f 'EngineCore|VLLM::' >/dev/null 2>&1; then
    pkill -9 -f 'EngineCore' 2>/dev/null || true
    pkill -9 -f 'VLLM::' 2>/dev/null || true
    killed=1
  fi
  if [ "$killed" = "1" ]; then
    echo "Stopped existing vLLM/EngineCore processes. Waiting for CUDA to release..."
    sleep 8
    # Verify GPU is actually free before proceeding
    local free_mb
    free_mb=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader | head -1 | awk '{print $1}')
    echo "VRAM free: ${free_mb} MiB"
  fi
}

serve_model() {
  echo "Starting vLLM: model=$VLLM_MODEL max_len=$VLLM_MAX_LEN port=$VLLM_PORT"

  local extra_args=()
  if [ -n "$VLLM_TOOL_PARSER" ]; then
    extra_args+=(--enable-auto-tool-choice --tool-call-parser "$VLLM_TOOL_PARSER")
  fi
  if [ -n "$VLLM_REASONING_PARSER" ]; then
    extra_args+=(--enable-reasoning --reasoning-parser "$VLLM_REASONING_PARSER")
  fi
  if [ -n "$VLLM_EXTRA_ARGS" ]; then
    # Word-split intentional — VLLM_EXTRA_ARGS holds CLI flags.
    # shellcheck disable=SC2206
    extra_args+=($VLLM_EXTRA_ARGS)
  fi
  echo "  extra flags: ${extra_args[*]:-none}"

  cd /root
  HF_HUB_DISABLE_XET=1 \
  HF_HOME="$VOLUME_MOUNT_PATH/hf_cache" \
  HUGGINGFACE_HUB_CACHE="$VOLUME_MOUNT_PATH/hf_cache/hub" \
  TRITON_CACHE_DIR="$VOLUME_MOUNT_PATH/triton_cache" \
  TMPDIR="$VOLUME_MOUNT_PATH/tmp" \
  TORCHINDUCTOR_CACHE_DIR="$VOLUME_MOUNT_PATH/torch_compile_cache" \
  VLLM_CACHE_ROOT="$VOLUME_MOUNT_PATH/vllm_cache" \
  nohup vllm serve "$VLLM_MODEL" \
    --max-model-len "$VLLM_MAX_LEN" \
    --gpu-memory-utilization "$VLLM_GPU_UTIL" \
    "${extra_args[@]}" \
    --host "$VLLM_BIND_HOST" \
    --port "$VLLM_PORT" \
    > "$VLLM_LOG" 2>&1 &
  disown
  echo "vLLM started (pid $!). Log: $VLLM_LOG"
}

wait_until_serving() {
  echo "Waiting for vLLM to serve (first start may take ~3-10 min for model download + compile)..."
  local i max=60  # up to 30 min (60 * 30s)
  for i in $(seq 1 $max); do
    if curl -fsS --max-time 3 "http://$VLLM_PROBE_HOST:$VLLM_PORT/v1/models" >/dev/null 2>&1; then
      echo "vLLM serving on port $VLLM_PORT."
      curl -s "http://$VLLM_PROBE_HOST:$VLLM_PORT/v1/models" | head -c 300
      echo ""
      return 0
    fi
    if ! pgrep -f 'vllm serve' >/dev/null 2>&1; then
      echo "ERROR: vLLM process died during startup. Last log lines:"
      tail -30 "$VLLM_LOG"
      return 1
    fi
    local t=$((i * 30))
    local last
    last="$(tail -1 "$VLLM_LOG" 2>/dev/null | head -c 120)"
    echo "  [${t}s] still starting — $last"
    sleep 30
  done
  echo "ERROR: vLLM did not start within 30 minutes. Check $VLLM_LOG."
  return 1
}

main() {
  # --bench-only: print hardware info and exit (skip vLLM serve).
  if [ "${1:-}" = "--bench-only" ]; then
    echo "=== GPU hardware info ==="
    nvidia-smi
    echo "=== Bench-only mode — exiting ==="
    exit 0
  fi

  echo "=== vLLM setup ==="
  verify_volume_mount
  ensure_cache_dirs
  start_tailscale_if_configured
  install_vllm_if_missing
  stop_existing_vllm
  serve_model
  wait_until_serving
  echo "=== Setup complete ==="
}

main "$@"
