---
feature-id: subprocess-per-step-observability
---

# Discovery Brief: Subprocess-Per-Step Observability

## Summary

The orchestrator runs all steps "inline" — the orchestrator's own LLM context does the work, with post-hoc JSONL mining to derive metrics. Empirical review of 5 archived state.yaml files confirmed every step ran with `agent: inline`, producing zero token counts, because Claude Code does not expose parent-context token counters to the running conversation. This caused the recent `cost_usd=0` and `per_agent_tool_uses` sparsity bugs.

The user's vision: the orchestrator is a thin YAML-driven dispatcher, nothing more. Each step is executed by an agent (any runtime — Claude session, Codex CLI, bash script, Python, HTTP client), which owns its own state update and its own metric reporting. Works outside Claude-native models.

**Converged design (after several iterations in discovery):**
- **One-verb CLI**: `orchestrator next <state.yaml>` — pure-read dispatcher returning JSON describing the next action. No subprocess spawning. No `record` verb.
- **Agent self-reports in state.yaml.** Marks in-progress, does work, appends a completed step_history entry with a `usage:` block, advances `next_step`.
- **OpenTelemetry GenAI semantic conventions** as the naming standard (at the DuckDB column layer; short names in state.yaml for readability).
- **DuckDB as the query layer.** CLI idempotently upserts completed step_history entries on every `next` call (single writer, keyed on repo/change/phase/step/attempt).
- **One proof adapter** migrates one real step (suggest `explore`) to demonstrate the full loop.

## Intent

Surface request: fix broken cost/metrics. Deeper goal: runtime-agnostic workflow execution — decouple step execution from the orchestrator's LLM context so any tool (or no LLM at all) can run a step. Make metrics a first-class output captured at write time, not mined from transcripts. Realize the repo's stated vision (`spec/project.yaml:6`, the `agent-agnostic` rule). The inline-execution model implicitly couples the orchestrator to Claude Code; subprocess-per-step via a language-neutral CLI + agent-authored state updates is the coherent design.

## Existing Solutions Surveyed

### Task runners and workflow engines
- **go-task / just / mise tasks** — YAML command runners. No state or metrics protocol.
- **Dagster Pipes** — closest protocol match (subprocess writes structured JSON back via a defined wire contract). Right shape, wrong framework (Python-heavy vs. bash/yq stack). **Borrow the JSON-on-file protocol; don't adopt the framework.**
- **Prefect / Airflow / Argo / Temporal** — server + DB dependencies. Overkill.
- **Stepwise** (github.com/zackham/stepwise) — closest overall: YAML flows, token/cost tracking, exit+JSON contract. Rejected: Python/SQLite runtime conflict, schema mismatch migrates all 45 contracts, ~9 stars.

**Verdict: build the CLI, borrow Dagster Pipes' JSON-on-file shape.**

### Metrics / observability standards
- **OpenTelemetry GenAI semantic conventions** — defines `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cost_usd`, model name, tool call structure. Status: Development (not yet Stable), but shipped by all major LLM-obs vendors. **Adopted** — zero cost, our fields map 1:1.
- **Langfuse / LangSmith / Phoenix / OpenLIT / Langtrace / Helicone** — all OTel-compatible. Not adopted now (no UI/collector requirement); path is clean later because we speak OTel.
- **MCP for observability** — no MCP server exists for this. Future opportunity; not this feature.
- **Claude/OpenAI usage APIs** — usage only in per-call response; no post-hoc query endpoint. Agents capture at call time; unavoidable.

**Verdict: adopt OTel GenAI names at the query layer. Short names in state.yaml; CLI maps to OTel names when upserting DuckDB.**

### Storage
- **DuckDB** — already in use (`~/.config/orchestrator/metrics.duckdb`); excellent OLAP, single-writer. Sufficient for one-workflow-at-a-time.
- **SQLite (WAL)** — considered for OLTP/concurrency. Deferred — user decision: stay on DuckDB; concurrency not near-term.

**Verdict: DuckDB, single-writer, documented. Existing `metrics-db-derived` learning intact.**

## Codebase Patterns & Constraints

- **45 step contracts** in `config/steps/*.yaml`. **14 declare an `agent:` field**; 31 are inline-only. Inline-only steps continue working as today (no `run:` → CLI returns "execute inline"). Incremental migration.
- **Tech stack** (`spec/project.yaml:77`): bash, zsh, yaml, duckdb, yq. Recent bugs (`cost_usd=0`, YAML timestamp corruption) originated from bash/yq interpolation quoting.
- **Existing subprocess patterns** to follow:
  - `config/scripts/compute-swe-metrics.sh` — state.yaml walk → DuckDB upsert, env var conventions.
  - `config/scripts/read-sub-state-metrics.sh` — state file locator.
  - `config/scripts/estimate-cost.sh` — env var plumbing.
