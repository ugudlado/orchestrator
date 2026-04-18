# Design: Subprocess-Per-Step Observability

## Context

The orchestrator is a YAML-driven workflow engine. Today it executes every step
inline inside its own LLM context; the agent footer reports tool calls but not
tokens because Claude Code does not expose parent-context token counters. The
existing DuckDB `features` table (written by `compute-swe-metrics.sh` at
archive time) is the query plane for cross-feature metrics, but it aggregates
only at the feature level, derived from state.yaml + post-hoc JSONL mining.

This design introduces a thin dispatcher CLI (`orchestrator next`) that makes
per-step execution runtime-agnostic and per-step observability first-class.
state.yaml remains the single source of truth. DuckDB gains a new `step_events`
table keyed on the step granularity, derived idempotently from state.yaml on
every dispatch. One reference step migrates to the new path as proof.

## Goals / Non-Goals

### Goals
- A single-verb CLI (`orchestrator next`) that is pure-read, deterministic, and
  fixture-testable.
- A `step_events` DuckDB table keyed on `(repo_root, change_id, phase, step_id,
  attempt)` with OpenTelemetry GenAI column names, populated idempotently.
- A human-readable `usage:` block shape on `step_history[]` entries using short
  field names.
- A single reference step (`explore`) migrated end-to-end to demonstrate real
  token/cost capture.
- Zero regression for inline-only step contracts (44 at implementation time; contract count is informational, not a hard requirement).
- A documented migration path for remaining steps.

### Non-Goals
- Parallel / concurrent step execution (single-writer DuckDB accepted).
- Live telemetry dashboards or mid-workflow polling.
- Sub-span granularity (per-LLM-call, per-tool-call rows).
- Migrating any step other than `explore`.
- Implementing non-Claude adapters.
- Deleting `compute-swe-metrics.sh` (legacy fallback).
- An MCP server or OTel collector.
- Backfilling historical archives into `step_events`.

## Decisions

The five open questions from the discovery brief are resolved here.

### OQ-1 — Driver language: **Python 3 (stdlib + PyYAML)**

**Decision**: Write the driver in Python 3 as a single entry-point file,
`bin/orchestrator` (shebang-invoked, no wrapper). All support modules are
Python. Adapters for steps are also Python — bash is not used anywhere this
feature introduces new code. User direction: Python covers what previously
would have been split across bash/zsh/Python; shrink the stack to what's
needed, not layer on top of it.

**Rationale**:
- Recent project bugs (`cost_usd=0`, YAML timestamp corruption in the
  `fix-cost-usd-and-widen-token-split` change) traced back to bash/yq
  interpolation — state.yaml is the single source of truth and bash quoting
  is structurally unsafe for the parse-mutate-emit loop the dispatcher needs.
- Python stdlib `json` + `yaml.safe_load` is the minimum-surface safe path
  for reading state.yaml and emitting JSON on stdout.
- Python's `duckdb` package installs cleanly via `pip install --user duckdb`
  and is already used indirectly via the `duckdb` CLI.
- Bash would save ~50 lines in the wrapper but cost ~3 hours the next time a
  YAML edge case surfaces.

**Mitigations for stack drift**:
- No venv. Requirements documented at top of entry-point script:
  `#!/usr/bin/env python3` + `# requires: pyyaml, duckdb` as a comment.
- `install.sh` adds a one-line pip install step gated on `python3 -c "import yaml"` passing.
- Unit tests run via `python3 -m unittest` — no pytest dependency.

**Consequence**: Python replaces bash for anything new this feature
introduces — the CLI, its internal modules, and the reference adapter. The
existing bash scripts (`compute-swe-metrics.sh`, `estimate-cost.sh`, etc.)
stay as-is; no rewrite is in scope. Net effect on `project.yaml:77`
`tech_stack`: add `python3`. Follow-up can migrate other scripts to Python
if they hit similar YAML/jq quoting issues.

