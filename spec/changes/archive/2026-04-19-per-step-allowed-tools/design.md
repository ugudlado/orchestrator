# Design: Per-Step allowed_tools Enforcement

## Context

`orchestrator_next` already has three components that together determine what an agent
runs and is observed doing:

- `parser.py` (`StepContract` + `_load_contract`) — pure YAML → dataclass loader.
- `dispatch.py` — resolves the agent, builds the action dict consumed by the
  orchestrate skill.
- `cost_report.py` — post-run aggregator; reads persisted `tool_calls` rows and already
  flags tools used outside the agent role's frontmatter (`_anomalies`).

A private helper, `cost_report._load_agent_tools`, parses the agent `.md` frontmatter
(search order: `$ORCHESTRATOR_HOME/agents/` then `~/.claude/agents/`) and returns the
role's tool set or `None`. The dispatcher does not know about this helper today, and
neither dispatch nor cost-report observe per-step restrictions — the concept does not
exist in the system.

The feature introduces that concept as an optional `allowed_tools:` field on each step
contract, wires the dispatcher to compute and publish `resolved_allowed_tools`, and
extends the cost report to flag drift at report time.

## Goals / Non-Goals

### Goals

- Step contracts can declare a minimal tool set for the step they author.
- The action dict exposes `resolved_allowed_tools` as the authoritative narrowed list
  for that step's run.
- The cost report distinguishes "tool not in role" from "tool not in step allowlist".
- Zero breakage for the 33 existing contracts.
- `_load_agent_tools` lives in exactly one place.

### Non-Goals

- Orchestrate skill changes (how narrowed tools are passed to spawned agents).
- Persistence of `resolved_allowed_tools` in `step_events` (DDL migration).
- Wildcard / prefix matching in `allowed_tools` values.
- `orchestrator doctor` static validation of `allowed_tools` entries.
- Runtime-conditional allowlists (per-flag or per-input variance).

## Approaches Considered

### Approach 1: Field-only + report-time detection (selected)

Add `allowed_tools` to `StepContract`; move `_load_agent_tools` into a shared
`resolver.py`; have `dispatch.py` intersect and publish `resolved_allowed_tools`; have
`cost_report.py` add a second anomaly check at report time.

- Pros: Minimal surface, no schema migration, no skill changes, self-contained.
- Cons: Report-time check re-reads current contracts — historical drift possible if
  the allowlist changes between the run and the report.

### Approach 2: Field + enforcement at spawn (orchestrate skill consumes)

Approach 1 plus changes to the `~/.claude/skills/orchestrate/SKILL.md` to translate
`resolved_allowed_tools` into the Agent-tool `allowed_tools` parameter at spawn time.

- Pros: Actual at-run enforcement; narrower blast radius in real time.
- Cons: Skill file is external to this repository's contract surface and was
  inaccessible during discovery; couples this feature to skill changes we don't own
  in this iteration.

### Approach 3: Contract registry with eager CI validation + per-step tool attestation

Introduce a formal registry of all contracts, validate `allowed_tools` against agent
frontmatter at CI time via `orchestrator doctor`, and persist an attestation per step.

- Pros: Catches widening errors without running; historically accurate.
- Cons: Large surface (new CLI check, new persistence columns, migrations). Out of
  proportion to the single feature requested.

### Selected Approach

**Approach 1.** Smallest correct surface. Publishes `resolved_allowed_tools` so
Approach 2 (spawn-time enforcement in the skill) can land as a pure consumer in a
follow-up without re-touching the dispatcher. Approach 3's benefits (eager validation,
historical accuracy) are additive and can ship later as `orchestrator doctor` and
`step_events` migrations without contract changes.

## High-Level Design

### Architecture Overview

```
YAML contract ──▶ parser._load_contract ──▶ StepContract
                                               │
                                               ▼
                                          dispatch.dispatch()
                                               │
                   resolver.load_agent_tools ◀─┤  (role tool set or None)
                                               │
                   intersection + widening guard
                                               │
                                               ▼
                                          action dict
                                    { ..., "resolved_allowed_tools": [...] }


persisted tool_calls ──▶ cost_report.aggregate_feature
                              │
                              ├─▶ existing _anomalies          ("not in role")
                              │
                              └─▶ new   _step_allowlist_anomalies ("not in step allowlist")
                                            │
                                            └─ loads each step's contract via parser,
                                               re-uses resolver.load_agent_tools as
                                               needed for sanity (role-set fallback).
```

