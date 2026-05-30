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

---

## Shell Agent Loop

A deterministic shell entrypoint that drives the full `orchestrator next` →
execute → `orchestrator done` loop without an LLM in the dispatch path.

### Invocation

**Preferred (ticket only, non-chat):**

```bash
# From the target repo (or pass --repo). Seeds state if missing, then runs the loop.
orchestrator run ORC-83
orchestrator run HL-287 --schema bugfix
orchestrator run 42 --repo /path/to/app worktree=true
orchestrator run ORC-84 agent.developer.subprocess=cursor
orchestrator run ORC-84 --routes-override ./my-routes.yaml
```

**Lower-level (when you already have state.yaml):**

```bash
scripts/run-workflow.sh <state.yaml>
scripts/run-workflow.sh <state.yaml> TICKET-ID
```

### Config files

| File | Purpose |
|------|---------|
| `config/tools.yaml` | Per-tool invocation shape (binary + args template) |
| `scripts/routes.yaml` | Agent role → model tier (`agents.<role>.model`) and shell-loop subprocess tool (`agents.<role>.subprocess`, e.g. `claude`, `pi`) |

To route `developer` through another tool, set `agents.<role>.subprocess` in
`scripts/routes.yaml` (or repo copy under `.orchestrator/config/scripts/routes.yaml`).
Supported tools are defined in `config/tools.yaml` (`claude`, `pi`, `codex`, `cursor`).

**Pi coding agent (default for `developer` on main):**

```yaml
# scripts/routes.yaml
agents:
  developer: { model: sonnet, subprocess: pi }
```

```yaml
# config/tools.yaml (pi entry)
pi:
  binary: pi
  args_template: ["run", "--prompt-file", "{prompt_file}"]
```

Requirements: `pi` on PATH. The subprocess must print a `COMPLETION:` YAML block on stdout;
`run-workflow.sh` appends that requirement to every agent prompt.

**Runtime agent routing (per run, no repo edit):**

Precedence per field: `agent.<role>.<field>=` CLI → `--agents-config` file → `routes.yaml`.

```bash
# YAML overlay (same agents: block as routes.yaml)
orchestrator run ORC-84 --agents-config ./agents.config.yaml
orchestrator run ORC-84 agents.config=./agents.config.yaml

# One-off field overrides (win over agents.config)
orchestrator run ORC-84 agent.developer.subprocess=cursor

# Replace full routes file for this run
orchestrator run ORC-84 --routes-override /path/to/routes.yaml
```

Example `agents.config.yaml` (see `scripts/agents.config.example.yaml`):

```yaml
agents:
  developer: { model: sonnet, subprocess: pi }
  reviewer:  { model: sonnet, subprocess: claude }
```

`agent.*` and `agents.config` are **not** written to `state.yaml`. Workflow flags like
`worktree=true` still go to seed-state.

Direct `run-workflow.sh`:

```bash
export ORCHESTRATOR_AGENTS_CONFIG=/path/to/agents.config.yaml
# optional fine-grained overrides on top of the file:
export ORCHESTRATOR_AGENT_ROUTE_OVERRIDES='{"developer":{"subprocess":"cursor"}}'
scripts/run-workflow.sh spec/changes/my-feat/state.yaml ORC-84
```

### Override semantics

All three config files support `.orchestrator/config/` repo overrides, which
take precedence over `$ORCHESTRATOR_HOME/config/` (global) and `config/`
(repo-level defaults):

```
Priority: .orchestrator/config/<file>  >  config/<file>  >  $ORCHESTRATOR_HOME/config/<file>
```

To override `tools.yaml` for a specific repo, create
`.orchestrator/config/tools.yaml` in that repo root. The file format is
identical to the global version; it replaces the global file entirely
(no merging).

### Exit code table

| Code | Meaning |
|------|---------|
| 1 | Workflow complete (`complete_workflow`) |
| 2 | Workflow blocked (including `spawn_failure_cap` after 3 zero-token spawn failures in one loop). A new `orchestrator run` clears that cap once and sets `status: in_progress` so the failed step retries; failures inside the same loop still trip the cap. |
| 3 | Contract error (`orchestrator next` exit 3) |
| 4 | Unknown agent role in `routes.yaml` |
| 5 | Malformed COMPLETION block from tool |
| 6 | Tool subprocess exited non-zero (or ticket halt) |
| 7 | Unexpected error (missing args, missing files, etc.) |

### Dependencies

`jq`, `yq`, `python3` (3.9+), `curl` (for ticket status check), and the
`orchestrator` CLI must be in `PATH`.

### Ticket-driven entry

When a ticket ID is passed, `run-workflow.sh` fetches the current ticket status via
`scripts/ticket-fetch-status.sh`, which reads `ticketing:` from `spec/project.yaml`
and queries the matching backend:

| `ticketing:` | Status source | Credential / tool |
|--------------|---------------|-------------------|
| `backlog` (default) | `backlog task view <id> --plain` | `backlog` CLI in `PATH` |
| `linear` | Linear GraphQL API | `LINEAR_API_KEY` env var |

If the ticket is **Done** or **Cancelled**, the run halts immediately. Otherwise,
routing is state-driven: if a `state.yaml` exists for the slug → resume; else → seed fresh.

When the backend is unavailable, the status fetch returns empty and the loop proceeds
with the existing `state.yaml`.

### Outbound ticket sync (workflow → ticket)

Outbound lane changes are explicit **workflow steps** (ORC-107), not a per-step hook:
`ticket-start` (→ In Progress, at implement entry), `ticket-review` (→ Code Review,
after `run-phase-review`), `ticket-qa` (→ QA Review, before `mark-change-completed`).
Each is a `kind: script` step that calls the backlog CLI. Agents do **not** need to
call `/backlog-manager` for lane changes when using the shell loop.

Each `ticket-*` step bakes in its target status (per backend). Transports:

- **backlog** — `backlog task edit <id> -s "<status>"` when the CLI is installed
- **linear** — the `ticket-sync-linear.sh` backend (GraphQL `issueUpdate`) is still
  available for the QA skills; the workflow `ticket-*` steps currently target backlog

### `state.yaml` ticket fields (shell loop only)

Written by `ticket-state-update.sh` (not `orchestrator done` / `record.py`):

| Field | Purpose |
|-------|---------|
| `ticket_id` | From `[TICKET-ID]` arg or `change_id` when it looks like `HL-123` / `task-42` |
| `ticket_status` | Last polled lane from the ticketing backend |
| `ticket_status_checked_at` | ISO 8601 UTC timestamp of last poll |
| `ticket_rework` | `true` when ticket moved from review lane back to `In Progress` |
| `ticketing` | `backlog` or `linear` (from `spec/project.yaml`) |
| `flags.rework_from_review` | Set by `ticket-reconcile.sh` on rework detection |

**Inbound poll:** `ticket-reconcile.sh` runs at the start of each `run-workflow.sh` loop
iteration (before `orchestrator next`) so external lane changes are visible on state
before the next agent dispatch.

**Outbound sync:** the `ticket-start` / `ticket-review` / `ticket-qa` workflow steps
push lane changes via the backlog CLI at their placements in the workflow `steps:` list.

### COMPLETION parsing

`scripts/parse-completion.py` extracts the `COMPLETION:` YAML block from agent
stdout and emits canonical JSON matching the `orchestrator done` payload shape.
Valid `status` values: `completed`, `recovered`, `abandoned`.