### OQ-2 — Who sets `attempt`: **the CLI computes it, returns it in JSON**

**Decision**: `orchestrator next` scans `step_history[]` for the current
`(phase, step_id)` pair, counts existing entries with matching phase+step_id,
sets `attempt = N + 1`, and includes it in the JSON response. The agent
writes the returned value verbatim into the `step_history[]` entry.

**Rationale**:
- Keeps the agent dumber — agents don't need a read-modify-write loop on
  state.yaml to determine `attempt`. They receive it as an input.
- Matches the pure-read property — CLI computes from state.yaml, doesn't
  mutate anything. Agent just writes what it was told.
- Centralises the attempt-assignment rule in one code path, testable via
  fixture state.yamls.

**Consequence**: Agents treat `attempt` as opaque data from the CLI's JSON
response. This is already how env-propagated context works.

### OQ-3 — Escalation channel: **`status` field in step_history entry**

**Decision**: Agents signal escalation and blocking via `status` values in
the step_history entry they append:
- `status: escalate_to_architect` — developer hit a design question per
  `contracts/architect-escalation.md`. CLI returns `action: blocked` with
  `reason: escalate_to_architect` and the escalation block from the entry.
- `status: blocked` — agent cannot proceed for reasons per
  `contracts/error-recovery.md` § Agent Blocked Protocol. CLI returns
  `action: blocked` with `reason: blocked`.

Exit codes remain: 0 = action returned, 1 = workflow complete, 2 = blocked,
3 = error. The exit code mirrors the JSON `action`; the JSON is the canonical
signal.

**Rationale**:
- state.yaml is already the source of truth — the status field already
  exists; adding two new enum values is minimal.
- Keeps the CLI pure-read. Exit codes alone would be lossy — callers need
  the escalation block to know which architect question to raise.
- Composes with the existing `contracts/architect-escalation.md` protocol:
  the agent's output-format block (STATUS/type/context/question/attempted)
  gets copied into the step_history entry; the CLI surfaces it in the JSON
  `blocked` response so the orchestrator knows to spawn the architect.

**Consequence**: `contracts/error-recovery.md` state-transition table grows
two rows:
- `status: escalate_to_architect` → CLI returns `action: blocked` with
  escalation block; orchestrator spawns architect per
  `contracts/architect-escalation.md`.
- `status: blocked` → CLI returns `action: blocked` with blocker context;
  orchestrator applies § Agent Blocked Protocol (re-spawn once, then treat as
  failure).

### OQ-4 — Phase-verify execution locus: **CLI returns the action; caller runs**

**Decision**: When a phase's steps are all terminal and the phase has a
`verify:` block, `orchestrator next` returns
`{action: verify_phase, phase, commands: [...], assertions: [...],
max_retries: N, attempts_used: K}`. The caller runs the commands, evaluates
assertions, and reports by appending a `run-phase-review` step_history entry
with `status: completed` (pass) or `status: failed` (fail). On failure the
CLI on the next call returns `action: retry_step` for `run-phase-review`
until `attempts_used >= max_retries`, then `action: blocked`.

**Rationale**:
- Preserves the pure-read property. Running shell commands from within the
  CLI would make it side-effectful and much harder to fixture-test.
- Matches existing pattern — `run-phase-review` is already a step that runs
  verify commands; the CLI just dispatches to it.
- Keeps verification centralised in the agent/skill layer where retry and
  escalation logic already live.

**Consequence**: Callers must understand the `verify_phase` action type.
Documented in `contracts/step-dispatch.md`.

**Phase-scoped dispatch clarification**: The CLI dispatches within the
current `state.yaml.phase` value only. Phase-cursor advancement is the
caller's responsibility, not the CLI's. The caller's sequence for crossing
a phase boundary is: (1) run `verify_phase` action; (2) append
`run-phase-review` step_history entry with terminal status; (3) if passed,
update `state.yaml.phase` to the next phase; (4) call `orchestrator next`
again, which now dispatches within the new phase. The CLI never rewrites
`state.yaml.phase` — this preserves the pure-read property and keeps the
dispatcher single-responsibility. When the last phase completes, the CLI
returns `action: complete_workflow` (exit 1).

