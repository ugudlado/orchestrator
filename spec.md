---
feature-id: subprocess-per-step-observability
---

# Specification: Subprocess-Per-Step Observability

## Motivation

The orchestrator currently runs every step "inline" — the orchestrator's own LLM
context does the work and metrics are mined post-hoc from Claude Code session
JSONL files. Empirical review of 5 recent archives confirmed every step ran with
`agent: inline` producing zero token counts at the step level, because Claude
Code does not expose parent-context token counters to the running conversation.
This directly caused the `cost_usd=0` and `per_agent_tool_uses` sparsity bugs
fixed in the `fix-cost-usd-and-widen-token-split` bugfix, which were treated
as patches rather than the structural problem they signal.

The deeper issue is that the inline-execution model implicitly couples the
orchestrator to Claude Code, violating the repo's `agent-agnostic` rule
(`spec/project.yaml:135`) and the stated vision of an LLM-agnostic configuration
layer (`spec/project.yaml:6`). Step execution and metric capture must be
decoupled from the orchestrator's own LLM context so any runtime — Claude
session, Codex CLI, bash script, Python, HTTP client — can execute a step and
self-report usage at write time.

## Background / Context

- **Vision alignment**: realises the `agent-agnostic` rule — step contracts
  already declare an `agent:` name, not a tool. Adding a `run:` command field
  completes the abstraction: the orchestrator never spawns LLMs itself, only
  dispatches to named commands.
- **Empirical finding**: 5 recent archived `state.yaml` files show 100% of
  `step_history` entries are `agent: inline`. Token counts per step are always
  0. Feature-level totals come solely from post-hoc JSONL mining. Any step that
  needs per-step cost attribution is blocked until this is fixed.
- **Supersedes**: this feature supersedes the stalled
  `~/.workflows/step-self-reported-metrics/` workflow (phase-1 scaffold only;
  user will remove the stale state.yaml manually after signoff).
- **Reuses**: the existing `metrics-db-derived` learning (DuckDB, idempotent
  `INSERT OR REPLACE`, `sql_quote` escaping) is honoured — the new
  `step_events` table sits alongside `features` in the same `metrics.duckdb`.

## What Changes

1. A new `orchestrator next <state.yaml>` CLI — pure-read dispatcher returning
   a deterministic JSON action for the caller to execute.
2. A new `step_events` table in `metrics.duckdb` with OTel GenAI column names,
   populated idempotently by `orchestrator next` as a side effect of dispatch.
3. The `step_history[]` entry schema gains a `usage:` sub-block with short
   field names (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
   `cost_usd`, `tool_calls: {Name: N}`, `duration_ms`) and new `status` values
   (`in_progress`, `escalate_to_architect`, `blocked`).
4. One reference step (`explore`) migrates to the new path via a `run:` adapter
   — this is the proof that the full loop works end-to-end with real token
   capture.
5. A migration guide documents how remaining 44 step contracts adopt the new
   path incrementally.

## Requirements

### Functional

1. **FR-1**: `orchestrator next <state.yaml>` MUST be pure-read — it reads the
   provided state.yaml and writes only to `metrics.duckdb`. It MUST NOT mutate
   state.yaml and MUST NOT spawn subprocesses.
2. **FR-2**: For a given `state.yaml`, `orchestrator next` MUST return a
   deterministic JSON response. Two calls with the same input produce byte-identical
   output (excluding timestamps that are themselves deterministic from the input).
3. **FR-3**: The JSON response MUST declare one of: `run_step`, `run_inline`,
   `retry_step`, `verify_phase`, `complete_workflow`, `blocked`. (Six actions.
   `advance_phase` was considered and removed.) The CLI dispatches within the
   current `state.yaml.phase` — it is phase-scoped. The caller is responsible
   for updating `state.yaml.phase` to the next phase once phase-verify passes;
   the next `orchestrator next` call will then return the first step of the
   newly-current phase. Multi-phase advancement is the caller's
   responsibility, not the CLI's; this keeps the CLI pure-read and
   single-responsibility.
4. **FR-4**: When `action ∈ {run_step, run_inline, retry_step}`, the response
   MUST include an `env` object with keys `ORCHESTRATOR_CHANGE_ID`,
   `ORCHESTRATOR_PHASE`, `ORCHESTRATOR_STEP_ID`, `ORCHESTRATOR_ATTEMPT`,
   `ORCHESTRATOR_WORKFLOW_DIR`, `ORCHESTRATOR_REPO_ROOT`.
5. **FR-5**: Every completed `step_history` entry present in state.yaml MUST be
   upserted into `step_events` on each `orchestrator next` call, keyed on
   `(repo_root, change_id, phase, step_id, attempt)`.
