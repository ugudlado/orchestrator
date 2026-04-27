#!/usr/bin/env bash
# vendors/vast.sh — Vast.ai-specific functions and command entry points.
# Sourced by gpu.sh after gpu-common.sh. Requires SCRIPT_DIR to be set.

# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

detect_vast_cli() {
  # Add ~/.local/bin so a freshly pip-installed vastai is found immediately.
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
  esac

  if command -v vastai >/dev/null 2>&1; then
    return 0
  fi

  echo "vastai CLI not found. Attempting pip install --user vastai..."
  if ! pip install --user vastai --quiet; then
    echo "ERROR: pip install vastai failed. Install manually and retry." >&2
    exit 1
  fi

  # Reload PATH entry we prepended above and check again.
  if ! command -v vastai >/dev/null 2>&1; then
    echo "ERROR: vastai not found on PATH after install." >&2
    echo "       Try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
    exit 1
  fi
  echo "vastai installed."
}

verify_vast_api_key() {
  # If VAST_API_KEY env var is set, write it to the CLI config so vastai commands work.
  if [ -n "${VAST_API_KEY:-}" ]; then
    vastai set api-key "$VAST_API_KEY" >/dev/null 2>&1 || true
  fi

  # Validate that a key is configured (either written above or already in ~/.vast_api_key).
  if ! vastai show user >/dev/null 2>&1; then
    echo "ERROR: Vast.ai API key not configured or invalid." >&2
    echo "       Set VAST_API_KEY in $ENV_FILE, or run:" >&2
    echo "         vastai set api-key <KEY>" >&2
    echo "       Get your key at: https://cloud.vast.ai/account/" >&2
    exit 1
  fi
}

# Ensure local SSH pubkey is registered with the Vast account so created
# instances accept our identity. Idempotent — Vast returns success even if the
# key already exists. Defaults to ~/.ssh/id_ed25519.pub; override with VAST_SSH_PUBKEY.
ensure_vast_ssh_key() {
  local pubkey_file="${VAST_SSH_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
  if [ ! -r "$pubkey_file" ]; then
    echo "ERROR: SSH pubkey not found at $pubkey_file" >&2
    echo "       Set VAST_SSH_PUBKEY in $ENV_FILE to override." >&2
    exit 1
  fi
  local pubkey
  pubkey="$(cat "$pubkey_file")"
  # Check if already registered to avoid noisy duplicate-key API calls.
  if vastai show ssh-keys 2>/dev/null | grep -qF "$pubkey"; then
    return 0
  fi
  echo "Registering local SSH pubkey with Vast account ($pubkey_file)..."
  vastai create ssh-key "$pubkey" >/dev/null 2>&1 || true
}

# Poll until instance reaches actual_status == "running" and ssh_host/ssh_port are populated.
# Timeout: 5 minutes (60 × 5s). Sets VAST_SSH_HOST, VAST_SSH_PORT, VAST_INSTANCE_ID on success.
wait_for_vast_running() {
  local instance_id="$1"
  local i
  # 15 min timeout — fresh hosts pull 12GB Docker image before reaching running.
  local max_iters=180
  echo "Waiting for Vast instance $instance_id to reach running state (up to 15 min)..."
  for i in $(seq 1 $max_iters); do
    local info ssh_host ssh_port actual_status
    info="$(vastai show instances --raw 2>/dev/null || true)"
    if [ -z "$info" ]; then
      sleep 5
      continue
    fi
    actual_status="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('actual_status',''))
        break
" 2>/dev/null || true)"
    ssh_host="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('ssh_host') or inst.get('public_ipaddr') or '')
        break
" 2>/dev/null || true)"
    ssh_port="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('ssh_port') or '')
        break
" 2>/dev/null || true)"

    if [ "$actual_status" = "running" ] && [ -n "$ssh_host" ] && [ -n "$ssh_port" ]; then
      VAST_SSH_HOST="$ssh_host"
      VAST_SSH_PORT="$ssh_port"
      echo "Instance running: $VAST_SSH_HOST:$VAST_SSH_PORT"
      return 0
    fi

    # Early-fail if Vast reports a hard error (manifest unknown, OOM, etc.)
    local status_msg
    status_msg="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('status_msg','') or '')
        break
" 2>/dev/null || true)"
    if printf '%s' "$status_msg" | grep -qiE "manifest unknown|not found|error response from daemon|no space left|invalid image"; then
      echo "ERROR: Vast instance $instance_id failed: $status_msg" >&2
      return 1
    fi

    echo "  [${i}×5s] status=${actual_status:-unknown} ssh=${ssh_host:-?}:${ssh_port:-?}"
    sleep 5
  done

  echo "ERROR: Vast instance $instance_id did not reach running state within 15 minutes." >&2
  return 1
}