### Key Abstractions

- `resolver.load_agent_tools(agent_name: str) -> set[str] | None` — the single source
  of truth for "what tools does this role declare?". Relocated verbatim from
  `cost_report._load_agent_tools`; same two-location search, same graceful-None
  semantics.
- `contract.allowed_tools: list[str]` — additive `StepContract` field; empty list
  means "no restriction, inherit full role list".
- Action-dict key `resolved_allowed_tools: list[str]` — the narrowed, sorted, unique
  tool list for this step's run. Always present going forward; `[]` only when role
  tools are unresolvable or the agent is `inline`.

## Low-Level Design

### Components

**`parser.py`**
- `StepContract` gains `allowed_tools: list[str] = field(default_factory=list)`.
- `_load_contract()` reads `data.get("allowed_tools", []) or []` (the `or []` converts
  an explicit YAML `null` to `[]`, matching FR-2 / AC-7 / UC-E4).
- Parser does no validation of names — pure YAML load.

**`resolver.py` (new)**
- Single public function: `load_agent_tools(agent_name: str) -> set[str] | None`.
- Implementation is the body currently at `cost_report.py:62-102`, copied verbatim and
  renamed (drop leading underscore). The constants and search order are preserved.
- Returns `None` on: missing file, missing/unparseable frontmatter, missing `tools:`
  key, non-list `tools:` value. Callers interpret `None` as "no role-tool information
  available; skip intersection, emit warning if `allowed_tools` was declared".

**`cost_report.py`**
- Deletes the private `_load_agent_tools`; imports `load_agent_tools` from `resolver`.
- Adds `_step_allowlist_anomalies(conn, repo_root, change_id) -> list[dict]`:
  - SQL: `SELECT phase, step_id, agent_name, tool_name, COUNT(*) AS calls FROM
    tool_calls WHERE repo_root = ? AND change_id = ? GROUP BY phase, step_id,
    agent_name, tool_name`.
  - For each distinct `(phase, step_id)`, load the step's contract via
    `parser._load_contract` (re-used, same path-resolution logic as dispatch).
  - Skip rows where the contract's `allowed_tools` is empty (inherits full role —
    no step-level restriction to check) or where the contract cannot be located.
  - Emit a row for each `tool_name` not in `contract.allowed_tools`.
- `aggregate_feature()` invokes the new function and attaches results under a new
  key (e.g., `anomalies_step_allowlist`) alongside the existing `anomalies`.
- `render_markdown_feature()` renders two subsections under Anomalies:
  - "Tool not in role" (existing output, unchanged).
  - "Tool not in step allowlist" (new), only rendered if non-empty.

**`dispatch.py`**
- After constructing `contract` and resolving the agent name, call
  `resolver.load_agent_tools(contract.agent)` (skipping when `contract.agent ==
  "inline"`).
- Compute `resolved_allowed_tools` using the pseudocode below; raise `ContractError`
  on widening; emit a stderr warning on graceful-degradation cases.
- Inject the key into every action-dict build site:
  - `run_inline` branch.
  - `run_step` new path (`parser.load_contract` route).
  - `run_step` legacy path (`contract.run` variant).
  - `retry_step` path (lines ~188-215).

Intersection pseudocode (authoritative):

```python
if contract.agent == "inline":
    if contract.allowed_tools:
        warn_stderr(f"allowed_tools on inline step {contract.step_id!r} ignored")
    resolved_allowed_tools = []
else:
    role_tools = resolver.load_agent_tools(contract.agent)
    if role_tools is None:
        if contract.allowed_tools:
            warn_stderr(
                f"cannot resolve agent {contract.agent!r} tools; "
                f"allowed_tools on step {contract.step_id!r} not enforced"
            )
        resolved_allowed_tools = []
    elif contract.allowed_tools:
        declared = set(contract.allowed_tools)
        illegal = declared - role_tools
        if illegal:
            raise ContractError(
                f"allowed_tools on step {contract.step_id!r} declares "
                f"{sorted(illegal)!r} not in agent {contract.agent!r} tools"
            )
        resolved_allowed_tools = sorted(declared & role_tools)
    else:
        resolved_allowed_tools = sorted(role_tools)
```