### OQ-5 — Stalled `step-self-reported-metrics` workflow: **declared superseded**

**Decision**: This feature's spec.md (§ Background / Context) declares the
stalled `~/.workflows/step-self-reported-metrics/` workflow superseded. The
user removes the stale state.yaml manually (sandbox prevents automated
cleanup from this worktree).

**Rationale**: The stalled workflow was an earlier attempt at the same goal
without the dispatcher contract. Keeping it around would confuse future
readers of state directories. User-owned cleanup avoids automated
cross-workflow deletion — a principle this codebase holds.

**Consequence**: No automation. The `Impact` section of spec.md flags the
manual step. A post-signoff checklist item reminds the user.

## Approaches Considered

### Approach 1: Multi-verb CLI (`next`, `record`, `fail`, `complete`)
Rejected. Duplicates state ownership — state.yaml already expresses everything
needed. More verbs means more commands to test, document, and keep in sync.

### Approach 2: CLI spawns subprocesses itself
Rejected. Combines dispatch and execution; makes the CLI hard to test with
fixture state.yaml files. Pure-read keeps tests deterministic.

### Approach 3: Keep inline execution, improve JSONL mining
Rejected. Doesn't address the structural coupling to Claude Code or the
zero-token problem at step granularity.

### Selected Approach: Single-verb `orchestrator next`, pure-read, JSON response + DuckDB side effect

Fixture-testable. Agent-owned state writes. Single source of truth unchanged.
Minimal surface area (~300 lines Python).

## High-Level Design

### Architecture Overview

```
Caller (skill, script, or CI)
         │
         │ orchestrator next state.yaml
         ▼
┌───────────────────────────┐       reads       ┌──────────────┐
│  orchestrator next (py)   │◄──────────────────│  state.yaml  │
│                           │                   └──────────────┘
│  1. parse state.yaml      │
│  2. upsert terminal       │   INSERT OR       ┌──────────────┐
│     step_history[] into   │──REPLACE─────────►│ metrics.duck │
│     step_events           │                   │  .step_events│
│  3. compute next action   │                   └──────────────┘
│  4. emit JSON             │
└───────────┬───────────────┘
            │ JSON action on stdout, exit code
            ▼
Caller executes next action (run_step/run_inline/verify_phase/...)
and, for run_step/run_inline, appends a step_history entry when done.
```

### State Transitions (step lifecycle)

```
         ┌──────────┐
         │ pending  │  (no step_history entry for this step_id yet)
         └────┬─────┘
              │ agent starts work — writes partial entry
              ▼
         ┌──────────┐
         │ in_progr │  (has started_at, no ended_at)
         └────┬─────┘
              │
     ┌────────┼────────┬──────────────────┐
     ▼        ▼        ▼                  ▼
 completed  failed  blocked      escalate_to_architect
     │        │        │                  │
     │        │        │                  │ orchestrator spawns architect
     │        │        │                  │ (per architect-escalation.md)
     │        │        │                  ▼
     │        │        │          developer re-spawned with DECISION
     │        │        │          appended, same (phase, step_id),
     │        │        │          SAME attempt (no retry charged)
     │        │        └──► re-spawn once with blocker context (attempt++
     │        │             only after second blocked result)
     │        └─────► CLI returns retry_step with attempt = N+1
     └──────► CLI advances to next step_id
```

### Key Abstractions

- **State.yaml as queue**: `step_history[]` is the audit log; `next_step` (or
  inferred from workflow_plan + step_history) is the head.
- **Dispatch dictionary**: a pure function `(state.yaml) -> action` with no
  side effects on state.yaml.
