# vLLM on H100/A100 — known issues and fixes

Captured 2026-04-26 while running Qwen3.6-27B-AWQ-INT4 spikes on RunPod's
`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` image.

---

## Issue 1 — Qwen3.x dies on first inference: flashinfer GDN kernel fails to JIT

### Symptoms

- vLLM starts cleanly, `/v1/models` responds 200
- First `/v1/chat/completions` request returns HTTP 500
- vLLM process dies immediately after
- Log shows: `7 errors detected in the compilation of "/root/.cache/flashinfer/.../gdn_prefill_kernel_*.cu"`
- Specific PTX errors: `namespace "cuda::ptx" has no member "n32_t"`,
  `fence_proxy_tensormap_generic`

### Root cause

flashinfer >= 0.6 ships PTX intrinsics that require **CUDA toolkit ≥ 12.6**.
RunPod's stock pytorch image has **12.4**. The GDN (Gated Delta Net) prefill
kernel JIT-compiles on first inference and fails.

Affects Qwen3.5 / Qwen3.6 family (uses GDN). Does NOT affect older Qwen3-Coder
or non-Qwen models that don't use GDN.

### Fix (current)

Add `--gdn-prefill-backend triton` to `vllm serve`. Triton path doesn't need
flashinfer's sm_90 PTX. ~10-15% slower than flashinfer would be, but works.

In our profiles this is set via:

```env
VLLM_EXTRA_ARGS="--gdn-prefill-backend triton"
```

setup-vllm.sh appends `$VLLM_EXTRA_ARGS` to the `vllm serve` command line.

### Fix (future)

Use a base image with CUDA 12.6+. RunPod has `runpod/pytorch:2.6.0-...-cuda12.8.1-...`
images that should let flashinfer JIT succeed and unlock another 10-20% speed.
Not done yet (would touch base image, increase risk surface).

### Don't bother trying

- `VLLM_ATTENTION_BACKEND=FLASH_ATTN` — doesn't help, GDN path is independent
  of attention backend.
- `pip uninstall flashinfer-python` — vLLM's GDN code-path *imports*
  flashinfer at runtime; uninstalling causes `ModuleNotFoundError`.
- vLLM PR #37507 (auto-detect CUDA<12.6 fallback) — was closed unmerged.

---

## Issue 2 — multiple pods on one network volume corrupt each other's tailnet identity

### Symptoms

- Two pods sharing `vllm-cache` network volume (different POD_NAMEs)
- Second pod's `tailscale up` succeeds and shows on `tailscale status`
- First pod **disappears** from tailnet — its hostname now resolves to second pod
- First pod's tailscaled is still alive but its identity got hijacked

### Root cause

setup-vllm.sh defaulted `TAILSCALE_STATE_FILE` to `/workspace/tailscale.state`.
Both pods wrote to the same file; second `tailscale up` overwrote the first
pod's identity in shared storage.

### Fix

setup-vllm.sh now defaults state and log files per-hostname:

```sh
TAILSCALE_STATE_FILE="${TAILSCALE_STATE_FILE:-$VOLUME_MOUNT_PATH/tailscale-${TAILSCALE_HOSTNAME}.state}"
TAILSCALE_LOG="${TAILSCALE_LOG:-$VOLUME_MOUNT_PATH/tailscaled-${TAILSCALE_HOSTNAME}.log}"
VLLM_LOG="${VLLM_LOG:-$VOLUME_MOUNT_PATH/vllm-${TAILSCALE_HOSTNAME}.log}"
```

Pods on the same volume now coexist cleanly. Re-running setup on an existing
pod with the new script also re-auths it under the per-host state file.

### Recovery (if it happens)

```sh
# On the lost pod:
pkill -9 tailscaled
rm -f /workspace/tailscale.state    # only the shared one, not per-host
# Re-run setup-vllm.sh with TAILSCALE_AUTHKEY + TAILSCALE_HOSTNAME set.
```

---

## Issue 3 — gpu.sh spend tracking silently broken

Three independent bugs (all fixed):

### 3a. systime() is gawk-only

`compute_spend_today` END block called `systime()` — fails on macOS BSD awk.
Active pods (still running) never counted. Spend always reported $0 while
sessions were live.

**Fix**: pass `now` from shell as `-v now="$(date +%s)"` to awk.

### 3b. active[$2] in if-check creates phantom keys

`if (active[$2])` *creates* the array element as a side effect of access.
Stray STOP entries (from terminated pods) created phantom active entries
that bloated the END loop's pod-count.

**Fix**: use `if (($2) in active)` — the membership test doesn't create.

### 3c. SPOT_BID_PER_GPU used as rate for on-demand pods

`record_spend_start` always wrote `${SPOT_BID_PER_GPU:-0.44}` as the cost rate.
For on-demand H100 ($2.99/hr) this under-reported by ~7x.