6. **FR-6**: The `step_events` table MUST use OpenTelemetry GenAI semantic
   convention column names for usage fields (`gen_ai_usage_input_tokens`,
   `gen_ai_usage_output_tokens`, `gen_ai_usage_cache_read_input_tokens`,
   `gen_ai_usage_cost_usd`, `gen_ai_request_model`).
7. **FR-7**: `state.yaml` `step_history[]` entries MUST use short, human-readable
   field names under `usage:` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
   `cost_usd`, `tool_calls: {Name: N}`, `duration_ms`). The CLI maps short names
   to OTel column names at upsert time.
8. **FR-8**: When `step_history[-1].status == in_progress` with no `ended_at` and
   no matching `completed` entry later, `orchestrator next` MUST return
   `action: retry_step` with `attempt = N+1` where N is the highest existing
   attempt for that `(phase, step_id)`.
9. **FR-9**: When a step declares `run:` in its contract,
   `orchestrator next` MUST return `action: run_step` with `run`, `agent`,
   `instruction`, `rules`, `env`. When it does not, the response MUST be
   `action: run_inline` with `instruction`, `rules`, `env` (no `run` field).
10. **FR-10**: When all phases complete, `orchestrator next` MUST return
    `action: complete_workflow` and exit 1.
11. **FR-11**: When a phase's steps are all `completed` but the phase's `verify:`
    block has not been evaluated, `orchestrator next` MUST return
    `action: verify_phase` with `commands[]` and `assertions[]` copied from the
    phase contract — the caller runs them and reports results by appending a
    `run-phase-review` step_history entry.
12. **FR-12**: When a `step_history` entry has `status: escalate_to_architect`
    or `status: blocked` with non-exhausted retries, `orchestrator next` MUST
    return `action: blocked` with the escalation context and exit 2 — it MUST
    NOT advance.
13. **FR-13**: The reference step (`explore`) MUST complete end-to-end via its
    `run:` adapter producing a `step_events` row with non-null
    `gen_ai_usage_input_tokens`, `gen_ai_usage_output_tokens`, and
    `gen_ai_usage_cost_usd`.
14. **FR-14**: Inline-only steps (contract has no `run:`) MUST continue to work.
    Their `step_events` rows carry `agent_name = 'inline'` and nullable usage
    columns (zero or NULL tokens/cost).

### Non-Functional

1. **NFR-1**: The CLI MUST be idempotent. Running `orchestrator next` twice in
   succession on the same state.yaml MUST produce a single `step_events` row
   per `(repo_root, change_id, phase, step_id, attempt, status)` tuple, not two.
   Note: the 6-column key (not 5) is deliberate — an architect escalation
   legitimately produces two terminal entries at the same
   `(phase, step_id, attempt)`, distinguished by `status`
   (`escalate_to_architect` then `completed`). See design.md § DuckDB
   step_events Table, Note on `status` in the PK.
2. **NFR-2**: DuckDB writes MUST follow the `metrics-db-derived` learning:
   `INSERT OR REPLACE`, `sql_quote`-escaped interpolation, change_id slug
   guard before any INSERT.
3. **NFR-3**: The CLI MUST NOT require a heavy setup step. Installation is a
   symlink (matching `install.sh` conventions) or a shebang-invokable script.
4. **NFR-4**: Token / feature / phase / step rollups MUST be achievable via a
   single `GROUP BY` query over `step_events` — no post-processing required.
5. **NFR-5**: The CLI MUST be agent-agnostic. No contract or column may
   reference Claude, Codex, or any specific LLM tool by name.

## Architecture

High-level (full detail in design.md):

```
┌─────────────────────┐
│ Caller (skill/CLI)  │
└──────────┬──────────┘
           │ orchestrator next state.yaml
           ▼
┌─────────────────────┐        ┌──────────────────┐
│ orchestrator next   │ reads  │  state.yaml      │
│ (pure-read driver)  │◄───────┤  (source truth)  │
│                     │ upsert └──────────────────┘
│                     │─────┐
└──────────┬──────────┘     ▼
           │                ┌──────────────────┐
           │ JSON action    │  metrics.duckdb  │
           ▼                │   step_events    │
┌─────────────────────┐     └──────────────────┘
│ Caller executes:    │
│  - run_step (exec   │
│    contract.run)    │
│  - run_inline       │
│  - verify_phase     │
│  - retry_step       │
│  - complete_workflow│
│  - blocked          │
└─────────────────────┘
```

