# LLM Infrastructure

Three decoupled components for running LLM workloads across any provider or host.

## Architecture

```
Orchestrator (Claude Code / Codex / any host)
│
│  Step: agent: developer
│  llm_route("developer") → PROXY or NATIVE
│
├── NATIVE (native_opus, native_sonnet, native_o3...)
│   → Host's built-in sub-agent
│
└── PROXY (qwen, qwen-local...)
    → llm_submit → Monitor → llm_result
    │
    ▼
LLM Proxy (:4000)
│
├── OpenRouter     → "qwen": model ID + API key
└── LLM Manager    → "qwen-local": pod name + manager key
                     (resolves to tailnet hostname `http://<pod-name>:8000`)
```

**Agents don't know which model runs them.** The mapping is in `routes.json`:

```json
{
  "agents": {
    "architect": "native_opus",    // Claude sub-agent
    "developer": "qwen"           // routed via proxy
  },
  "models": {
    "qwen":       { "url": "https://openrouter.ai/api/v1", "model": "qwen/qwen3-coder-30b-a3b-instruct" },
    "qwen-local": { "url": "http://localhost:3456/v1", "model": "coder" }
  }
}
```

Switch agent routing with one line. Switch hosts by changing `native_*` entries:
- Claude Code: `native_opus`, `native_sonnet`
- Codex: `native_o3`, `native_o3-mini`
- OpenCode: `native_deepseek`

---

## Components

### LLM Proxy (`llm-proxy/`, port 4000)

Agent-to-model router with async task queue.

```bash
cd scripts/llm-proxy && npm start
```

| Endpoint | Purpose |
|----------|---------|
| `GET /routes/:agent` | Resolve agent → native or proxy |
| `POST /tasks` | Submit async work `{ agent, messages }` |
| `GET /tasks/:id/wait` | Long-poll until complete (for Monitor) |
| `GET /tasks/:id/result` | Collect response |
| `POST /reload` | Hot-reload routes.json |
| `GET /health` | Health check |

Convention: `native_<model>` in agent mapping = use host sub-agent. Anything else = route through proxy to model config.

### LLM Manager (`llm-manager/`, port 3456)

RunPod pod management UI. Also acts as an OpenAI-compatible API provider
for self-hosted models — like OpenRouter but for your own pods.

```bash
cd scripts/llm-manager && npm start
# Open http://localhost:3456
```

**UI features**: create/terminate pods, GPU presets, cost display.

**As API provider** (pod name = model name):
```bash
curl http://localhost:3456/v1/chat/completions \
  -H "Authorization: Bearer $LLM_MANAGER_KEY" \
  -d '{"model": "coder", "messages": [...]}'
```

### MCP Server (`mcp-qwen/`)

Claude Code integration. Three tools:

| Tool | Purpose |
|------|---------|
| `llm_route(agent)` | Ask proxy: native sub-agent or async proxy? |
| `llm_submit(agent, messages)` | Fire async work, get task_id |
| `llm_result(task_id)` | Collect completed result |

### GPU Scripts

GPU-pod provisioning and the vLLM serving harness moved to a separate repo: [hopper](~/code/hopper). The orchestrator agent harness (`mcp-qwen`, `llm-manager`, `routes.yaml`) still lives here and consumes hopper's output via Tailscale hostnames (e.g. `vllm-qwen27b-vast:8000`).

---

## Switching models

```
"developer": "qwen"          → OpenRouter ($0.08/M tokens)
"developer": "qwen-local"    → LLM Manager → RunPod pod via tailnet (`http://<pod-name>:8000`)
"developer": "native_sonnet" → Claude Sonnet sub-agent
```

## Experiment results

See `EXPERIMENT.md` for model evaluation data:

| Role | Qwen3-Coder-30B Score | Blind Spot |
|------|----------------------|------------|
| Generator | 7/8 (88%) | Re-entrant lock deadlock |
| Reviewer | 9/10 (90%) | Re-entrant lock deadlock |
| Fix from feedback | PASS | None |

- 121 tok/s on A40 ($0.35/hr) via vLLM
- Tailnet direct access uses pod hostnames like `vllm-qwen27b:8000` and `vllm-qwen35b:8000` (matches `POD_NAME` in the profile env) instead of a localhost tunnel
- Thinking variant: no improvement, slower, worse at fixes
- Total experiment cost: $4.05 across 4 experiments

---

## .env reference

| Var | Purpose |
|-----|---------|
| `RUNPOD_API_KEY` | RunPod API auth |
| `OPENROUTER_API_KEY` | OpenRouter API auth |
| `LLM_MANAGER_KEY` | Auth for LLM Manager API |
| `GPU_TYPE` | Default GPU (e.g. `NVIDIA A40`) |
| `GPU_FALLBACKS` | Comma-separated fallback GPUs |
| `PROFILE` / `PROFILE_FILE` | Optional profile env override loaded after `.env` (e.g. `qwen27b`, `qwen35b`) |
| `VLLM_MODEL` | HuggingFace model ID |
| `VLLM_MAX_LEN` | Max context tokens (default 65536) |
| `VOLUME_NAME` | Network volume name (`none` = ephemeral) |