- **DuckDB as cache**: every terminal step_history entry has a corresponding
  `step_events` row, idempotently re-derived on every dispatch.

## Low-Level Design

### CLI Interface

**Invocation**: `orchestrator next <path-to-state.yaml>`

**Exit codes**:
- `0` — an action was returned (`run_step`, `run_inline`, `retry_step`,
  `verify_phase`)
- `1` — workflow complete (`complete_workflow`)
- `2` — blocked (`blocked`)
- `3` — CLI error (invalid state.yaml, schema unknown, etc.)

**JSON response schema** (on stdout, one line, pretty-printing optional for tty):

```jsonc
{
  "action": "run_step",               // required; one of the 7 values
  "step_id": "explore",               // required for action ∈ {run_step, run_inline, retry_step}
  "phase": "specify",                 // required for most actions
  "attempt": 1,                       // CLI-computed; required for run_step/run_inline/retry_step
  "agent": "discoverer",              // from step contract
  "run": "scripts/adapters/claude_discoverer.py",  // only when contract has `run:`
  "instruction": "…",                 // from step contract, interpolated
  "rules": ["…"],                     // merged rules list
  "env": {
    "ORCHESTRATOR_CHANGE_ID":    "subprocess-per-step-observability",
    "ORCHESTRATOR_PHASE":        "specify",
    "ORCHESTRATOR_STEP_ID":      "explore",
    "ORCHESTRATOR_ATTEMPT":      "1",
    "ORCHESTRATOR_WORKFLOW_DIR": "/Users/spidey/.workflows/subprocess-per-step-observability",
    "ORCHESTRATOR_REPO_ROOT":    "/Users/spidey/code/orchestrator"
  },
  // For verify_phase:
  "commands": ["bash scripts/verify-spec.sh"],
  "assertions": ["spec.md exists"],
  // For blocked:
  "reason": "escalate_to_architect",
  "escalation": { "type": "…", "question": "…", "context": "…", "attempted": "…" },
  // For retry_step:
  "previous_failure": "no ended_at",
  "attempts_remaining": 2
}
```

**Read/write contract**:
- CLI **reads**: `state.yaml`, step contracts under
  `$ORCHESTRATOR_HOME/config/steps/`, pricing under `config/pricing.yaml`
  (for optional enrichment), phase `verify:` blocks from the workflow schema.
- CLI **writes**: `metrics.duckdb` (`INSERT OR REPLACE` into `step_events` only).
- CLI **never** writes to `state.yaml`, spawns subprocesses, or touches any
  other file.

### state.yaml `step_history[]` entry schema

**Before** (current):
```yaml
step_history:
  - step_id: explore
    phase: specify
    status: completed
    agent: inline
    started_at: 2026-04-17T21:12:42Z
    completed_at: 2026-04-17T21:12:42Z
    usage:
      tool_uses: 22
      duration_ms: 900000
```

**After** (new fields additive; old fields remain for inline steps):
```yaml
step_history:
  - step_id: explore
    phase: specify
    status: completed                   # new values allowed: in_progress, blocked, escalate_to_architect
    agent: discoverer                   # was usually 'inline'; now named runtime role
    attempt: 1                          # NEW — CLI-assigned
    started_at: 2026-04-17T21:12:42Z
    ended_at:   2026-04-17T21:27:42Z    # NEW canonical name; completed_at kept as alias during migration
    artifacts:                          # NEW — list of files touched
      - path/to/output.md
    usage:                              # NEW sub-block (short names)
      input_tokens:              120000
      output_tokens:             18000
      cache_read_input_tokens:   85000
      cost_usd:                  2.47
      tool_calls:
        Read: 32
        Grep: 8
        Bash: 4
      duration_ms:               912000
      model: "claude-sonnet-4-5"        # optional; maps to gen_ai.request.model
    retry_context:                      # OPTIONAL — per contracts/error-recovery.md
      previous_failure: regression
      detail: "…"
```

