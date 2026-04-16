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

### Exp 4: Qwen3-30B-A3B-Thinking via OpenRouter API

**Date**: 2026-04-16
**Setup**: OpenRouter API (no self-hosted infra — RunPod capacity unavailable)
**Model**: `qwen/qwen3-30b-a3b-thinking-2507`
**Context**: default (33K)

**Hypothesis**: the quality gap isn't about model size — it's about reasoning.
The Thinking variant has `<think>` chain-of-thought that may catch subtle bugs
the Instruct/Coder variants miss.

**Pivot**: RunPod had zero GPU availability across all DCs (A40, A6000, A100 all
failed to provision — stuck pods with uptime=0). Pivoted to OpenRouter API for
the quality test, deferring infra to post-validation.

| Run | Model | Time | Tokens | JsonlBuffer deadlock? |
|-----|-------|------|--------|-----------------------|
| 1 | Thinking-30B | 40.3s | 4395 | **MISSED** |
| 2 | Thinking-30B | 34.7s | 2951 | **MISSED** |
| 3 | Thinking-30B | 39.9s | 4395 | **MISSED** |
| 4 | Coder-30B | 12.7s | 559 | **MISSED** |

**Result**: 0/3 Thinking runs caught the deadlock. The model spends 3-4x more
tokens on `<think>` reasoning but never reasons through "what if add() calls
flush() while already holding the lock?" The chain-of-thought focuses on
implementing thread safety, not tracing re-entrancy.

**Conclusion**: At the 30B parameter class, neither Coder nor Thinking variants
catch concurrency bugs. The local model ceiling is confirmed. Per decision tree:

```
Catches deadlock?
└── NO → local ceiling confirmed, hybrid (local dev + Sonnet reviewer) is answer
```

**Cost**: ~$0.01 (OpenRouter API) + ~$0.07 (wasted RunPod stuck provisions)

**Infrastructure note**: `setup-vllm.sh` was updated with `--reasoning-parser`
and `--enable-reasoning` support, and `gpu.sh` now supports `VOLUME_NAME=none`
for ephemeral-disk-only runs. These changes are ready for future experiments.

---

### Exp 4b: Reviewer feedback fix test (Thinking vs Coder)

**Date**: 2026-04-16
**Setup**: OpenRouter API
**Question**: Can the model *fix* a deadlock when a reviewer points it out?

| Model | Given explicit feedback | Fixed? | Strategy | Time |
|-------|------------------------|--------|----------|------|
| Thinking-30B | Yes | **NO** — still deadlocks | None (reproduced bug) | 95s |
| Coder-30B | Yes | **YES** | `_flush_internal()` helper | 4s |

The Coder variant correctly extracted an internal flush helper when told about
the deadlock. The Thinking variant spent 95s and 2178 tokens but *still produced
the deadlock* even with explicit feedback. Thinking mode actively hurts here.

---

### Exp 4c: Coder-30B capability range (8-test suite)

**Date**: 2026-04-16
**Setup**: OpenRouter API, `qwen/qwen3-coder-30b-a3b-instruct`, temp=0.2
**Question**: What's the Coder-30B's range beyond the deadlock test?

| # | Test | Type | Time | Tokens | Result |
|---|------|------|------|--------|--------|
| 1 | Deadlock (generate) | generate | 3.6s | 247 | **FAIL** — produces deadlock |
| 2 | Race condition (detect) | review | 3.9s | 323 | **PASS** — found race condition |
| 3 | Off-by-one / infinite loop | review | 5.3s | 403 | **PASS** — found `lo = mid` bug |
| 4 | SQL injection | review | 7.7s | 227 | **PASS** — found SQL injection |
| 5 | Memory leak (handler ref) | review | 5.8s | 575 | **PASS** — found handler leak |
| 6 | Async sequential footgun | review | 14.5s | 309 | **PASS** — found sequential await |
| 7 | Fix from feedback (LRU) | fix | 8.1s | 189 | **PASS** — added maxsize + eviction |
| 8 | Type coercion (JS) | review | 7.8s | 700 | **PASS** — found string comparison |

**Score: 7/8 (88%)**

**Findings**:
- The only failure is re-entrant lock deadlock during *generation*.
- Detection works across categories: concurrency, logic, security, memory, async, types.
- Fixes from reviewer feedback work reliably (tested in 4b and test 7).
- Response times: 3-15s, token usage: 189-700 per response. Fast enough for interactive use.

**Conclusion**: Coder-30B's blind spot is narrow — it can't *avoid producing*
lock-inside-lock patterns when writing fresh code, but it *can detect* most
other bug categories and *can fix* bugs when given reviewer feedback. This
validates the hybrid architecture:

```
Coder-30B (local, 121 tok/s)     →  fast code generation (88% correct)
Sonnet/Opus (API, reviewer agent) →  catches the 12% the local model misses
Coder-30B (local)                 →  applies fixes from reviewer feedback
```

---

### Exp 4d: Coder-30B as reviewer (10-test suite)