# Copy setup-vllm.sh to pod and kick it off under nohup so SSH disconnect doesn't kill it.
vast_run_setup() {
  local ssh_host="$1"
  local ssh_port="$2"
  local setup_dest="/workspace/setup-vllm.sh"
  local run_log="/workspace/run-setup.log"
  local wrapper="/workspace/run-setup.sh"
  local remote_env

  # Ensure mount path exists — `cuda-12.8.1-auto` template ships without /workspace.
  ssh $SSH_OPTS -p "$ssh_port" "root@$ssh_host" "mkdir -p /workspace"

  echo "Copying setup script to pod ($setup_dest)..."
  scp $SSH_OPTS -P "$ssh_port" "$SCRIPT_DIR/setup-vllm.sh" "root@$ssh_host:$setup_dest"

  # Build env-export wrapper that mirrors the manual spike's run-setup.sh.
  # build_remote_env_prefix returns "VAR=val VAR2=val2 " — used as env prefix before exec.
  remote_env="$(build_remote_env_prefix)"
  # Always prepend VOLUME_MOUNT_PATH for Vast (overlay-root; /workspace is a dir on /).
  local wrapper_content
  wrapper_content="$(cat <<EOF
#!/usr/bin/env bash
set -a
VOLUME_MOUNT_PATH=/workspace
set +a
exec ${remote_env}bash $setup_dest
EOF
)"

  echo "Writing wrapper script on pod ($wrapper)..."
  ssh $SSH_OPTS -p "$ssh_port" "root@$ssh_host" \
    "cat > $wrapper && chmod +x $wrapper" <<< "$wrapper_content"

  echo "Launching vLLM setup under nohup (model=$VLLM_MODEL)..."
  ssh $SSH_OPTS -p "$ssh_port" "root@$ssh_host" \
    "nohup bash $wrapper > $run_log 2>&1 &"

  echo "Setup launched. Tailing $run_log for 15 seconds to confirm startup..."
  sleep 2
  ssh $SSH_OPTS -p "$ssh_port" "root@$ssh_host" \
    "timeout 13 tail -f $run_log 2>/dev/null || tail -n 20 $run_log 2>/dev/null" || true
  echo ""
  echo "Setup is running in background. Poll readiness with:"
  echo "  ssh -p $ssh_port root@$ssh_host 'curl -fsS http://localhost:$REMOTE_PORT/v1/models'"
  echo "Or tail the log:"
  echo "  gpu.sh vast logs"
}

# ---------------------------------------------------------------------------
# Cleanup trap helper — only called on error during create flow.
# ---------------------------------------------------------------------------
_vast_create_cleanup() {
  local instance_id="$1"
  if [ -n "$instance_id" ]; then
    echo "ERROR during create — destroying instance $instance_id to avoid runaway billing..." >&2
    yes | vastai destroy instance "$instance_id" >/dev/null 2>&1 || true
  fi
}

# ---------------------------------------------------------------------------
# Command entry points: vast_<command>
# ---------------------------------------------------------------------------

