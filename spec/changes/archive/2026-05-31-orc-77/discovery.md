---
feature-id: orc-77
linear-ticket: ORC-77
---

# Discovery Brief: Remove agent: inline sentinel — migrate shell-script steps to explicit run: declaration

## Feature Summary

The `agent: inline` string currently serves as a magic sentinel in the orchestrator engine — it acts as both an **explicit declaration** in one test fixture and as a **default fallback** value when a step contract omits the `agent:` field. Removing it requires distinguishing two cases: (1) script-executed steps that already omit `agent:` and use `run: script.sh`, and (2) engine code that defaults missing agents to the literal string `"inline"`. This refactor eliminates the sentinel by making the absence of `agent:` the canonical representation for script steps, updating all engine internals to test `contract.agent is None` instead of `contract.agent == "inline"`, and migrating the one test fixture that explicitly declares `agent: inline`.

## Personas & Actors

- **Orchestrator engine** (dispatch.py, record.py, doctor.py, parser.py, generate_plan.py) — consumes step contracts and uses the sentinel as a branch condition
- **Workflow authors** — write step contracts; currently omit `agent:` for script steps (no change needed for them)
- **Test suite** — fixtures that carry `agent: inline` in state payloads and contract files must be migrated

## Use Cases

### Happy Path

UC-1: Script step dispatches without agent sentinel — a step contract with `kind: script` and `run: script.sh` but no `agent:` field is dispatched correctly via the run-path; engine checks `contract.agent is None` and routes to inline script execution.

UC-2: Agent step dispatches to named agent — a step contract with `agent: developer` is dispatched via the agent-spawn path; absence of `agent: inline` check does not affect named agent dispatch.

UC-3: Doctor validates script steps — `orchestrator doctor` correctly identifies script steps missing their `run:` target file without relying on the inline sentinel string.

UC-4: Record stores script step result — a completed script step's done-payload (no agent field) is recorded in DuckDB with `agent_name = NULL` (or an agreed placeholder) instead of `"inline"`.

### Error & Edge Cases

UC-E1: Contract missing both agent and run — a contract with neither `agent:` nor `run:` should produce a `ContractDispatchError` (exit 3); the old inline-fallback path must not silently mask this misconfiguration.

UC-E2: Test fixture still carries agent: inline — any test that asserts `agent_name == "inline"` in a state fixture must be updated; stale assertions would produce false positives post-migration.

UC-E3: Metrics bucketing references inline sentinel — `compute-swe-metrics.sh` buckets steps by agent value; if the sentinel is replaced with NULL, the bucketing query must handle NULL explicitly.

## Scope

### In Scope

- Audit all 15 production `kind: script` step contracts and confirm none explicitly set `agent: inline`
- Remove `agent: inline` default from parser.py (line 374), replacing with `None`
- Update dispatch.py (lines 267, 488, 554) to check `contract.agent is None` instead of `contract.agent == "inline"`
- Update record.py (lines 1499, 1516, 1520, 1648) to replace `"inline"` sentinel with `None` or an agreed NULL strategy
- Update doctor.py (lines 227, 241) to check `name is None` instead of `name == "inline"`
- Update generate_plan.py (lines 276, 280) to use `None` as default instead of `"inline"`
- Migrate test fixture `tests/fixtures/step_contracts/step-inline-only.yaml` (remove `agent: inline`)
- Migrate state fixture files that carry `agent: inline` in step history entries (5+ files)
- Update `test_inline_smoke.py`, `test_step_events_upsert.py`, `test_inline_script.py`, `test_doctor.py` to remove inline-sentinel assertions
- Update `spec/project.yaml` learning entry `inline-steps-are-tokenless` to describe the new NULL pattern
- Decide and document the DuckDB `agent_name` column value for script steps (NULL vs. empty string)

### Out of Scope

- CONVENTIONS.md, metrics-schema.md, rule-merge.md — these files do not exist in the repo; the ticket referenced them speculatively; any live documentation is in `spec/project.yaml` learnings
- The `inline: bool` field on `StepContract` (parser.py line 58) — this is the HL-287 M3 `inline: true + run:` feature flag, distinct from the `agent: inline` sentinel; leave untouched
- Changes to workflow YAML schemas or step directory structure
- Metrics DuckDB schema changes beyond NULL handling for `agent_name`

## UI Direction

N/A — no UI components. This is a pure engine refactor with no user-facing interface changes.

## Key Decisions

- **NULL vs. empty string for agent_name in DuckDB** → NULL. No live external consumers filter on `agent_name = 'inline'` (compute-swe-metrics is a Python step, not a shell script; its test fixture baseline.duckdb.sql will be updated to use NULL). NULL is SQL-canonical for "absent value". Consequence: queries use `agent IS NULL` for script steps.
- **Alias vs. hard removal** → Hard removal. Grepping `config/steps/` returns zero matches for `agent: inline` in production contracts. No aliases needed; keeping one preserves dead code with no benefit.
- **per-site analysis for record.py** → Required. Four record.py sites have two distinct patterns: default assignment (`get(..., "inline")` → `get(...)`) vs. comparison (`!= "inline"` → `is not None`). A mechanical find-replace would invert the logic at lines 1499/1520. Each site must be edited individually.
- **Build or reuse** → Reuse. Pure refactor of existing engine code — no new components, no external dependencies. All work is within 5 existing files plus test fixtures.

## Open Questions

- OQ-1: What should `agent_name` be in DuckDB for script steps post-migration — `NULL`, `"script"`, or kept as `"inline"` for backward compatibility with existing metrics queries?
- OQ-2: Are there any external consumers of the metrics DB (dashboard, compute-swe-metrics.sh) that filter on `agent_name = 'inline'` and would break if the value changes?
- OQ-3: The `test_inline_smoke.py` comment references "44 inline-only step contracts" — is this count accurate at time of migration, and does it include the test fixture contract or only production config/steps contracts?
