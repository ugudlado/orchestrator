# RunPod + vLLM + Qwen3-Coder

Spin up a RunPod GPU pod, install vLLM, serve a coding model with OpenAI-
compatible API, and tunnel it to `localhost:8000` for use with Cline, Continue,
or anything that speaks the OpenAI chat-completions API.

## Quickstart

```bash
./gpu.sh create        # creates/attaches volume, pod, installs vLLM, opens tunnel
curl http://localhost:8000/v1/models      # verify
./gpu.sh logs          # tail vLLM log
./gpu.sh terminate     # destroy pod (volume survives)
```

After `create`, the vLLM server is reachable at **http://localhost:8000/v1** —
point Cline or any OpenAI-compatible client there.

## All commands

- `./gpu.sh create` — create pod, attach volume, install vLLM, open tunnel
  - Prefix with `SPOT=1` for an interruptible (bid-priced) pod
- `./gpu.sh ssh` — interactive shell on pod
- `./gpu.sh tunnel` — open/reopen SSH tunnel `localhost:8000 -> pod:8000`
- `./gpu.sh status` — pod state + tunnel health + vLLM `/v1/models` check
- `./gpu.sh health` — one-shot health probe (exit 0 = healthy, for scripting)
- `./gpu.sh spend` — today's accumulated pod spend
- `./gpu.sh logs` — tail `/workspace/vllm.log` on the pod
- `./gpu.sh restart-vllm` — restart vLLM without recreating the pod
- `./gpu.sh terminate` — delete pod (network volume survives)
- `./gpu.sh supervise` — watchdog loop: auto-recreate on interruption,
  auto-restart vLLM if it dies, kill on daily budget hit

## Spot instance + supervisor workflow

For long experiments at ~50% cost (accepting occasional interruptions):

```bash
# start in one terminal
SPOT=1 ./gpu.sh create

# start supervisor in another terminal
./gpu.sh supervise
```

Supervisor polls every 30s, recreates pod on spot interruption, restarts vLLM
if it hangs, and terminates everything if daily spend exceeds `DAILY_BUDGET`
(default $5). macOS desktop notifications fire on each event (set
`NOTIFY_ENABLED=0` to silence).

Typical event flow during an interruption:

```
[12:04] OK — healthy uptime=8120s (spend: $1.12)
[12:04] Health fail: pod-gone (consec=1, hourly=1)
[12:04] Pod gone — recreating in 30s
[12:08] Pod back online
[12:08] OK — healthy uptime=240s
```

Downtime: ~3-5 min per interruption. Model cached on volume → no re-download.

## Required `.env`

`scripts/.env` is loaded automatically. Minimum keys:

| Var | Purpose |
|---|---|
| `RUNPOD_API_KEY` | RunPod API auth |
| `POD_NAME` | Display name for the pod |
| `GPU_TYPE` | e.g. `NVIDIA A40` (default; 48GB VRAM) |

### Model & vLLM tuning

| Var | Default | Purpose |
|---|---|---|
| `VLLM_MODEL` | `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` | HuggingFace model ID |
| `VLLM_MAX_LEN` | `65536` | Max context tokens |
| `LOCAL_PORT` / `REMOTE_PORT` | `8000` | Tunnel and vLLM port |

### Volume auto-discovery

The script auto-discovers (or creates) a network volume so model weights persist
across pod recreations.

| Var | Default | Purpose |
|---|---|---|
| `VOLUME_NAME` | `vllm-cache` | Name to look for / create |
| `VOLUME_SIZE_GB` | `60` | Size when auto-creating |
| `VOLUME_CREATE_DC` | `EU-SE-1` | DC when auto-creating (must support volumes) |
| `RUNPOD_VOLUME_ID` | *unset* | Explicit volume ID (skips discovery) |
| `RUNPOD_DATA_CENTER_IDS` | *unset* | Explicit DC (auto-inferred from volume) |

Valid DCs for network volumes include `US-KS-2`, `US-GA-2`, `US-IL-1`,
`US-TX-3`, `US-WA-1`, `CA-MTL-3`, `EU-NL-1`, `EU-SE-1`. Check the RunPod
console Storage page or try `runpodctl network-volume create --data-center-id X`
for the current list.

## What lives on the network volume

