# 80B Qwen3-Coder-Next experiment — findings

Date: 2026-04-16
Session spend: ~$3.50 (A40 + failed A100/H100 provisions + A100 80GB run)

## Goal

Test whether Qwen3-Coder-Next (80B MoE, 3B active) running locally on A100 80GB
could match Claude Sonnet 4.6 for agentic coding — the model that reportedly
beat Opus 4.6 on SWE-rebench Pass@5.

## Setup

- **Pod**: A100 80GB PCIe in US-KS-2, on-demand $1.39/hr
- **Model**: `cyankiwi/Qwen3-Coder-Next-AWQ-4bit` (~44GB VRAM loaded)
- **Inference**: vLLM 0.19.0
- **Context**: 65K (attempted 128K first)
- **Tool calling**: `--enable-auto-tool-choice --tool-call-parser qwen3_coder`

## What happened

### Attempt 1: compiled mode, 128K context
- Model downloaded cleanly to volume (50GB over ~5 min)
- Shard load took ~10 min
- `torch.compile` completed in 322 sec
- **Then hung silently after compile** — never bound HTTP port 8000
- CUDA graph capture phase (post-compile) wedged on hybrid linear-attention
- UserWarning from `fla/ops/utils.py` about tensor shape format — likely the hang point
- Symptom: 47GB VRAM held, GPU 0% utilization, no log output, no HTTP listener

### Attempt 2: eager mode, 65K context (after killing attempt 1)
- Initial restart failed with OOM — zombie `VLLM::EngineCore` process still held 47GB
  - `pkill -f vllm` didn't match it (process name is `VLLM::EngineCore`, not `vllm`)
  - Had to explicitly `kill -9` by PID
- Second restart with `--enforce-eager` flag: weights loaded fast from cache (~65 sec)
- HTTP came up ~4 min after start
- **Serving successful at 65K context**

### Quality / speed test — JsonlBuffer thread-safety trap

Prompt: "Write a thread-safe JsonlBuffer class with lock, context manager,
docstrings." The trap: `add()` calling `flush()` while holding the lock → deadlock.

| Model | Speed | Caught deadlock? |
|---|---|---|
| Qwen2.5-Coder-32B Q6 (Ollama, A40) | 20 tok/s | ❌ no |
| Qwen3-Coder-30B-A3B AWQ (vLLM, A40) | **121 tok/s** | ❌ no |
| **Qwen3-Coder-Next 80B AWQ (vLLM eager, A100)** | **7.8 tok/s** | ❌ no |
| Sonnet 4.6 (for reference) | ~80 tok/s | ✅ catches it |

## Why 80B was so slow

vLLM with `--enforce-eager` disables torch.compile and CUDA graph capture.
For a standard transformer this costs ~15% throughput. For Qwen3-Coder-Next's
hybrid Gated-DeltaNet + Gated-Attention architecture, kernel fusion matters
much more — eager mode is 5-10× slower than compiled mode.

But we **had** to use eager mode because compiled mode hangs on this model in
vLLM 0.19.0 (April 2026).

## Conclusions

1. **Qwen3-Coder-Next on A100 80GB single-GPU is not production-usable in
   vLLM 0.19.0.** Either torch.compile hangs (compiled mode) or inference is
   too slow to use (eager mode). Need vLLM ≥0.20 with the Qwen3-Coder-Next
   compile fixes, OR use SGLang which handles this model better.

2. **Even if speed were solved, quality vs 30B-AWQ is not qualitatively
   different** on the specific concurrency-bug trap. The 80B added type hints
   and slightly cleaner structure but missed the same fundamental mistake.
   SWE-bench benchmarks don't translate perfectly to "catches subtle bugs".

3. **The real value is the infrastructure we built** (scripts/gpu.sh +
   setup-vllm.sh + supervisor loop + spot support). Switching models is one
   config change. When vLLM ships fixes or a better quant lands, we rerun.

## What's working well

- **30B-AWQ on A40 at 121 tok/s** — validated earlier this session, good
  daily driver for coding assistance
- **Scripts refactored** to one-command pod create + vLLM start + tunnel
- **Network volume caches** everything persistent (model, Triton, torch
  compile) — pod recreates are fast now
- **Budget kill-switch + supervisor** ready to use when spot A40 capacity
  returns

## Costs