vast_create() {
  detect_vast_cli
  verify_vast_api_key
  ensure_vast_ssh_key

  if [ -z "${VAST_OFFER_ID:-}" ]; then
    echo "ERROR: VAST_OFFER_ID is not set." >&2
    echo "       Find an offer at https://cloud.vast.ai/create/ (filter A100 SXM4 80GB)," >&2
    echo "       copy the offer ID, and set VAST_OFFER_ID in your profile or env." >&2
    exit 1
  fi

  local vast_image="${VAST_IMAGE:-vastai/pytorch:2.7.0-py3.11-cuda12.8.1-devel}"
  local vast_disk="${VAST_DISK_GB:-100}"

  local create_args=(
    create instance "$VAST_OFFER_ID"
    --image "$vast_image"
    --disk "$vast_disk"
    --ssh
    --raw
  )

  if [ "${SPOT:-0}" = "1" ]; then
    # Bid at 80% of on-demand price. User must set VAST_BID_PRICE or we calculate
    # from the profile's on-demand price if available.
    local bid_price="${VAST_BID_PRICE:-}"
    if [ -z "$bid_price" ]; then
      # Default fallback bid — user should set VAST_BID_PRICE in profile for accuracy.
      echo "WARNING: SPOT=1 but VAST_BID_PRICE not set. Using \$0.72 as default bid price." >&2
      bid_price="0.72"
    fi
    create_args+=(--bid-price "$bid_price")
    echo "Creating SPOT instance (bid=\$$bid_price/hr): offer=$VAST_OFFER_ID image=$vast_image disk=${vast_disk}GB"
  else
    echo "Creating on-demand instance: offer=$VAST_OFFER_ID image=$vast_image disk=${vast_disk}GB"
  fi

  local create_output create_rc
  create_output="$(vastai "${create_args[@]}" 2>&1)" && create_rc=0 || create_rc=$?
  if [ $create_rc -ne 0 ]; then
    echo "ERROR: vastai create instance failed (exit $create_rc):" >&2
    printf '%s\n' "$create_output" >&2
    exit 1
  fi

  # Parse instance ID from JSON. Vast returns {"new_contract": <int>, "success": true}
  # or similar. Try .new_contract first, then .id, then .contract_id.
  local instance_id
  instance_id="$(printf '%s' "$create_output" | python3 -c "
import json, sys
raw = sys.stdin.read().strip()
try:
    data = json.loads(raw)
except Exception:
    sys.exit(1)
for key in ('new_contract', 'id', 'contract_id'):
    v = data.get(key)
    if v is not None:
        print(int(v))
        sys.exit(0)
sys.exit(1)
" 2>/dev/null || true)"

  if [ -z "$instance_id" ]; then
    echo "ERROR: Could not parse instance ID from create response:" >&2
    printf '%s\n' "$create_output" >&2
    exit 1
  fi

  echo "Created Vast instance: $instance_id"
  save_pod_id "$instance_id"

  # Cleanup trap: EXIT fires on ANY exit path (including SIGPIPE, set -e abort,
  # uncaught signal). Idempotent — _vast_create_cleanup is a no-op if the create
  # flow completed (CREATE_DONE=1). Avoids leaking instances at $0.60+/hr.
  CREATE_DONE=0
  # Bake instance_id into trap strings at definition time. Using $instance_id
  # by reference would NameError under `set -u` if local goes out of scope.
  trap "[ \"\$CREATE_DONE\" = \"1\" ] || _vast_create_cleanup $instance_id" EXIT
  trap "_vast_create_cleanup $instance_id; exit 130" INT TERM

  # `vastai create instance` provisions but does NOT auto-start. Without this,
  # the instance sits in intended_status=stopped forever and our wait loop
  # times out. (Discovered 2026-04-26 — undocumented in Vast CLI help.)
  echo "Starting instance $instance_id..."
  vastai start instance "$instance_id" >/dev/null 2>&1 || {
    echo "WARNING: vastai start instance returned non-zero — proceeding anyway." >&2
  }

  wait_for_vast_running "$instance_id"

  # Set common POD_IP / POD_SSH_PORT for wait_for_ssh (from gpu-common.sh).
  POD_IP="$VAST_SSH_HOST"
  POD_SSH_PORT="$VAST_SSH_PORT"
  wait_for_ssh

  vast_run_setup "$VAST_SSH_HOST" "$VAST_SSH_PORT"

  # Record spend start for the supervisor's budget/hours math.
  # Rate: bid price if SPOT, else dph_total from the running instance.
  local rate
  if [ "${SPOT:-0}" = "1" ]; then
    rate="${VAST_BID_PRICE:-0.72}"
  else
    rate="$(vastai show instance "$instance_id" --raw 2>/dev/null \
      | python3 -c "import json,sys
try: print(json.loads(sys.stdin.read()).get('dph_total',''))
except Exception: pass" 2>/dev/null || true)"
    [ -z "$rate" ] && rate="0.60"  # conservative fallback
  fi
  POD_ID="$instance_id" record_spend_start "$rate"

  # Mark create flow done — EXIT trap will see this and skip destroy.
  CREATE_DONE=1
  trap - EXIT INT TERM

  echo ""
  if direct_access_enabled; then
    echo "Vast pod ready. vLLM starting on http://$(effective_tailnet_hostname):$REMOTE_PORT"
    echo "   Tailscale direct access will be available once setup completes (~8-12 min)."
  else
    echo "Vast pod ready. Once vLLM is up, open a tunnel with:"
    echo "  gpu.sh vast tunnel   (not yet implemented — use ssh -L manually)"
  fi
  echo ""
  echo "Common commands:"
  echo "  gpu.sh vast logs        # tail vLLM log"
  echo "  gpu.sh vast ssh         # shell on pod"
  echo "  gpu.sh vast status      # pod state + vLLM health"
  echo "  gpu.sh vast terminate   # delete pod"
}

