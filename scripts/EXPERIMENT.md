# Local LLM for Coding — Experiment Log

Goal: find a local model + infrastructure setup that can serve as a fast coding
assistant (via Cline) and potentially handle orchestrator agent roles, within a
$50/month budget.

## Infrastructure built

```
scripts/
├── gpu.sh              one-command pod lifecycle + supervisor
├── setup-vllm.sh       installs vLLM, serves model, waits for health
├── README.md           full usage docs
├── legacy/
│   └── setup-ollama.sh preserved, no longer the default
└── .env                model + RunPod config (gitignored)
```

Key features:
- Auto-discover or create network volume by name
- vLLM with tool calling + all caches on network volume
- SSH tunnel auto-open after create
- Spot instance support via GraphQL (`SPOT=1`)
- Supervisor loop with auto-recreate, budget kill-switch, macOS notifications
- `./gpu.sh status|health|spend|logs|restart-vllm` helpers

## Experiments

### Exp 1: Ollama + Qwen2.5-Coder-32B Q6 on A40

**Date**: 2026-04-16
**Setup**: Ollama, A40 48GB (EU-SE-1), on-demand $0.44/hr
**Model**: `qwen2.5-coder:32b-instruct-q6_K` (26GB)
**Context**: 32K

| Metric | Result |
|---|---|
| Speed | 20 tok/s |
| JsonlBuffer deadlock caught? | No |
| Code quality | Syntactically good, misses subtle bugs |
| Verdict | **Too slow for interactive use** |

**Key finding**: Ollama's single-request serving + GGUF quant on NVIDIA GPU =
3-6x slower than vLLM with AWQ on same hardware.

---

### Exp 2: vLLM + Qwen3-Coder-30B-A3B AWQ on A40

**Date**: 2026-04-16
**Setup**: vLLM 0.19.0, A40 48GB (EU-SE-1), on-demand $0.44/hr
**Model**: `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` (18GB)
**Context**: 65K

| Metric | Result |
|---|---|
| Speed | **121 tok/s** |
| JsonlBuffer deadlock caught? | No |
| Code quality | Good for standard tasks, misses concurrency edge cases |
| Tool calling | Works with `--tool-call-parser qwen3_coder` |
| Cline integration | Working, fast, usable |
| Verdict | **Current daily driver. Fast, cheap, good enough for 70-80% of tasks.** |

**Key finding**: vLLM + AWQ on same hardware = 6x faster than Ollama + GGUF.
MoE architecture (3.3B active of 30B total) makes this absurdly fast.

**Issues solved during setup**:
- `hf-xet` download crash → `HF_HUB_DISABLE_XET=1`
- Triton fills `/tmp` (30GB ephemeral) → redirect `TMPDIR`, `TRITON_CACHE_DIR` to `/workspace`
- Zombie `VLLM::EngineCore` holds VRAM after kill → explicit match in `stop_existing_vllm()`

---

### Exp 3: vLLM + Qwen3-Coder-Next 80B AWQ on A100 80GB

**Date**: 2026-04-16
**Setup**: vLLM 0.19.0, A100 80GB PCIe (US-KS-2), on-demand $1.39/hr
**Model**: `cyankiwi/Qwen3-Coder-Next-AWQ-4bit` (44GB loaded)
**Context**: 65K (attempted 128K first)

| Metric | Result |
|---|---|
| Speed (compiled mode) | Hung — never served |
| Speed (eager mode) | **7.8 tok/s** |
| JsonlBuffer deadlock caught? | No |
| Code quality | Similar to 30B + type hints, slightly cleaner structure |
| Verdict | **Not production-usable in vLLM 0.19.0.** |

**Root causes**:
1. Compiled mode: CUDA graph capture hangs silently on hybrid Gated-DeltaNet attention (`fla/ops/utils.py`)
2. Eager mode: disabling compile fixes the hang but kills throughput (5-10x slower for hybrid attention models)
3. Zombie `VLLM::EngineCore` process name doesn't match `pkill -f vllm` — holds VRAM after parent kill

**Wasted spend**: ~$1.30 on stuck A100/H100 provisions that never booted (RunPod capacity issue, not our code).

---

### Exp 3.5: Failed pod provisions (A100 + H100)

**Date**: 2026-04-16
**What happened**: Tried to spin up bigger GPUs across ~12 data centers.

| Attempt | GPU | DC | Result |
|---|---|---|---|
| A100 SXM #1 | A100-SXM4-80GB | US (community) | Stuck — uptime=0s for 40 min, $0.93 wasted |
| A100 SXM #2 | A100-SXM4-80GB | US (community) | Stuck — same failure, $0.14 |
| H100 SXM (community) | H100 80GB HBM3 | 9 DCs tried | All rejected — 0/9 creates succeeded |
| H100 SXM (secure) | H100 80GB HBM3 | IN (secure) | Stuck — uptime=0s for 5 min, $0.22 |

**Key finding**: RunPod's `gpu list` and `datacenter list` report stale availability.
"High stock" doesn't mean creates will succeed. Always probe with a real create attempt.

**Lesson encoded in scripts**: supervisor detects stuck provisions (uptime=0s after 5 min) and auto-terminates.

---

### Exp 4 (tomorrow): vLLM + Qwen3-30B-A3B-Thinking on A40