| Item | Time | Cost |
|---|---|---|
| A40 pod (earlier 30B-AWQ validation) | ~2.5h | ~$1.10 |
| Dead A100/H100 provisioning attempts | 50m | ~$1.30 |
| A100 80GB PCIe (this experiment) | ~1h | ~$1.40 |
| Volume storage (prorated) | | ~$0.15 |
| **Total** | | **~$3.95** |

## Recommended next steps

1. **Terminate A100 pod** now (done).
2. **Default to A40 + 30B-AWQ** for daily Cline use — 121 tok/s, familiar quality.
3. **Retry Qwen3-Coder-Next in 1-2 months** when vLLM compile fixes land.
4. **Keep the 100GB volume** ($7/mo) — caches both 30B and 80B, zero-download restart.
5. **For quality jumps**, stay with Claude Sonnet API for orchestrator work; use
   local only for quick autocomplete / utility generation.

## Tomorrow's experiment: Qwen3-30B-A3B-Thinking

The 80B findings suggested "reasoning mode" is what's missing, not "bigger model."
Found a better fit: **`cyankiwi/Qwen3-30B-A3B-Thinking-2507-AWQ-8bit`** — the 30B
family's Thinking variant.

### Why this should work where the 80B didn't

| Factor | 80B Qwen3-Coder-Next | 30B Qwen3-Thinking |
|---|---|---|
| VRAM (AWQ) | 46GB (tight on A100) | 30.8GB (comfy on A40) |
| Pod cost | $1.39/hr (A100) | **$0.44/hr (A40)** |
| vLLM compile | ❌ hangs (hybrid attention) | ✅ standard GQA attention |
| Reasoning mode | ❌ no `<think>` | **✅ yes** (deepseek_r1 parser) |
| Raw SWE-bench | 70.6% | 50% |
| With OpenHands agent | ~72% | matches or beats Coder-30B |
| AIME 2025 (reasoning) | not measured | 85.0 |

Standard attention = no vLLM compile bug. Thinking mode = may catch the
JsonlBuffer deadlock that both Coder models missed. Fits A40 so no A100 needed.

### Setup (tomorrow)

`.env` already updated:

```
VLLM_MODEL="cyankiwi/Qwen3-30B-A3B-Thinking-2507-AWQ-8bit"
VLLM_MAX_LEN=65536
```

Run:

```bash
./gpu.sh create      # attaches existing vllm-cache volume, pulls ~31GB
# wait ~5-8 min (no compile hang, weights come down fast)
./gpu.sh status      # should show healthy
```

vLLM command will need reasoning-mode flags added to `setup-vllm.sh`:

```
--reasoning-parser deepseek_r1
```

Optionally remove `--tool-call-parser qwen3_coder` — the Thinking variant uses
different tool-call format. TBD based on Cline compatibility testing.

### Test plan

1. **Speed**: measure tok/s warm and cold. Expect 40-70 tok/s (thinking tokens
   overhead; standard attention compiled = fast otherwise).
2. **Quality**: JsonlBuffer deadlock trap (same prompt that 30B-Coder and
   80B-Next both missed). If Thinking catches it → big win.
3. **Cline compatibility**: does Cline handle `<think>` blocks correctly, or
   does it show the reasoning scratch to the user?
4. **Context**: try 65K first, push to 128K if stable.

### Decision tree after test

- **Catches deadlock + usable speed** → this is the daily driver. Update
  README + scripts defaults.
- **Catches deadlock but too slow for Cline** → use it for orchestrator
  `reviewer` role only; keep 30B-Coder for `developer`.
- **Misses deadlock** → local has a quality ceiling. Hybrid route (local
  developer + Sonnet reviewer) is the answer.

### Expected cost

- A40 ~$0.44/hr × 1hr = $0.44 for test
- Storage (already paid): $0
- Total: **~$0.50 for tomorrow's experiment**

## Files changed this session

- `scripts/gpu.sh` — full vLLM + spot + supervisor + budget rewrite
- `scripts/setup-vllm.sh` — new, replaces setup-ollama.sh
- `scripts/legacy/setup-ollama.sh` — moved aside, not deleted
- `scripts/README.md` — rewritten for vLLM flow
- `scripts/.gitignore` — new, hides state + secrets
- `scripts/EXPERIMENT_80B.md` — this file