**Escalation / blocked entries** carry structured sub-blocks:
```yaml
  - step_id: execute-next-task
    phase: implement
    status: escalate_to_architect       # new
    agent: developer
    attempt: 1
    started_at: 2026-04-18T10:00:00Z
    ended_at:   2026-04-18T10:12:00Z
    escalation:                         # new — consumed by CLI, surfaced in blocked JSON
      type: contradiction
      task_id: T-7
      context: "…"
      question: "…"
      attempted: "…"
    usage: { ... }                      # still captured
```

Backwards compatibility: fields absent from historical archives remain absent;
consumers check key existence (same contract as `per_step` block).

**Sunset for `completed_at` (deprecation path)**: new writers emit
`ended_at` only. Readers accept both, preferring `ended_at` when both
are present. `completed_at` is removed from the schema when there are
zero active (non-archived) state.yaml files still using it — at that
point a one-line cleanup chore rewrites any archived uses to
`ended_at` (or leaves them alone; archives are read-only). Sunset is
tracked separately, not in this feature's tasks.

### DuckDB `step_events` Table

**DDL** (to be executed via `CREATE TABLE IF NOT EXISTS` at CLI boot):

```sql
-- step_events: one row per terminal step_history[] entry.
-- Keyed granularity: (repo_root, change_id, phase, step_id, attempt).
-- Writer: `orchestrator next` only. Single-writer constraint is accepted —
-- DuckDB does not support concurrent writers and this is sufficient for
-- single-workflow-at-a-time operation.
CREATE TABLE IF NOT EXISTS step_events (
  -- ─── Dimension keys (all non-null) ─────────────────────────────────────
  repo_root   VARCHAR NOT NULL,
  change_id   VARCHAR NOT NULL,
  phase       VARCHAR NOT NULL,
  step_id     VARCHAR NOT NULL,
  attempt     INTEGER NOT NULL,

  -- ─── Descriptors ──────────────────────────────────────────────────────
  agent_name  VARCHAR NOT NULL,    -- e.g., 'discoverer', 'reviewer', 'inline'
  status      VARCHAR NOT NULL,    -- completed|failed|blocked|escalate_to_architect
  schema_name VARCHAR,             -- from state.yaml schema field

  -- ─── Timestamps ───────────────────────────────────────────────────────
  started_at  TIMESTAMP,
  ended_at    TIMESTAMP,
  duration_ms BIGINT,

  -- ─── OpenTelemetry GenAI semantic convention columns ──────────────────
  gen_ai_request_model                   VARCHAR,
  gen_ai_usage_input_tokens              BIGINT,
  gen_ai_usage_output_tokens             BIGINT,
  gen_ai_usage_cache_read_input_tokens   BIGINT,
  gen_ai_usage_cost_usd                  DOUBLE,

  -- ─── Structured payloads (JSON strings for flex queries) ──────────────
  tool_calls_json  VARCHAR,        -- {"Read": 32, "Grep": 8, ...}
  artifacts_json   VARCHAR,        -- ["path/to/out.md", ...]
  escalation_json  VARCHAR,        -- non-null only for escalate_to_architect rows

  -- ─── Audit ────────────────────────────────────────────────────────────
  upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
);

-- Helpful non-unique index for feature-level rollup queries.
CREATE INDEX IF NOT EXISTS idx_step_events_change
  ON step_events(repo_root, change_id);
```

**Note on the `status` column in the PK**: a single `(phase, step_id,
attempt)` may legitimately produce more than one terminal step_history
entry when an architect escalation occurs. Per
`contracts/architect-escalation.md`, an escalated attempt is **not**
charged a retry — the developer re-spawns with the same `attempt` after
the architect decision. This produces two entries at the same
`(phase, step_id, attempt)`: an earlier one with
`status: escalate_to_architect` and a later one with `status: completed`.
Adding `status` to the primary key preserves the escalation audit trail;
rollup queries that care about only terminal outcomes filter
`status IN ('completed','failed','blocked')` (exclude
`escalate_to_architect`). Rollups that want full attempt history use no
filter.

