#!/usr/bin/env bash

set -euo pipefail

MODEL_TO_PULL="${MODEL_TO_PULL:-qwen2.5-coder:32b-instruct-q6_K}"
VOLUME_MOUNT_PATH="${VOLUME_MOUNT_PATH:-/workspace}"

# Auto-detect volume mount — if /workspace is a real mount, use it; else ephemeral.
if awk -v p="$VOLUME_MOUNT_PATH" '$2 == p {found=1} END {exit !found}' /proc/mounts 2>/dev/null; then
  OLLAMA_MODELS_DIR="${OLLAMA_MODELS_DIR:-$VOLUME_MOUNT_PATH/ollama}"
  OLLAMA_LOG="${OLLAMA_LOG:-$VOLUME_MOUNT_PATH/ollama.log}"
  VOLUME_ATTACHED=1
else
  OLLAMA_MODELS_DIR="${OLLAMA_MODELS_DIR:-/root/.ollama/models}"
  OLLAMA_LOG="${OLLAMA_LOG:-/root/ollama.log}"
  VOLUME_ATTACHED=0
fi

REQUIRE_NETWORK_VOLUME="${REQUIRE_NETWORK_VOLUME:-0}"

verify_network_volume_mount() {
  if [ "$REQUIRE_NETWORK_VOLUME" != "1" ]; then
    echo "Skipping network volume check (REQUIRE_NETWORK_VOLUME=$REQUIRE_NETWORK_VOLUME)."
    return
  fi

  if ! awk -v p="$VOLUME_MOUNT_PATH" '$2 == p {found=1} END {exit !found}' /proc/mounts; then
    echo "ERROR: $VOLUME_MOUNT_PATH is not a mounted filesystem — network volume failed to attach."
    echo "Aborting to avoid writing $OLLAMA_MODELS_DIR to ephemeral container disk."
    echo "Check pod creation: ensure --network-volume-id matched a volume in the pod's data center."
    echo "To bypass for testing, re-run with REQUIRE_NETWORK_VOLUME=0."
    exit 1
  fi

  echo "Verified $VOLUME_MOUNT_PATH is a mounted network volume."
}

add_export_if_missing() {
  local line="$1"
  local file="$2"
  if [ ! -f "$file" ] || ! rg -Fq "$line" "$file"; then
    printf '%s\n' "$line" >> "$file"
  fi
}

echo "Starting Ollama setup for RunPod..."

echo "Installing dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y zstd curl git ca-certificates

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "Ollama already installed."
fi

echo "Configuring persistent model storage..."
verify_network_volume_mount
mkdir -p "$OLLAMA_MODELS_DIR"
export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"

add_export_if_missing "export OLLAMA_MODELS=$OLLAMA_MODELS_DIR" "$HOME/.bashrc"
add_export_if_missing "export OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL" "$HOME/.bashrc"
add_export_if_missing "export OLLAMA_MAX_LOADED_MODELS=$OLLAMA_MAX_LOADED_MODELS" "$HOME/.bashrc"
add_export_if_missing "export OLLAMA_KEEP_ALIVE=$OLLAMA_KEEP_ALIVE" "$HOME/.bashrc"

echo "Starting Ollama server..."
if pgrep -x ollama >/dev/null 2>&1; then
  pkill -x ollama || true
  sleep 1
fi

nohup bash -lc "OLLAMA_HOST=0.0.0.0 OLLAMA_MODELS='$OLLAMA_MODELS' OLLAMA_NUM_PARALLEL='$OLLAMA_NUM_PARALLEL' OLLAMA_MAX_LOADED_MODELS='$OLLAMA_MAX_LOADED_MODELS' OLLAMA_KEEP_ALIVE='$OLLAMA_KEEP_ALIVE' ollama serve" > "$OLLAMA_LOG" 2>&1 &

echo "Waiting for Ollama health check..."
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  echo "Ollama failed to start. Check logs at $OLLAMA_LOG"
  exit 1
fi

if [ "$MODEL_TO_PULL" != "none" ]; then
  echo "Pulling model: $MODEL_TO_PULL"
  ollama pull "$MODEL_TO_PULL"
else
  echo "Skipping model pull because MODEL_TO_PULL=none"
fi

echo "Setup complete."
echo "Ollama is running at http://localhost:11434"