**Date**: 2026-04-16
**Setup**: OpenRouter API, `qwen/qwen3-coder-30b-a3b-instruct`, temp=0.2
**Question**: How good is Coder-30B as a code reviewer?

| # | Test | Category | Time | Tokens | Result |
|---|------|----------|------|--------|--------|
| 1 | Deadlock (re-entrant lock) | concurrency | 64.8s | 744 | **FAIL** |
| 2 | TOCTOU race condition | concurrency | 21.2s | 2426 | **PASS** |
| 3 | SQL injection | security | 10.8s | 502 | **PASS** |
| 4 | Exception swallowing | quality | 19.5s | 482 | **PASS** |
| 5 | Mutable default argument | logic | 7.7s | 449 | **PASS** |
| 6 | Timing attack | security | 4.3s | 395 | **PASS** |
| 7 | ReDoS (catastrophic backtrack) | security | 137.3s | 4096 | **PASS** |
| 8 | Unsafe deserialization | security | 6.8s | 429 | **PASS** |
| 9 | Iterator exhaustion | logic | 12.4s | 415 | **PASS** |
| 10 | Open redirect | security | 5.1s | 490 | **PASS** |

**Reviewer score: 9/10 (90%)**

The Coder-30B is a better reviewer than generator (90% vs 88%). It catches
TOCTOU, timing attacks, ReDoS, unsafe deserialization, iterator exhaustion,
and open redirects. The only blind spot across all experiments remains the
re-entrant lock deadlock.

### Capability summary

| Role | Score | Blind spot |
|------|-------|------------|
| Generator | 7/8 (88%) | Re-entrant lock deadlock |
| Fix from feedback | PASS | None |
| Reviewer | 9/10 (90%) | Re-entrant lock deadlock |

### Decision: stick with Coder-30B

The Qwen3-Coder-30B-A3B-Instruct on vLLM (121 tok/s, $0.44/hr on A40) is the
chosen local model. The Thinking variant adds no value — slower, more expensive,
and worse at applying fixes. The 90% reviewer hit rate means the model can
handle most reviews locally, with Sonnet escalation only for concurrency-heavy
code.

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
| 2026-04-16 | Stuck A6000/A100 provisions (Exp 4) | $0.07 |
| 2026-04-16 | OpenRouter API (Exp 4, 4 calls) | $0.01 |
| 2026-04-16 | OpenRouter API (Exp 4b, 2 calls) | $0.01 |
| 2026-04-16 | OpenRouter API (Exp 4c, 8 calls) | $0.01 |
| **Total** | | **$4.05** |

## Key learnings

1. **vLLM >> Ollama** on NVIDIA GPUs for same model: 6x throughput (121 vs 20 tok/s)
2. **Model size isn't everything**: 80B missed same bugs as 30B. Reasoning mode doesn't help either (see #11).
3. **MoE = fast**: 3.3B active params means 30B-total models run at speeds you'd expect from 3B models
4. **Infrastructure > model choice**: scripts, caching, supervisor took 80% of tonight's effort but enable instant model swaps going forward
5. **RunPod capacity is unpredictable**: always probe before committing; budget for wasted provisions
6. **Local ceiling for coding**: ~65-70% SWE-bench on single GPU. For Sonnet-class quality, API is still the answer. Best approach: hybrid (local fast + API for hard tasks).
7. **AWQ > GGUF on NVIDIA**: vLLM + AWQ uses INT4 tensor cores; Ollama + GGUF doesn't. Same bit-width, 20% faster.
8. **Cache everything on network volume**: model weights, Triton JIT, torch compile, vLLM cache. Pod recreates go from 20 min to 3 min.
9. **Kill zombie EngineCore processes by name**: `VLLM::EngineCore` doesn't match `pkill -f vllm`. Must kill explicitly.
10. **`HF_HUB_DISABLE_XET=1`**: mandatory on RunPod pods with network-mounted filesystems. The xet downloader crashes.
11. **Thinking mode doesn't fix concurrency reasoning**: Qwen3-30B-A3B-Thinking spends 3-4x more tokens on `<think>` blocks but still misses the same deadlock as the Coder variant (0/3 runs). The chain-of-thought focuses on implementing patterns, not tracing execution paths through re-entrant lock acquisition.
12. **Thinking mode is worse at applying fixes**: given explicit reviewer feedback about the deadlock, Coder-30B fixed it in 4s while Thinking-30B spent 95s and still produced the bug. Thinking actively hurts for code-edit tasks.
13. **Coder-30B blind spot is narrow**: 7/8 (88%) on a range test covering race conditions, off-by-one, SQL injection, memory leaks, async footguns, type coercion, and fix-from-feedback. Only failure: generating correct re-entrant lock patterns. Detection and fix-application work fine.
14. **Validate model quality via API before solving infra**: OpenRouter at $0.03 total answered questions that would have cost $0.50+ on RunPod (if it had worked). Always test the model first, then solve deployment.
15. **RunPod "Low" stock = zero**: across two sessions, every "Low" stock GPU type failed to provision. Only "High" stock is reliable, and even that can produce stuck pods (uptime=0). The `gpu list` / `datacenter list` APIs report aspirational availability, not real-time.