### Data Flow

1. Workflow author edits a step contract YAML and (optionally) adds
   `allowed_tools: [Read, Grep, Glob]`.
2. `orchestrator next` runs → `dispatch()` loads the contract, calls
   `resolver.load_agent_tools`, computes `resolved_allowed_tools`, and emits the
   action dict.
3. The orchestrate skill reads the action dict (skill-side consumption is out of
   scope; the key is now available).
4. The spawned agent runs; `tool_calls` rows are persisted by existing machinery
   (unchanged) with `phase` and `step_id`.
5. `orchestrator cost --change-id <cid>` aggregates and renders; the new
   "Tool not in step allowlist" subsection appears when drift exists.

### State Management

No new persistent state. The new `anomalies_step_allowlist` list lives only in the
aggregate result dict returned by `aggregate_feature()` and is re-derived on every
run of `orchestrator cost`.

### Error Handling

| Condition | Strategy | Rationale |
|-----------|----------|-----------|
| YAML `allowed_tools: null` or missing | Parser returns `[]` | Same semantics as absent (FR-2, AC-7). |
| `allowed_tools` widens role | `ContractError` at dispatch | Contract author error; fail loudly (FR-4, AC-4). |
| Role frontmatter unresolvable | Warn stderr, `resolved_allowed_tools = []` | Degrade gracefully; mirrors existing cost_report behavior (FR-9, AC-5). |
| `agent: inline` with `allowed_tools:` | Warn stderr, skip intersection | Inline steps don't spawn tool-using agents (FR-8, AC-6). |
| Cost-report can't locate a step's contract at report time | Skip that `(phase, step_id)` row silently | Historical contracts may have moved; don't crash a post-mortem report. |

## Constraints

- No new third-party deps.
- No DDL migration.
- Must not change behavior for the 33 existing contracts beyond the additive
  `resolved_allowed_tools` action-dict key.
- Tests must follow existing `config/scripts/orchestrator_next/tests/` conventions.

## Trade-offs

- **Report-time drift.** Because `_step_allowlist_anomalies` re-reads current contract
  state, a contract edited after a run will shift the report. Acceptable for M1; a
  future `step_events` column can close this (see Non-Goals).
- **No spawn-time enforcement.** This iteration does not modify the orchestrate skill,
  so agents are not prevented from using a disallowed tool — they are only flagged
  afterward. Acceptable: publishing `resolved_allowed_tools` is the contract this
  feature owes; the skill change is a clean follow-up.
- **Empty-list semantics as "no restriction".** Authors who expected `[]` to mean
  "zero tools" will be surprised. Mitigated by the explicit Decision in spec.md; a
  distinct sentinel can be introduced later if a real use case emerges.

## Decisions

- `allowed_tools: list[str]` on `StepContract`, default `[]` → rationale: mirrors the
  `inputs` / `outputs` pattern introduced in HL-287 → consequence: no new migration
  needed; parser stays pure.
- Extract `_load_agent_tools` into `resolver.py` rather than duplicating into
  `dispatch.py` → rationale: DRY, clean module boundary → consequence: one new file,
  one import change in `cost_report.py`.
- Intersection in `dispatch.py` (not `_load_contract`) → rationale: dispatch already
  knows the agent name and owns role-level context → consequence: parser remains a
  pure YAML reader; tests for the parser stay simple.
- Widening is `ContractError`; other failure modes are warnings → rationale: widening
  is author error; missing frontmatter is environment noise → consequence: CI
  fails fast on author mistakes, runs don't crash on env hiccups.
- Cost-report renders two distinct anomaly subsections → rationale: AC-3 requires
  distinguishing the two causes; separate subsections are the clearest signal →
  consequence: slightly longer Anomalies section in reports.

## Open Questions

- **OQ-1 resolved:** The orchestrate skill's consumption path is out of scope this
  iteration. M1's contract is the action-dict key; the skill is a pure consumer in a
  follow-up.
- **OQ-4 deferred:** Persisting `resolved_allowed_tools` in `step_events` is a future
  migration when report-time drift becomes a real problem. Not needed for M1.
- **OQ-7 deferred:** A corresponding `orchestrator doctor` check to statically
  validate `allowed_tools` entries across all contracts is a separate feature.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