Everything that would otherwise fill the pod's 30GB ephemeral disk:

- `/workspace/hf_cache` — HuggingFace model downloads (~18GB per model)
- `/workspace/triton_cache` — Triton JIT kernels
- `/workspace/torch_compile_cache` — TorchInductor compile artifacts
- `/workspace/vllm_cache` — vLLM internal cache
- `/workspace/tmp` — TMPDIR override
- `/workspace/vllm.log` — server log

Next pod creation in the same DC with the same volume attached **skips** the
18GB model download entirely. Cold start: ~3 min load + ~1 min CUDA graph
compile → serving.

## Changing the model

Pick any vLLM-compatible model on HuggingFace. Edit `.env`:

```
VLLM_MODEL=Qwen/Qwen3-Coder-Next          # if/when it fits the GPU
VLLM_MAX_LEN=32768                         # drop for OOM
```

Then either `./gpu.sh restart-vllm` (if pod is running) or `./gpu.sh terminate && ./gpu.sh create`.

### Recommended models by GPU

- **A40 (48GB)**: `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` (default)
- **A100 80GB**: `Qwen/Qwen3-Coder-Next` at Q4 (46GB), `Qwen/Qwen3-Coder-30B-A3B-Instruct` at BF16
- **H100 80GB**: same as A100, faster
- **Dual H100 160GB**: `Qwen/Qwen3-Coder-480B-A35B-Instruct` at INT4 (~250GB — needs offload or 2× H100)

## Cline VS Code setup

Install the Cline extension, then in Cline settings:

| Field | Value |
|---|---|
| API Provider | `OpenAI Compatible` |
| Base URL | `http://localhost:8000/v1` |
| API Key | `sk-dummy` (any non-empty string) |
| Model ID | `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` |
| Context Window | `65536` |
| Max Output Tokens | `8192` |

Enable **Use Compact Prompt**, disable **KV Cache Quantization**.

## Gotchas we learned the hard way

**hf-xet crashes on some filesystems.** We set `HF_HUB_DISABLE_XET=1` to force
the legacy downloader. Fixes "Background writer channel closed" errors.

**Triton writes to `/tmp` during compile.** With a 30GB ephemeral disk and a
30B model, compile fills the disk and crashes. Fix: `TMPDIR`, `TRITON_CACHE_DIR`,
and `TORCHINDUCTOR_CACHE_DIR` all redirected to `/workspace`.

**"A100 SXM High stock" in runpodctl can lie.** We saw stuck provisions where
pods reported `RUNNING` but `uptime=0s` forever. Fix: terminate after ~5 min and
retry, or switch to another GPU tier.

**RunPod's network volumes are DC-locked.** Volume lives in one DC — pod must
spawn in the same DC. Auto-discovery infers this, but if you explicitly set
`RUNPOD_VOLUME_ID` you must also set `RUNPOD_DATA_CENTER_IDS`.

**Cline rejects empty API keys.** Use `sk-dummy` or any non-empty placeholder —
vLLM ignores it, but Cline validates client-side.

## Terminate behavior

`./gpu.sh terminate` destroys the pod and kills the local tunnel. The network
volume **stays** in your RunPod account at ~$0.07/GB/month (~$4/mo for a 60GB
volume). Delete it in the RunPod console if you want to stop the storage cost.

## Supervisor tuning

| Var | Default | Purpose |
|---|---|---|
| `SPOT` | `0` | `1` = use interruptible bid pricing via GraphQL |
| `SPOT_BID_PER_GPU` | `0.22` | Max $/hr willing to pay on spot |
| `DAILY_BUDGET` | `5` | Supervisor kills pod if today's spend hits this |
| `SUPERVISOR_POLL_SECS` | `30` | Health check interval |
| `SUPERVISOR_MAX_FAILS_PER_HOUR` | `5` | Abort if this many failures in 1hr window |
| `NOTIFY_ENABLED` | `1` | macOS `osascript` desktop notifications |

Spend is tracked in `scripts/.spend-log` and computed per local day. Supervisor
log goes to `scripts/.supervisor.log`.

## Future work

- Multi-model serving (switch at runtime without full pod recreate)
- Automated quality benchmarking vs Claude Sonnet
- Dual-GPU layouts for 480B-class models
- Hot-standby (second warm pod) for zero-downtime failover