**Fix**: branch on `$SPOT`. For on-demand, query `runpodctl pod get` for the
actual `costPerHr`.

---

## Issue 4 — 30GB container disk fills up on H100/A100 spike pods

### Symptoms

- vLLM startup logs `OSError: [Errno 28] No space left on device`
- `df -h /` shows 30G/30G used

### Root cause

Stock RunPod pytorch image starts at ~10GB used. Add vLLM install (~5GB),
HuggingFace cache for Qwen3.6-27B-AWQ (~17GB), Triton/Inductor caches —
30GB default fills.

### Fix

Set `CONTAINER_DISK_GB=80` in spike profiles (no network volume). Using a
network volume side-steps this since `/workspace` is a different mount.

---

## Issue 5 — gpu.sh single LAST_POD_FILE collides between profiles

### Symptoms

- Boot pod with `PROFILE=qwen35b-a3b-...`. State file `.runpod-pod-id` written.
- Boot another with `PROFILE=qwen27b-...`. Overwrites same state file.
- `gpu.sh terminate` on the second profile kills the first pod.

### Fix

gpu.sh derives `LAST_POD_FILE` from `POD_NAME` after profile loads:
`$SCRIPT_DIR/.runpod-pod-id-${POD_NAME}`. Each profile gets its own state
file automatically. Can still be overridden explicitly if needed.

---

## Verified bench results (single-user, AWQ-INT4 quantization, vLLM 0.19.1)

| GPU | Bandwidth | tok/s | $/hr | Notes |
|---|---|---|---|---|
| A40 (Qwen3.6-27B dense) | 696 GB/s | 30 | 0.44 | baseline |
| A40 (Qwen3.6-35B-A3B MoE) | 696 GB/s | 83 | 0.44 | 3B active per token |
| H100 80GB (Qwen3.6-27B dense) | 3,350 GB/s | 95 | 2.99 | with `--gdn-prefill-backend triton` |
| A100 80GB PCIe (Qwen3.6-27B dense) | ~1,935 GB/s | TBD | 1.89 | predicted 65-90 tok/s |
| **A100 SXM4 80GB (Qwen3.6-27B dense, Vast)** | **2,039 GB/s** | **65.8** | **0.895** (on-demand) / **0.752** (reserved) | Vast.ai Czechia, Triton GDN fallback. 56 tok/s short / 63 medium / 65.8 long. ~$77/mo at 4hr/day×5d/wk. |

Architecture-bandwidth math: dense 27B reads ~17GB weights/token, MoE-3B-active
reads ~1.7GB/token. A40 ceiling on dense ≈ 41 tok/s; on MoE ≈ 400 tok/s
(compute-saturated well before that).

---

## Vast.ai spike (A100 SXM4 80GB, Czechia) — 2026-04-26

First Vast rental, manual provisioning via UI (no `gpu.sh` automation). On-demand
$0.895/hr, reserved $0.752/hr. Rented `vastai/pytorch:2.7.0-py3.11-cuda12.8.1-devel`
template with 100 GB disk.

### Issue 6 — pip read-timeout on first vLLM install

#### Symptoms

- `setup-vllm.sh` runs, `pip install --upgrade 'vllm>=0.15.0'` starts
- Mid-install: `urllib3.exceptions.ReadTimeoutError: HTTPSConnectionPool(host='files.pythonhosted.org', ...): Read timed out.`
- `set -e` propagates, script aborts

#### Root cause

PyPI default pip timeout is 15 s. Vast EU pods occasionally hit slow PyPI
mirrors during install of large wheels (torch 2.10 = 800 MB+). One read stall
> 15 s and the whole install dies.

#### Fix

`setup-vllm.sh:install_vllm_if_missing()` now defaults to
`pip install --timeout 120 --retries 5 'vllm>=0.15.0'`. No re-run needed.

### Issue 7 — `/workspace` is overlay-root on Vast, not a separate volume

#### Symptoms

- `df -h /workspace` shows same size and usage as `/`
- `verify_volume_mount()` warns "not a mounted filesystem" but proceeds

#### Root cause