**Upsert pattern** (matches `metrics-db-derived` learning):

```sql
INSERT OR REPLACE INTO step_events (
  repo_root, change_id, phase, step_id, attempt,
  agent_name, status, schema_name,
  started_at, ended_at, duration_ms,
  gen_ai_request_model,
  gen_ai_usage_input_tokens, gen_ai_usage_output_tokens,
  gen_ai_usage_cache_read_input_tokens, gen_ai_usage_cost_usd,
  tool_calls_json, artifacts_json, escalation_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
```

All string values pass through `sql_quote`-equivalent parameterisation
(Python's `duckdb.execute(sql, params)` binds parameters — no string
interpolation). `change_id` validated against `^[a-z0-9][a-z0-9-]*$`
before any INSERT (slug guard per `metrics-db-derived`).

**Short → OTel name mapping** (applied at upsert time):

| state.yaml (short)            | step_events column                      |
|-------------------------------|-----------------------------------------|
| `usage.input_tokens`          | `gen_ai_usage_input_tokens`             |
| `usage.output_tokens`         | `gen_ai_usage_output_tokens`            |
| `usage.cache_read_input_tokens` | `gen_ai_usage_cache_read_input_tokens` |
| `usage.cost_usd`              | `gen_ai_usage_cost_usd`                 |
| `usage.model`                 | `gen_ai_request_model`                  |
| `usage.duration_ms`           | `duration_ms`                           |
| `usage.tool_calls` (map)      | `tool_calls_json` (JSON-serialised)     |

### Reference Step Migration: `explore`

**Why `explore`**: it already declares `agent: discoverer`, exercises LLM +
tool calls, produces a real artifact (`discovery.md`), and runs early in
every feature workflow — fastest path to a real token/cost row.

**Contract change** (`config/steps/explore.yaml`):

```yaml
id: explore
version: 3                                   # bumped
intent: …                                    # unchanged
agent: discoverer                            # unchanged
run: config/scripts/adapters/claude_discoverer.py   # NEW — optional field
inputs: [phase_context_bundle]               # unchanged
rules: [ … ]                                 # unchanged
instruction: |                               # unchanged
  …
verify: [ … ]                                # unchanged
outputs: [discovery_result]                  # unchanged
```

**Adapter** (`config/scripts/adapters/claude_discoverer.py`):

```python
#!/usr/bin/env python3
# claude_discoverer.py — reference adapter for `explore` step.
# Invoked by the caller (skill/CLI) after `orchestrator next` returns run_step.
# Inputs via env: ORCHESTRATOR_CHANGE_ID, ORCHESTRATOR_PHASE, ORCHESTRATOR_STEP_ID,
#                 ORCHESTRATOR_ATTEMPT, ORCHESTRATOR_WORKFLOW_DIR, ORCHESTRATOR_REPO_ROOT.
# The adapter:
#   1. Constructs the discoverer prompt from the step instruction + rules.
#   2. Invokes `claude -p <prompt>` (claude-code CLI) via subprocess and captures output.
#   3. Reads the last ccusage record for this session to compute tokens/cost.
#   4. Appends a step_history entry to state.yaml with the usage block.
#
# The adapter is the runtime-specific boundary — Codex, Anthropic API,
# OpenAI CLI, etc. adapters implement the same env/output contract.
# Python was chosen for the adapter for the same reason as the CLI
# (OQ-1): YAML-safe append via ruamel/pyyaml round-trip, not bash/yq.
# ... (implementation details tracked in T-9)
```

Adapter **output contract** (same shape for every runtime):
1. Produces the artifact the step contract declares (here: `discovery.md`).
2. Appends a single completed `step_history[]` entry to `state.yaml` with:
   - `status: completed` (or `failed` / `blocked` / `escalate_to_architect`)
   - `agent: discoverer`
   - `attempt: $ORCHESTRATOR_ATTEMPT`
   - `started_at` / `ended_at`
   - `usage:` block with short names
   - `artifacts: [discovery.md]`