vast_terminate() {
  resolve_pod_id
  local instance_id="$POD_ID"
  echo "Terminating Vast instance $instance_id..."

  local out rc
  out="$(yes | vastai destroy instance "$instance_id" 2>&1)" && rc=0 || rc=$?

  if [ $rc -ne 0 ]; then
    # 404 / not found = already gone; treat as success.
    if printf '%s' "$out" | grep -qiE "not found|404|invalid contract|already|error"; then
      echo "Instance $instance_id not found (already terminated or ID invalid) — cleaning state."
    else
      echo "ERROR: vastai destroy failed (exit $rc):" >&2
      printf '%s\n' "$out" >&2
      # Still clean the state file so we don't get stuck.
    fi
  else
    echo "Instance $instance_id terminated."
  fi

  record_spend_stop "$instance_id"

  rm -f "$LAST_POD_FILE"
  echo "State file cleared."
}

vast_status() {
  resolve_pod_id
  local instance_id="$POD_ID"
  echo "Vast instance: $instance_id"

  detect_vast_cli

  local info
  info="$(vastai show instances --raw 2>/dev/null || true)"
  if [ -z "$info" ]; then
    echo "Could not fetch instance list from Vast API."
  else
    printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        keys = ['actual_status','ssh_host','ssh_port','gpu_name','num_gpus','dph_total','label']
        row = {k: inst.get(k,'') for k in keys}
        for k, v in row.items():
            print(f'  {k}: {v}')
        break
else:
    print('  instance not found in your list (may have been terminated)')
" 2>/dev/null || echo "  (could not parse instance info)"
  fi

  echo ""
  echo "vLLM health:"
  if direct_access_enabled; then
    local url
    url="$(pod_access_url)/v1/models"
    if curl -fsS --max-time 3 "$url" 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
models = d.get('data', [])
if models:
    print('  model:', models[0].get('id','?'))
else:
    print('  (no models listed)')
" 2>/dev/null; then
      :
    else
      echo "  unreachable on $(pod_access_url)"
    fi
  else
    if curl -fsS --max-time 3 "http://localhost:$LOCAL_PORT/v1/models" 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
models = d.get('data', [])
if models:
    print('  model:', models[0].get('id','?'))
else:
    print('  (no models listed)')
" 2>/dev/null; then
      :
    else
      echo "  unreachable on localhost:$LOCAL_PORT (open tunnel with: ssh -L)"
    fi
  fi
}

vast_logs() {
  resolve_pod_id
  detect_vast_cli

  # Resolve SSH coordinates from the running instance.
  local instance_id="$POD_ID"
  local info ssh_host ssh_port
  info="$(vastai show instances --raw 2>/dev/null || true)"
  ssh_host="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('ssh_host') or inst.get('public_ipaddr') or '')
        break
" 2>/dev/null || true)"
  ssh_port="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('ssh_port') or '')
        break
" 2>/dev/null || true)"

  if [ -z "$ssh_host" ] || [ -z "$ssh_port" ]; then
    echo "ERROR: Could not get SSH coordinates for instance $instance_id." >&2
    echo "       Is the instance running? Check: vastai show instances" >&2
    exit 1
  fi

  POD_IP="$ssh_host"
  POD_SSH_PORT="$ssh_port"
  wait_for_ssh

  local log_file="/workspace/vllm-$(effective_tailnet_hostname).log"
  echo "Tailing $log_file (Ctrl+C to stop)..."
  ssh $SSH_OPTS -p "$ssh_port" "root@$ssh_host" \
    "test -f $log_file && tail -f $log_file || tail -f /workspace/vllm.log"
}

vast_ssh() {
  resolve_pod_id
  detect_vast_cli

  local instance_id="$POD_ID"
  local info ssh_host ssh_port
  info="$(vastai show instances --raw 2>/dev/null || true)"
  ssh_host="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('ssh_host') or inst.get('public_ipaddr') or '')
        break
" 2>/dev/null || true)"
  ssh_port="$(printf '%s' "$info" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for inst in data:
    if str(inst.get('id','')) == '$instance_id':
        print(inst.get('ssh_port') or '')
        break
" 2>/dev/null || true)"

  if [ -z "$ssh_host" ] || [ -z "$ssh_port" ]; then
    echo "ERROR: Could not get SSH coordinates for instance $instance_id." >&2
    echo "       Is the instance running? Check: vastai show instances" >&2
    exit 1
  fi

  POD_IP="$ssh_host"
  POD_SSH_PORT="$ssh_port"
  wait_for_ssh
  echo "Connecting to Vast instance: root@$ssh_host:$ssh_port"
  ssh $SSH_OPTS -p "$ssh_port" "root@$ssh_host"
}