| File / Path | Action | Purpose |
|-------------|--------|---------|
| `bin/orchestrator` | create | CLI entry point (Python, shebang-invoked, no bash wrapper) |
| `config/scripts/orchestrator_next/__init__.py` | create | Package marker |
| `config/scripts/orchestrator_next/parser.py` | create | state.yaml + step contract parser, YAML-safe |
| `config/scripts/orchestrator_next/dispatch.py` | create | Pure function State → action JSON |
| `config/scripts/orchestrator_next/upsert.py` | create | DuckDB DDL + INSERT OR REPLACE |
| `config/scripts/orchestrator_next/otel_map.py` | create | Short → OTel column mapping |
| `config/scripts/tests/` | create | Fixture-driven CLI tests |
| `config/steps/explore.yaml` | modify | Add `run:` adapter field |
| `config/scripts/adapters/claude_discoverer.py` | create | Reference adapter for `explore` |
| `config/steps/contracts/metrics-schema.md` | modify | Document short names under `usage:` and `step_events` table |
| `config/steps/contracts/step-dispatch.md` | create | CLI interface contract + JSON schema |
| `config/steps/contracts/migration-run-field.md` | create | Migration guide for remaining steps |

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/` → `config/scripts/tests/test_orchestrator_next.py`
- DuckDB upsert logic → `config/scripts/tests/test_step_events_upsert.py`
- Reference adapter end-to-end → `config/scripts/tests/test_explore_adapter.sh`
- Fixtures → `config/scripts/tests/fixtures/state-*.yaml`

### Coverage Targets

- CLI dispatch logic (all 7 action types): 100% branch coverage.
- DuckDB upsert idempotency: explicit test per UC-E1.
- Fixture-driven golden tests: one fixture per action type + one per UC.

### Key Test Scenarios

1. `next(state-pending.yaml)` → `{action: run_step, step_id, ...}` JSON matches
   golden file.
2. `next(state-inline-only.yaml)` → `{action: run_inline, ...}` (no `run`
   field).
3. `next(state-in-progress-no-ended.yaml)` → `{action: retry_step, attempt: 2, ...}`.
4. `next(state-phase-done.yaml)` → `{action: verify_phase, commands, assertions}`.
5. `next(state-all-done.yaml)` → `{action: complete_workflow}` (exit 1).
6. `next(state-escalate.yaml)` → `{action: blocked, reason: escalate_to_architect}` (exit 2).
7. **Idempotency**: call `next` twice on a fixture with one completed entry →
   `SELECT COUNT(*) FROM step_events WHERE ... = 1`.
8. **UC-E1 crash/retry**: step_history has an `in_progress` without `ended_at`
   followed by a later `completed` with `attempt: 2`. `step_events` has both
   rows; token totals reflect only `attempt: 2`.
9. **Reference step**: running the `explore` step via its adapter in a scratch
   workflow produces a `step_events` row with `gen_ai_usage_input_tokens > 0`
   and `gen_ai_usage_cost_usd > 0`.

## User Scenarios

### UC-1 — Happy path: runtime-owned step runs end-to-end
Orchestrate skill calls `orchestrator next state.yaml`. CLI returns
`{action: run_step, step_id: explore, run: "scripts/adapters/claude_discoverer.py",
agent: discoverer, instruction, rules, env}`. Caller:
1. Writes a partial `step_history[]` entry `status: in_progress` with `started_at`.
2. Execs the `run:` command with `env` set.
3. Adapter spawns the runtime (e.g., Claude session), captures usage at call
   time (via ccusage or equivalent).
4. Adapter appends a completed `step_history[]` entry with `usage:
   {input_tokens, output_tokens, cache_read_input_tokens, cost_usd,
   tool_calls: {Read: N, Bash: M}, duration_ms}`, `status: completed`,
   `ended_at`, `agent: discoverer`, `artifacts: [...]`.
5. Caller calls `orchestrator next` again — CLI upserts the completed entry
   into `step_events` and returns the next action.

### UC-2 — Non-Claude adapter (designed-for, not implemented here)
Contract declares `run: scripts/adapters/codex-adapter.sh`. Caller execs with
the same env contract. Adapter invokes `codex exec`, captures usage,
writes step_history in the same shape. CLI sees the same shape regardless of
runtime.

### UC-E1 — Agent crashes mid-step
Agent writes `status: in_progress` but dies before writing the completion.
`orchestrator next` sees `step_history[-1]` has no `ended_at`. Returns
`{action: retry_step, step_id, attempt: 2, previous_failure: "no ended_at"}`.
Caller reruns. DuckDB upsert is idempotent on the primary key — no duplicates.

### UC-E2 — Phase verify fails
All steps in a phase are `status: completed`. CLI returns
`{action: verify_phase, commands, assertions}`. Caller runs them and appends a
`run-phase-review` step_history entry. If that entry is `status: failed`, CLI
on the next call returns `{action: retry_step, step_id: run-phase-review,
attempt: 2}` (bounded by `max_retries`). CLI does not mutate phase state on
failure.

### UC-E3 — Workflow complete
All phases' steps completed and all phase verifies passed. `orchestrator next`
returns `{action: complete_workflow}` with exit 1. Caller writes
`status: completed`, archives.

### UC-E4 — Inline-only step (migration not yet done)
Contract has no `run:` field. CLI returns `{action: run_inline, step_id,
instruction, rules, env}`. Caller runs inline (current behaviour). step_history
entry has `agent: inline`; no `usage:` fields or zero/null values. DuckDB row
with null tokens/cost. No regression from current behaviour.

## Acceptance Criteria

- **AC-1**: `orchestrator next` is pure-read. For any input state.yaml, running
  it twice produces byte-identical JSON output and the state.yaml file is
  unchanged (mtime unchanged). **[traces: FR-1, FR-2, UC-1]**
- **AC-2**: For each of the 6 fixture state.yamls (pending/inline/in-progress/
  phase-done/all-done/escalate), `orchestrator next` returns JSON matching the
  committed golden file. **[traces: FR-3, UC-1, UC-E3, UC-E4]**
- **AC-3**: Running `orchestrator next` on a state.yaml with one completed
  step_history entry, twice, yields exactly one row in `step_events` for that
  key tuple. **[traces: NFR-1, FR-5, UC-E1]**
- **AC-4**: Every row in `step_events` has non-null `repo_root`, `change_id`,
  `phase`, `step_id`, `attempt`, `agent_name`. **[traces: FR-5, FR-6, UC-1]**
- **AC-5**: A single `GROUP BY change_id, phase` query over `step_events`
  returns correct phase-level token/cost totals across test fixtures.
  **[traces: NFR-4, UC-1]**
- **AC-6**: The reference `explore` step, run end-to-end via its `run:`
  adapter in a scratch workflow, produces a `step_events` row with
  `gen_ai_usage_input_tokens > 0`, `gen_ai_usage_output_tokens > 0`, and
  `gen_ai_usage_cost_usd > 0`. **[traces: FR-13, UC-1]**
- **AC-7**: An inline-only step (no `run:`) produces a `step_events` row with
  `agent_name = 'inline'`; token columns may be NULL or 0 without error.
  **[traces: FR-14, UC-E4]**
- **AC-8**: A fixture with `step_history[-1].status = in_progress` and no
  `ended_at` causes `orchestrator next` to return `action: retry_step` with
  `attempt: 2`. The upsert is idempotent when the retry completes.
  **[traces: FR-8, UC-E1]**
- **AC-9**: A fixture with `step_history[-1].status = escalate_to_architect`
  causes `orchestrator next` to return `action: blocked, exit 2` without
  advancing. **[traces: FR-12, UC-E5]**
- **AC-10**: All inline-only step contracts (those without a `run:` field — 44 at this feature's implementation time) run unchanged through
  `orchestrator next` in a smoke test (response is `run_inline`, no errors).
  **[traces: FR-14, UC-E4]**

## Alternatives Considered

**Alternative 1: Adopt Stepwise (github.com/zackham/stepwise).**
Rejected. Python/SQLite runtime conflict; schema mismatch would force migrating
all 45 contracts up front; ~9 stars — thin community.

**Alternative 2: Adopt Dagster Pipes framework.**
Rejected. Right protocol shape (JSON-on-file) but Python-heavy framework
overhead vs. the bash/yq/duckdb stack. **Borrow the JSON-on-file shape,
don't adopt the framework.**

**Alternative 3: Multi-verb CLI (`next`, `record`, `fail`, `complete`).**
Rejected by user. State.yaml is already the source of truth; adding verbs that
duplicate state transitions creates two paths where one suffices. One verb is
enough.

**Alternative 4: Keep inline execution, improve JSONL mining.**
Rejected. Root cause is structural — Claude Code does not expose parent-context
counters. Any inline-only solution is permanently capped at post-hoc mining
and remains Claude-specific.

## Impact

- **No breaking changes** to state.yaml consumers: short names live alongside
  existing fields; consumers that read the old `total_tokens`/`tool_uses` form
  continue to work during transition.
- **No breaking changes** to existing step contracts: absence of `run:` is the
  inline path; all inline-only contracts (44 at implementation time) are unaffected.
- **New dependency**: Python 3 (stdlib + PyYAML) for the driver — justified in
  design.md OQ-1.
- **Documented migration path** for remaining 44 step contracts — incremental,
  not required by this feature.

## Decisions

See design.md § Decisions for the 5 open questions resolved (driver language,
attempt assignment, escalation channel, phase-verify locus, supersede of
`step-self-reported-metrics`).

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