3. Exits 0 on success, non-zero on adapter failure.

### Coexistence with inline-only contracts (44 at implementation time)

During the transition:
- Contract without `run:` → CLI returns `action: run_inline` → caller runs
  inline (today's behaviour) → state_history entry written by the calling
  skill inline with `agent: inline`, no `usage:` sub-block → CLI upserts a
  `step_events` row with null token columns and `agent_name='inline'`.
- Contract with `run:` → CLI returns `action: run_step` → caller execs the
  adapter → adapter writes the entry → CLI upserts a row with real tokens.

Both paths produce rows in `step_events`. Rollup queries that `SUM(...)`
tolerate null columns naturally. No data is lost for inline steps; they just
report no tokens (which is accurate — they produced none attributable to
themselves).

### Test Strategy

**Fixture-driven CLI tests** (`test_orchestrator_next.py`):
- `fixtures/state-pending-inline.yaml` → expect `run_inline`.
- `fixtures/state-pending-runfield.yaml` → expect `run_step` with `run`.
- `fixtures/state-in-progress-no-ended.yaml` → expect `retry_step`,
  `attempt: 2`.
- `fixtures/state-phase-done-needs-verify.yaml` → expect `verify_phase`.
- `fixtures/state-all-done.yaml` → expect `complete_workflow`, exit 1.
- `fixtures/state-escalate.yaml` → expect `blocked`, exit 2,
  `reason: escalate_to_architect`.

Each test asserts: JSON output matches golden file byte-for-byte (sorted
keys) AND state.yaml mtime is unchanged.

**DuckDB idempotency tests** (`test_step_events_upsert.py`):
- Given fixture with N terminal entries, call `next` twice, assert
  `SELECT COUNT(*) FROM step_events WHERE ... = N`.
- Assert all dimension columns non-null.
- Assert short→OTel column mapping (fixture with known `input_tokens`
  produces row with matching `gen_ai_usage_input_tokens`).
- UC-E1 case: fixture with `attempt: 1 failed` + `attempt: 2 completed` →
  assert two rows, totals match only `attempt: 2` when filtering by
  `status = 'completed'`.

**End-to-end reference test** (`test_explore_adapter.sh`):
- Scratch workflow dir, minimal state.yaml with only `explore` pending.
- Run `orchestrator next`, exec the returned `run:` adapter.
- Assert: `discovery.md` exists; state.yaml has completed entry; DuckDB
  has row with `gen_ai_usage_input_tokens > 0`.
- May be gated on `CLAUDE_API_KEY` presence; skip with SKIP message if
  absent (keeps CI green when key is missing).

**Smoke test for inline contracts** (`test_inline_smoke.py`):
- Iterate over all 31 `config/steps/*.yaml` without `run:`, synthesise a
  minimal state.yaml for each, run `orchestrator next`, assert
  `action: run_inline`, no error.

## Components

| Component | Responsibility | Inputs | Outputs |
|-----------|---------------|--------|---------|
| `bin/orchestrator` | Python entry-point (shebang-invoked, no bash wrapper); arg parse, orchestrate parse+upsert+dispatch | `state.yaml` path | JSON to stdout, exit code, DuckDB writes |
| `config/scripts/orchestrator_next/__init__.py` | Package marker | — | — |
| `config/scripts/orchestrator_next/parser.py` | state.yaml + step contract parser, YAML-safe | file path | `State` dataclass |
| `config/scripts/orchestrator_next/dispatch.py` | Pure function (State) → action JSON | `State` | JSON dict, exit code |
| `config/scripts/orchestrator_next/upsert.py` | DuckDB DDL, INSERT OR REPLACE, slug guard | `State`, DB path | rows written |
| `config/scripts/orchestrator_next/otel_map.py` | Short name ↔ OTel column mapping | usage dict | column-keyed dict |
| `config/scripts/adapters/claude_discoverer.py` | Reference adapter for `explore` (Python) | env vars | writes step_history entry, exit code |

## Data Flow

1. **Caller** runs `orchestrator next state.yaml`.
2. **Parser** loads state.yaml (yaml.safe_load), loads step contract for the
   step head, validates minimal shape.
3. **Upsert**: iterate terminal step_history entries; apply short→OTel
   mapping; `INSERT OR REPLACE` each. Idempotent — re-running produces
   identical rows.
4. **Dispatch**: compute next action from state (§ State Transitions above).
   Assign `attempt` from scan of existing entries.
5. **Emit**: print JSON to stdout. Exit with the code matching the action.
6. **Caller** reads JSON, takes the action (exec adapter, run verify, mark
   complete, etc.), writes the resulting step_history entry back to state.yaml,
   calls `orchestrator next` again.

## State Management

- state.yaml: owned by agents; CLI never writes.
- `metrics.duckdb::step_events`: owned by the CLI via `INSERT OR REPLACE`.
- No other state is introduced.

## Error Handling

- **Malformed state.yaml** → exit 3, stderr contains YAML parse error with
  file:line. No DuckDB write attempted.
- **Unknown schema field** in state.yaml → exit 3 with diagnostic referencing
  the offending key; no write. (Conservative.)
- **DuckDB lock (concurrent writer)** → exit 3 with clear message; docs note
  single-writer constraint.
- **Missing step contract** for a step_id in state.yaml → exit 3 with
  message listing `config/steps/*.yaml` search path.
- **`change_id` slug validation fail** → exit 3; no write. Matches
  `metrics-db-derived` learning.
- **Agent output lacks STATUS** (detected by caller, not CLI) → caller writes
  `status: blocked, blocker: "missing STATUS"` per `error-recovery.md`.

## Constraints

- **Single-writer DuckDB** — no concurrent CLI invocations.
- **State.yaml is the only mutable truth** — DuckDB is always re-derivable.
- **No network calls from the CLI.**
- **Agent-agnostic naming** — no column or JSON field references Claude,
  Codex, or any specific tool.

## Trade-offs

- **Python dependency added** to the stack; **bash not used** for anything new.
  User direction: shrink the stack to what's needed instead of layering
  bash-plus-Python. Mitigated by: shebang entry-point (no venv), stdlib +
  two `pip install --user` packages. Justified by the structural
  YAML-safety improvement over bash/yq.
- **Duplicate data** (state.yaml + step_events for the same entry) —
  accepted. DuckDB is a derived query cache; state.yaml is source of truth.
- **Single-writer DuckDB** — accepted. Concurrency not a near-term
  requirement; `metrics-db-derived` learning already documents this.
- **Only one step migrated** — other 44 keep today's inline behaviour. This
  leaves known-zero-token rows for inline steps. Accepted — migration path
  documented; no data is wrong (zero is accurate for inline).

## Decisions (summary)

- **Driver language: Python 3 (stdlib + PyYAML + duckdb)** → YAML safety >
  stack uniformity → one more language in the stack, scoped to one file.
- **`attempt` assignment: CLI computes, returns in JSON** → agents stay
  dumb → central retry accounting.
- **Escalation channel: `status` field in step_history** → state.yaml
  remains source of truth → `error-recovery.md` grows two status rows.
- **Phase verify locus: CLI describes, caller runs** → CLI stays pure-read
  → callers must know the `verify_phase` action type.
- **Superseded feature: `step-self-reported-metrics` declared obsolete** →
  no automated cross-workflow deletion → user removes stale state.yaml
  manually.

## Open Questions

None remaining at design time. Implementation may surface small decisions
(e.g., exact pip-install mechanism in `install.sh`) — those are scoped for
execute-next-task per the escalation protocol.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