# ---------------------------------------------------------------------------
# vast search — find rentable offers matching profile constraints
# ---------------------------------------------------------------------------
# Vast CLI's --raw search filter is unreliable for `gpu_ram>=N` (silently drops
# matches; returns 0 when 13+ exist). Workaround: fetch the unfiltered top
# offers and filter in Python locally.  Discovered 2026-04-26.
#
# Honors profile env: VAST_GPU_NAME (e.g. "A100_SXM4"), VAST_MIN_VRAM_MB
# (default 80000), VAST_VERIFIED_ONLY (default 1).
#
# Usage: gpu.sh vast search          # uses profile defaults
#        VAST_GPU_NAME=H100 ... vast search

vast_search() {
  detect_vast_cli
  verify_vast_api_key

  local gpu_name="${VAST_GPU_NAME:-A100_SXM4}"
  local min_vram="${VAST_MIN_VRAM_MB:-80000}"
  local verified="${VAST_VERIFIED_ONLY:-1}"
  local cli_filter="num_gpus=1 rentable=true"
  [ "$verified" = "1" ] && cli_filter="$cli_filter verified=true"

  echo "Searching Vast for: gpu_name~='$gpu_name' min_vram=${min_vram}MB verified=$verified"
  local tmp
  tmp="$(mktemp)"
  vastai search offers "$cli_filter" --order "dph_total" --raw > "$tmp" 2>&1 || {
    echo "ERROR: vastai search failed:" >&2
    cat "$tmp" >&2
    rm -f "$tmp"
    exit 1
  }

  python3 - "$tmp" "$gpu_name" "$min_vram" <<'PY'
import json, sys
path, want_gpu, min_vram = sys.argv[1], sys.argv[2], int(sys.argv[3])
data = json.load(open(path))
# Vast normalizes gpu_name as "A100 SXM4" (space, no underscore). Match either form.
def matches_gpu(name, want):
    if not want or not name: return True
    n = name.replace(' ', '_').upper()
    w = want.replace(' ', '_').upper()
    return w in n
results = [
    o for o in data
    if (o.get('gpu_ram') or 0) >= min_vram
    and matches_gpu(o.get('gpu_name', ''), want_gpu)
]
results.sort(key=lambda o: o.get('dph_total', 999))
print(f"\nFound {len(results)} matching offers (sorted by $/hr):\n")
print(f"  {'OFFER_ID':<10} {'$/hr':>7}  {'GPU':<22} {'VRAM':>7} {'CUDA':>5}  {'GEO':<24}")
print(f"  {'-'*10} {'-'*7}  {'-'*22} {'-'*7} {'-'*5}  {'-'*24}")
for o in results[:15]:
    gpu = (o.get('gpu_name','') or '')[:22]
    vram = f"{o.get('gpu_ram',0)//1024}GB"
    cuda = str(o.get('cuda_max_good','?'))[:5]
    geo = (o.get('geolocation','?') or '?')[:24]
    print(f"  {o.get('id'):<10} ${o.get('dph_total',0):.3f}  {gpu:<22} {vram:>7} {cuda:>5}  {geo:<24}")
print(f"\nUse: VAST_OFFER_ID=<id> in your profile, or pass inline.")
PY
  rm -f "$tmp"
}

# ---------------------------------------------------------------------------
# Stub commands — not yet implemented
# ---------------------------------------------------------------------------

vast_spend() {
  local spent hours
  spent="$(compute_spend_today)"
  hours="$(compute_hours_today)"
  echo "Today: \$$spent across ${hours}h (budget=\$$DAILY_BUDGET, hours_cap=${DAILY_HOURS:-none})"
  echo "Log: $SPEND_LOG"
}

vast_supervise() {
  supervise_loop vast_terminate "Vast"
}

vast_health() {
  echo "vast health: not yet implemented (supervise mode does cost/hours only)" >&2
  exit 1
}

vast_restart_vllm() {
  echo "vast restart-vllm: not yet implemented" >&2
  exit 1
}

vast_tunnel() {
  echo "vast tunnel: not yet implemented" >&2
  exit 1
}