- **state.yaml as source of truth** — `learnings/workflow-plan-upfront` (project.yaml:45): "Write full workflow_plan to state.yaml at init. Advance by reading state.yaml, never by reconstructing from schema in memory." Honored — `orchestrator next` reads state.yaml; agents write step_history entries.
- **Existing metrics derivation** — `learnings/metrics-db-derived` (project.yaml:54): DuckDB query plane with idempotent `INSERT OR REPLACE` keyed on `(repo_root, change_id)`. New `step_events` table extends the key to `(repo_root, change_id, phase, step_id, attempt)`. Same pattern.
- **Agent-agnostic rule** (project.yaml:135): schemas and step contracts must not reference any specific LLM tool. New `run:` field honors this — it names a command, not a model.

## Key Decisions

### 1. Build a thin CLI, don't adopt
~150-300 lines, bash or Python. Borrow Dagster Pipes' JSON-on-file shape. No off-the-shelf framework fits without more migration overhead than building.

### 2. One verb: `orchestrator next`
Pure-read dispatcher. No `record` verb. No subprocess spawning. Agents append step_history and advance state.yaml themselves. CLI upserts completed entries into DuckDB as a side effect of dispatch.

### 3. state.yaml is the single source of truth; DuckDB derives from it
Agents write only to state.yaml. CLI reads it, upserts `step_events` rows on every `next` call. DuckDB is a query cache, not a separate write target. Replayable: walk state.yaml files (active + archived) to rebuild.

### 4. OTel GenAI names at query layer, short names in state.yaml
state.yaml stays human-readable (`usage.input_tokens`, `usage.cost_usd`, `usage.tool_calls: {Read: N, Bash: M}`). DuckDB columns follow OTel (`gen_ai_usage_input_tokens`, etc.) for vendor compatibility.

### 5. Granularity = step (not span)
One `step_events` row per completed step_history entry. Sub-span detail (per-LLM-call, per-tool-call) deferred. Rollups = single `GROUP BY` over denormalized keys (`repo_root, change_id, phase, step_id, attempt, agent_name`).

### 6. Incremental migration
One reference step (suggest `explore`: LLM + tool calls, discoverer agent, exercises full loop). Remaining 44 contracts: current behavior until follow-up. No breaking changes.

### 7. DuckDB stays (not SQLite)
Single-writer limit accepted. `metrics-db-derived` learning authoritative. Concurrency deferred.

### 8. Context propagation via env vars
`orchestrator next` sets `ORCHESTRATOR_CHANGE_ID`, `ORCHESTRATOR_PHASE`, `ORCHESTRATOR_STEP_ID`, `ORCHESTRATOR_ATTEMPT`, `ORCHESTRATOR_WORKFLOW_DIR`, `ORCHESTRATOR_REPO_ROOT` when returning a step action. Agents inherit via exec. Dimension stamps on rows. Matches existing conventions.

## Use Cases

### UC-1 (happy path): Claude-native step runs end-to-end
Orchestrate skill calls `orchestrator next ~/.workflows/foo/state.yaml`. CLI returns `{action: "run_step", step_id, agent, instruction, rules, env: {...}}`. Skill:
1. Marks step `in_progress` in state.yaml (partial entry with `started_at`).
2. Spawns discoverer subagent with instruction + rules.
3. On return, extracts usage from result summary.
4. Appends completed step_history entry (status, ended_at, usage{input_tokens, output_tokens, cost_usd, tool_calls{}}, artifacts[]).
5. Advances `next_step`.
6. Calls `orchestrator next` again — CLI upserts the entry into DuckDB, returns next step.

### UC-2: Non-Claude adapter runs a step (designed-for future)
Contract declares `run: scripts/claude-cli-adapter.sh`. Skill (or bash loop, or CI) sets env vars, execs. Adapter invokes `claude -p`, captures usage via `ccusage`, writes step_history entry in the same shape, advances state.yaml. CLI sees the same shape regardless of runtime.

### UC-E1: Agent crashes mid-step
Agent dies after `in_progress` but before completion. `orchestrator next` sees `step_history[-1]` in_progress without `ended_at`. Retry: `{action: "retry_step", step_id, attempt: 2, previous_failure: "no ended_at"}`. Caller reruns. DuckDB upsert idempotent on `(repo, change, phase, step_id, attempt)`.

### UC-E2: Phase verify fails
Steps complete → CLI evaluates `verify:` block. On failure: `{action: "verify_failed", phase, failures, max_retries, attempts_remaining}`. Caller decides re-run / escalate / abort. CLI doesn't mutate phase on failure.

### UC-E3: Workflow complete
All phases complete → `{action: "complete_workflow"}` with exit 1. Caller writes `status: completed`, archives.

