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
VLLM_LOG="${VLLM_LOG:-$VOLUME_MOUNT_PATH/vllm.log}"

verify_volume_mount() {
  if ! awk -v p="$VOLUME_MOUNT_PATH" '$2 == p {found=1} END {exit !found}' /proc/mounts; then
    echo "ERROR: $VOLUME_MOUNT_PATH is not a mounted filesystem."
    echo "All caches (model, triton, torch) need to live on the network volume"
    echo "to avoid filling the 30GB ephemeral disk. Aborting."
    exit 1
  fi
  echo "Verified $VOLUME_MOUNT_PATH is mounted."
}

ensure_cache_dirs() {
  mkdir -p \
    "$VOLUME_MOUNT_PATH/hf_cache" \
    "$VOLUME_MOUNT_PATH/triton_cache" \
    "$VOLUME_MOUNT_PATH/torch_compile_cache" \
    "$VOLUME_MOUNT_PATH/vllm_cache" \
    "$VOLUME_MOUNT_PATH/tmp"
}

install_vllm_if_missing() {
  if python3 -c "import vllm" 2>/dev/null; then
    echo "vLLM already installed ($(python3 -c 'import vllm; print(vllm.__version__)'))"
    return
  fi
  echo "Installing vLLM (this takes a few minutes)..."
  pip install --upgrade 'vllm>=0.15.0' 2>&1 | tail -5
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
    --enable-auto-tool-choice \
    --tool-call-parser "$VLLM_TOOL_PARSER" \
    --host 127.0.0.1 \
    --port "$VLLM_PORT" \
    > "$VLLM_LOG" 2>&1 &
  disown
  echo "vLLM started (pid $!). Log: $VLLM_LOG"
}

wait_until_serving() {
  echo "Waiting for vLLM to serve (first start may take ~3-10 min for model download + compile)..."
  local i max=60  # up to 30 min (60 * 30s)
  for i in $(seq 1 $max); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$VLLM_PORT/v1/models" >/dev/null 2>&1; then
      echo "vLLM serving on port $VLLM_PORT."
      curl -s "http://127.0.0.1:$VLLM_PORT/v1/models" | head -c 300
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
  echo "=== vLLM setup ==="
  verify_volume_mount
  ensure_cache_dirs
  install_vllm_if_missing
  stop_existing_vllm
  serve_model
  wait_until_serving
  echo "=== Setup complete ==="
}

main "$@"