**Date**: 2026-04-17 (planned)
**Setup**: vLLM 0.19.0, A40 48GB, on-demand $0.44/hr
**Model**: `cyankiwi/Qwen3-30B-A3B-Thinking-2507-AWQ-8bit` (30.8GB)
**Context**: 65K (push to 128K if stable)

**Hypothesis**: the quality gap isn't about model size — it's about reasoning.
The Thinking variant has `<think>` chain-of-thought that may catch subtle bugs
the Instruct/Coder variants miss.

**Why this should work where Exp 3 didn't**:
- Standard GQA attention (no hybrid Gated-DeltaNet) → no vLLM compile hang
- 30.8GB VRAM → fits A40 comfortably (no A100 needed)
- $0.44/hr → 3x cheaper than the A100 experiment
- Reasoning mode → the feature we actually need, not more params

**Setup**:
```bash
# .env already configured:
VLLM_MODEL="cyankiwi/Qwen3-30B-A3B-Thinking-2507-AWQ-8bit"
VLLM_MAX_LEN=65536

# Run:
./gpu.sh create
# wait ~5-8 min (download ~31GB, load, compile — no hang expected)
./gpu.sh status
```

**Note**: `setup-vllm.sh` needs `--reasoning-parser deepseek_r1` flag added for
this variant. May also need to drop `--tool-call-parser qwen3_coder` depending
on Cline compatibility.

**Test plan**:

1. Speed: warm tok/s (expect 40-70, slower than 121 due to thinking overhead)
2. Quality: JsonlBuffer deadlock trap — THE test
3. Cline: does it strip `<think>` blocks or show them?
4. Context: 65K → 128K push

**Decision tree**:

```
Catches deadlock?
├── YES + fast enough → new daily driver
├── YES + too slow for Cline → use for reviewer agent only
└── NO → local ceiling confirmed, hybrid (local dev + Sonnet reviewer) is answer
```

**Expected cost**: ~$0.50

---

## Model landscape (April 2026)

What fits single-GPU vs what doesn't:

| Model | Total/Active | VRAM (Q4) | SWE-bench | Fits A40? | Fits A100? |
|---|---|---|---|---|---|
| Qwen3-Coder-30B-A3B | 30B/3.3B | 18GB | ~65% | Easy | Easy |
| Qwen3-30B-A3B-Thinking | 30B/3.3B | 31GB | 50% raw, ~70% w/agents | Yes | Easy |
| Qwen3-Coder-Next | 80B/3B | 46GB | 70.6% | Tight | Yes (buggy) |
| GLM-5 | 744B/40B | 241GB+ | 77.8% | No | No (needs 8xH200) |
| Kimi K2.5 | 1T/32B | 600GB | 76.8% | No | No (needs 2TB) |
| MiniMax M2.5 | 229B/10B | 457GB | 80.2% | No | No (needs 4xH100) |

Frontier models (GLM-5, Kimi, MiniMax) are out of reach for single-GPU / $50/mo.
The practical ceiling is Qwen3-30B class with different specializations (coder vs thinking).

## Cost analysis

### Running costs

| Config | $/hr | 4h/day 5d/week | Monthly |
|---|---|---|---|
| A40 on-demand | $0.44 | $8.80/wk | ~$38 |
| A40 spot | $0.22 | $4.40/wk | ~$19 |
| A100 on-demand | $1.39 | $27.80/wk | ~$120 |
| Volume storage (100GB) | — | — | ~$7 |

### Comparison to API alternatives

| Option | Monthly (at 86h heavy use) |
|---|---|
| A40 spot + volume | **~$26** |
| A40 on-demand + volume | **~$45** |
| Claude Sonnet API (heavy coding) | ~$60-150 |
| Featherless flat rate | ~$25 |
| DeepInfra pay-per-token | ~$34 |

### Session spend log

| Date | What | Cost |
|---|---|---|
| 2026-04-16 | A40 Ollama + vLLM validation | $1.10 |
| 2026-04-16 | Dead A100/H100 provisions | $1.30 |
| 2026-04-16 | A100 80B experiment | $1.40 |
| 2026-04-16 | Volume storage (prorated) | $0.15 |
| **Total** | | **$3.95** |

## Key learnings

1. **vLLM >> Ollama** on NVIDIA GPUs for same model: 6x throughput (121 vs 20 tok/s)
2. **Model size isn't everything**: 80B missed same bugs as 30B. Reasoning mode may matter more.
3. **MoE = fast**: 3.3B active params means 30B-total models run at speeds you'd expect from 3B models
4. **Infrastructure > model choice**: scripts, caching, supervisor took 80% of tonight's effort but enable instant model swaps going forward
5. **RunPod capacity is unpredictable**: always probe before committing; budget for wasted provisions
6. **Local ceiling for coding**: ~65-70% SWE-bench on single GPU. For Sonnet-class quality, API is still the answer. Best approach: hybrid (local fast + API for hard tasks).
7. **AWQ > GGUF on NVIDIA**: vLLM + AWQ uses INT4 tensor cores; Ollama + GGUF doesn't. Same bit-width, 20% faster.
8. **Cache everything on network volume**: model weights, Triton JIT, torch compile, vLLM cache. Pod recreates go from 20 min to 3 min.
9. **Kill zombie EngineCore processes by name**: `VLLM::EngineCore` doesn't match `pkill -f vllm`. Must kill explicitly.
10. **`HF_HUB_DISABLE_XET=1`**: mandatory on RunPod pods with network-mounted filesystems. The xet downloader crashes.