Vast.ai mounts the rental disk as the overlay root. `/workspace` is just a
directory on `/`, not a separate `/workspace` mount like RunPod. Behavior is
fine for spikes (caches still persist for the pod's lifetime) but caches do
**not** persist across pod recreates the way they would on a RunPod network volume.

#### Fix

None needed for spikes. For long-running setups, request a Vast volume separately
and mount under `/workspace`.

### Issue 8 — DERP-relayed Tailscale connection adds latency (not throughput cost)

#### Symptoms

- `tailscale ping vllm-qwen27b-vast` shows "via DERP(fra)" not direct
- First-byte latency to `/v1/chat/completions`: ~600 ms over what direct would give
- Tok/s during streaming unaffected (server-side measured)

#### Root cause

Vast Czechia pod sits behind symmetric NAT. The Mac is also behind NAT.
Tailscale couldn't punch a direct UDP path; falls back to relay through
Frankfurt DERP server. Adds ~500 ms RTT but doesn't throttle bandwidth.

#### Fix

Acceptable for coding-agent UX (TTFT ~600 ms is barely noticeable next to
the 1-2 s prefill of long contexts). For lower latency:
- Try `tailscale set --advertise-exit-node=false` and re-up
- Or rent a Vast pod in a region your home tailnet has direct connectivity to
- Or accept it; throughput is what matters

### Issue 9 — Vast template's pre-existing `venv` in `/workspace`

#### Symptoms

- Fresh pod has `/workspace/venv` from the rental template
- vLLM install still goes to system Python (we don't activate the venv)

#### Root cause

Vast templates include a starter venv but `setup-vllm.sh` calls `pip` and
`python3` without activating any venv. Consistent — install lands in
`/usr/local/lib/python3.10/dist-packages/`. Just noise; ignore the template venv.

### Pod provisioning checklist (Vast UI)

1. Filter for **A100 SXM4 80GB**, verified hosts only, region: Europe / NA
2. Disk: **100 GB** minimum (model + vLLM + caches ≈ 30 GB)
3. Template: any `pytorch:2.x-cuda12.x` works (we used 2.7.0/cuda12.8)
4. SSH key: confirm pubkey matches `~/.ssh/id_ed25519.pub`
5. After "Run", click **Open in SSH** to copy `ssh -p <port> root@<host>`

### Setup walkthrough (manual, no automation yet)

```sh
# 1. Push setup script
scp -P 16047 scripts/setup-vllm.sh root@ssh4.vast.ai:/workspace/

# 2. Run with env vars from profile + .env
set -a; . scripts/.env; . scripts/profiles/qwen3.6-27b-4b-vast.env; set +a
ssh -p 16047 root@ssh4.vast.ai "TAILSCALE_AUTHKEY=$TAILSCALE_AUTHKEY \
  TAILSCALE_HOSTNAME=vllm-qwen27b-vast \
  VLLM_MODEL=$VLLM_MODEL VLLM_MAX_LEN=$VLLM_MAX_LEN \
  VLLM_GPU_UTIL=$VLLM_GPU_UTIL VLLM_TOOL_PARSER=$VLLM_TOOL_PARSER \
  VLLM_EXTRA_ARGS='--gdn-prefill-backend triton' \
  VOLUME_MOUNT_PATH=/workspace \
  nohup /workspace/setup-vllm.sh > /workspace/run-setup.log 2>&1 &"

# 3. Poll readiness (8-12 min cold start: pip + 17 GB model + warmup + compile)
ssh -p 16047 root@ssh4.vast.ai 'curl -fsS http://localhost:8000/v1/models'

# 4. Bench from Mac (over tailnet)
python3 /tmp/bench_pods.py    # POOLS = [("27B-VAST", "vllm-qwen27b-vast", "...")]
```

### Cost math at 4 hr/day × 5 day/week (≈ 86.6 hr/mo)

| Tier | $/hr | $/mo |
|---|---|---|
| Vast on-demand | 0.895 | **77.5** |
| Vast reserved | 0.752 | 65.1 |
| RunPod H100 spot | ~2.00 | 173 (hypothetical, doesn't fit budget) |

### Smoke-test results (pending formal scenarios)

- Cline tool-calling: TBD
- Pi agent (developer + architect): TBD
- Code quality (no hallucinations / refusals): TBD
- Latency UX over DERP relay: TBD

See `scripts/profiles/qwen3.6-27b-4b-vast.env` for the spike profile.

---

## Quick-start: bench a fresh model on H100/A100

```sh
# 1. Profile with VLLM_EXTRA_ARGS already wired in:
PROFILE=qwen3.6-27b-awq-h100 ~/code/orchestrator/scripts/gpu.sh runpod create

# 2. Wait for ready (boot + flashinfer fallback ~5-8 min):
until ssh -i ~/.ssh/id_ed25519 -p $POD_PORT root@$POD_IP \
  "curl -fsS --max-time 2 http://localhost:8000/v1/models >/dev/null 2>&1"; do
  sleep 30
done

# 3. Bench:
python3 /tmp/bench_pods.py    # see scripts/H100_A100_FIXES.md repo

# 4. Auto-terminate after N hours:
DAILY_HOURS=2 PROFILE=qwen3.6-27b-awq-h100 \
  ~/code/orchestrator/scripts/gpu.sh runpod supervise

# 5. Or manual kill:
PROFILE=qwen3.6-27b-awq-h100 ~/code/orchestrator/scripts/gpu.sh runpod terminate
```