### UC-E4: Inline-only step (no `run:` field — migration not yet)
No `run:` → CLI returns `{action: "run_inline", step_id, instruction, rules}`. Caller runs inline (current behavior). Tokenless (Claude Code limitation). step_history `agent: inline`, no usage block. DuckDB row with nulls. No regression.

### UC-E5: Agent signals escalate_to_architect
Developer hits a design question per `contracts/architect-escalation.md` and appends a step_history entry with `status: escalate_to_architect` plus an `escalation:` sub-block (type, task_id, context, question, attempted). `orchestrator next` returns `{action: blocked, reason: escalate_to_architect, escalation: {...}}` with exit 2. Caller spawns the architect per the existing escalation protocol; developer re-spawns at same `attempt` (no retry charged); the second terminal entry at the same (phase, step_id, attempt) has `status: completed` — two `step_events` rows preserve the audit trail via the composite PK.

## Scope

### In Scope
- `orchestrator next` CLI (one verb, pure read)
- `step_events` DuckDB table with denormalized rollup keys and OTel column names
- CLI upserts completed step_history entries on every `next`, idempotent
- state.yaml `step_history[]` usage block schema (short names, documented)
- One reference step migration (`explore`) with a `run:` adapter demonstrating the loop end-to-end
- Migration path for remaining steps (documented, not implemented)
- A telemetry query demonstrating `step_events` rollup at feature/phase/step levels
- TDD: CLI unit tests (fixtures state.yaml → expected JSON; upsert idempotency)
- Docs: agent-author guide, migration guide

### Out of Scope
- Parallel/concurrent step execution (single-writer constraint accepted)
- Live telemetry dashboards polling DuckDB mid-workflow
- Sub-span granularity
- Migrating all 45 step contracts
- Non-Claude adapter implementations (contract stable; impl is future)
- Deleting `compute-swe-metrics.sh` (legacy fallback)
- Backfilling historical archives into `step_events`
- MCP server for observability

## Unresolved Questions

**OQ-1 — Driver language: bash or Python?**
Bash matches stack; Python safer for YAML/JSON (recent bugs from bash quoting). ~200-400 lines either way. **Architect decides.**

**OQ-2 — Who sets `attempt`?**
On retry, does (a) CLI count in-progress/failed entries and return `attempt`, or (b) agent read env var and write? **(a) keeps agent dumber.** Architect decides.

**OQ-3 — Escalation channel**
How does an agent signal "escalate to architect" / "block"? Existing `contracts/error-recovery.md` and `architect-escalation.md` define patterns. Options: (a) exit code convention; (b) status field in step_history entry. **(b) cleaner** — state.yaml is source of truth.

**OQ-4 — Phase-verify execution locus**
CLI runs `verify:` commands (breaks pure-read) or returns `{action: verify_phase, commands, assertions}` to caller? **Latter is cleaner.** Architect confirms.

**OQ-5 — Stalled `step-self-reported-metrics` workflow**
Aborted phase-1 scaffold at `~/.workflows/step-self-reported-metrics/state.yaml`. This feature supersedes it. Design declares supersede; user cleans up stale file manually (sandbox prevented removal).

## Technical Context

- Step contracts: `/Users/spidey/code/orchestrator/config/steps/*.yaml` — 45 files, 14 with `agent:`
- Reference scripts:
  - `config/scripts/compute-swe-metrics.sh` — state.yaml parsing, DuckDB upsert
  - `config/scripts/estimate-cost.sh` — env var conventions
- Contracts:
  - `config/steps/contracts/metrics-schema.md`
  - `config/steps/contracts/error-recovery.md`
  - `config/steps/contracts/architect-escalation.md`
- Existing env vars: `ORCHESTRATOR_HOME`, `WORKFLOW_STATE_DIR`, `REPO_ROOT`, `CHANGE_ID`
- New env vars: `ORCHESTRATOR_CHANGE_ID`, `ORCHESTRATOR_PHASE`, `ORCHESTRATOR_STEP_ID`, `ORCHESTRATOR_ATTEMPT`, `ORCHESTRATOR_WORKFLOW_DIR`, `ORCHESTRATOR_REPO_ROOT`
- DuckDB location: `~/.config/orchestrator/metrics.duckdb`
- New table: `step_events` (schema in design.md)

## Design decisions inherited by architect (not open for re-litigation)

1. **One-verb CLI** (`orchestrator next`) — user-confirmed.
2. **Agent writes state.yaml directly** — no `record` verb — user-confirmed.
3. **OTel GenAI naming at DuckDB layer** — user-confirmed.
4. **DuckDB (not SQLite)** — user-confirmed; single-writer accepted.
5. **One reference step migration, not all 45** — user-confirmed.
6. **No MCP server / no OTel collector / no external vendor** — phase 1 self-contained. OTel naming preserves future options.

Architect focuses on the 5 OQs above plus task decomposition.
