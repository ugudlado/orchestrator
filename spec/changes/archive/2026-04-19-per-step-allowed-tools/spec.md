---
feature-id: per-step-allowed-tools
linear-ticket: HL-295
---

# Specification: Per-Step allowed_tools Enforcement

## Motivation

Step contracts today assign a fixed `agent:` but cannot restrict which tools that agent
uses for a specific step. Every invocation of the `developer` role, for example, gets
the full 13-tool list — even when a step only needs Read, Grep, and Glob. This breaks
least-privilege: a step that is supposed to do bounded research can silently reach for
`Bash` or `WebSearch`, widening the blast radius and inflating cost. Workflow authors
need a way to declare the minimal tool set per step, the dispatcher needs to publish
the resolved set so the orchestrate skill can honor it, and the cost report needs to
surface drift when an agent used a tool outside the step's allowlist.

## What Changes

- `StepContract` gains an optional `allowed_tools: list[str]` field, parsed from YAML.
- The `_load_agent_tools` helper (currently private to `cost_report.py`) is extracted
  to a new shared `resolver.py` module so both the dispatcher and the cost report can
  reuse it without duplication.
- `dispatch.py` intersects the step's `allowed_tools` with the agent role's frontmatter
  tools and emits `resolved_allowed_tools` in every action-dict branch.
- `cost_report.py` gains a second anomaly check — "tool not in step allowlist" —
  rendered as a separate subsection alongside the existing "tool not in role" check.
- No database schema migration; no new CLI flag; no orchestrate skill change in this
  iteration (report-time detection is the enforcement surface for M1).

## Requirements

### Functional

1. **FR-1**: `StepContract` must expose an `allowed_tools: list[str]` attribute that
   defaults to an empty list when the YAML omits the field or sets it to `null`.
2. **FR-2**: `_load_contract()` in `parser.py` must populate `allowed_tools` from
   `data.get("allowed_tools", []) or []` so both absent and explicit-null produce `[]`.
3. **FR-3**: The shared `resolver.load_agent_tools(agent_name)` function must return
   `set[str] | None`, searching `$ORCHESTRATOR_HOME/agents/` then `~/.claude/agents/`,
   and returning `None` on missing file, missing frontmatter, bad YAML, or missing
   `tools:` key — matching the current `cost_report._load_agent_tools` behavior.
4. **FR-4**: At dispatch time, when `contract.allowed_tools` is non-empty and the
   role's tool set is resolvable, the dispatcher must raise `ContractError` if any
   declared tool is not present in the role's frontmatter tools (widening guard).
5. **FR-5**: Every action dict produced by `dispatch.py` (`run_inline`, `run_step`
   new-path, `run_step` legacy `contract.run` path, and the retry-step path) must
   include a `resolved_allowed_tools` key.
6. **FR-6**: When `contract.allowed_tools` is non-empty, `resolved_allowed_tools`
   must equal `sorted(set(contract.allowed_tools) & role_tools)`.
7. **FR-7**: When `contract.allowed_tools` is empty (absent, null, or `[]`) and the
   role's tool set resolves, `resolved_allowed_tools` must equal `sorted(role_tools)`
   — this is the backward-compatible default.
8. **FR-8**: When the agent has `agent: inline` (no agent frontmatter), the dispatcher
   must skip intersection and set `resolved_allowed_tools` to `[]`. If a contract with
   `agent: inline` also declares `allowed_tools:`, the dispatcher must emit a warning
   to stderr; it must not raise.
9. **FR-9**: When the role's tool set cannot be resolved (file missing, bad YAML), the
   dispatcher must degrade gracefully: emit a warning to stderr, set
   `resolved_allowed_tools` to `[]`, and continue. This must not raise.
10. **FR-10**: The cost report must gain a per-step anomaly check that, for each
    `(phase, step_id, agent_name, tool_name)` row in `tool_calls`, loads the step's
    contract and flags any tool name that is not in `contract.allowed_tools` when that
    list is non-empty.
11. **FR-11**: The Anomalies section of the rendered cost report must render this new
    check as a distinct subsection ("Tool not in step allowlist") separate from the
    existing "Tool not in role" subsection.

### Non-Functional

1. **NFR-1**: Backward compatibility — the 33 existing step contracts (none declare
   `allowed_tools:`) must run with byte-identical action-dict semantics modulo the
   new `resolved_allowed_tools` key.
2. **NFR-2**: No new third-party dependencies.
3. **NFR-3**: No DDL migration. The `tool_calls` table already carries `phase` and
   `step_id`, which is sufficient for the per-step anomaly query.
4. **NFR-4**: Test coverage for new and modified code paths ≥ 90%.

## Architecture

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/parser.py` | Add `allowed_tools: list[str]` to `StepContract`; populate from YAML. |
| `config/scripts/orchestrator_next/resolver.py` | **New.** Houses `load_agent_tools(agent_name) -> set[str] \| None`. |
| `config/scripts/orchestrator_next/cost_report.py` | Import `load_agent_tools` from `resolver` (remove private copy). Add `_step_allowlist_anomalies()` and render its output as a new subsection. |
| `config/scripts/orchestrator_next/dispatch.py` | Call `resolver.load_agent_tools`; compute `resolved_allowed_tools`; inject into all 4 action-dict sites; enforce widening guard. |

Data flow: contract YAML → `_load_contract` (parser) → `StepContract` → `dispatch()` →
intersects with `resolver.load_agent_tools(contract.agent)` → action dict with
`resolved_allowed_tools`. Post-run, `cost_report.aggregate_feature` reads persisted
`tool_calls` rows and re-loads each step's contract to detect allowlist violations
against current contract state.

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/parser.py` → `config/scripts/orchestrator_next/tests/test_parser.py`
- `config/scripts/orchestrator_next/resolver.py` → `config/scripts/orchestrator_next/tests/test_resolver.py` (new)
- `config/scripts/orchestrator_next/dispatch.py` → `config/scripts/orchestrator_next/tests/test_dispatch.py`
- `config/scripts/orchestrator_next/cost_report.py` → `config/scripts/orchestrator_next/tests/test_cost_report.py`

(If any of these test files does not yet exist, it will be created following the
existing test-layout convention in `config/scripts/orchestrator_next/tests/`.)

### Coverage Targets

- Overall ≥ 90% for new/modified code. `resolver.py` must be 100% (tiny surface).

### Key Test Scenarios

1. Parser: contract without `allowed_tools:` → field is `[]`.
2. Parser: contract with `allowed_tools: null` → field is `[]`.
3. Parser: contract with `allowed_tools: []` → field is `[]`.
4. Parser: contract with `allowed_tools: [Read, Grep]` → field is `["Read", "Grep"]`.
5. Resolver: existing agent file with `tools:` frontmatter → returns set of names.
6. Resolver: missing file, missing frontmatter, bad YAML, no `tools:` key → returns None.
7. Dispatch: non-empty `allowed_tools` subset of role → `resolved_allowed_tools` is the
   sorted intersection.
8. Dispatch: empty `allowed_tools` → `resolved_allowed_tools` is sorted full role list.
9. Dispatch: widening attempt → `ContractError` with name of illegal tool(s).
10. Dispatch: role tools unresolvable → warning on stderr, `resolved_allowed_tools: []`,
    no exception.
11. Dispatch: `agent: inline` with `allowed_tools:` declared → warning on stderr,
    `resolved_allowed_tools: []`.
12. Dispatch: all 4 action-dict branches (run_inline, run_step, legacy run_step,
    retry_step) carry the `resolved_allowed_tools` key.
13. Cost report: `tool_calls` contains a tool not in the step's `allowed_tools` →
    anomaly flagged in "Tool not in step allowlist" subsection.
14. Cost report: `tool_calls` contains only tools within `allowed_tools` → no entry in
    the new subsection.
15. Cost report: step whose contract has empty `allowed_tools` → step is skipped by the
    new check (no false positives).

## Acceptance Criteria

- **AC-1**: Given a contract that declares `allowed_tools: [Read, Grep, Glob, Bash]`
  for agent `developer`, when `orchestrator next` dispatches the step, then the action
  dict contains `resolved_allowed_tools: ["Bash", "Glob", "Grep", "Read"]`.
  [traces: UC-1]
- **AC-2**: Given a contract with no `allowed_tools:` field, when `orchestrator next`
  dispatches the step, then the action dict contains `resolved_allowed_tools` equal to
  the sorted full role tool list and behavior is otherwise unchanged. [traces: UC-2]
- **AC-3**: Given a run where the dispatched step declared
  `allowed_tools: [Read, Grep, Glob]` and the agent called `WebSearch`, when the user
  runs `orchestrator cost --change-id <cid>`, then the Anomalies section includes a
  "Tool not in step allowlist" row naming the agent, tool, step, and call count.
  [traces: UC-3]
- **AC-4**: Given a contract that declares `allowed_tools: [WebSearch, NewTool]` for
  agent `developer` where `NewTool` is not in the developer role's frontmatter, when
  dispatch runs, then a `ContractError` is raised naming `NewTool`. [traces: UC-E1]
- **AC-5**: Given a contract that declares `allowed_tools:` for an agent whose `.md`
  file is missing or has no `tools:` frontmatter, when dispatch runs, then a warning
  is written to stderr, `resolved_allowed_tools` is `[]`, and no exception is raised.
  [traces: UC-E2]
- **AC-6**: Given a contract with `agent: inline` and `allowed_tools: [Read]`, when
  dispatch runs, then a warning is written to stderr, `resolved_allowed_tools` is `[]`,
  and the action dict is otherwise well-formed. [traces: UC-E3]
- **AC-7**: Given a contract with `allowed_tools: []` (explicit empty list), when
  dispatch runs, then behavior is identical to a contract with the field absent —
  `resolved_allowed_tools` equals the sorted full role tool list. [traces: UC-E4]

## Alternatives Considered

**Alternative 1: Intersect in `_load_contract()` at parse time.**
Rejected. The parser would need to load the agent frontmatter to compute intersection,
inverting the module hierarchy and breaking its pure-read-of-YAML responsibility.

**Alternative 2: Duplicate `_load_agent_tools` inside `dispatch.py`.**
Rejected. Two copies of the same search/parse logic is unnecessary duplication. A tiny
shared `resolver.py` is the minimal clean extraction.

**Alternative 3: Persist `resolved_allowed_tools` in `step_events` (DDL migration) so
cost anomaly detection uses the allowlist enforced at the time of the run.**
Deferred (see Decisions / OQ-4). Report-time re-read of current contracts is
acceptable for M1; historical drift is a known limitation.

**Alternative 4: Enforcement at spawn time (modify orchestrate skill to pass narrowed
`allowed_tools` to the Agent tool).**
Deferred. M1 publishes `resolved_allowed_tools` in the action dict so the skill can
adopt enforcement in a follow-up without further dispatcher changes.

## Impact

- No breaking changes. All 33 existing contracts run unchanged; they simply gain a
  `resolved_allowed_tools` key in their action dict carrying the sorted full role list.
- No DDL migration. `tool_calls.step_id` is already populated.
- New public symbol: `resolver.load_agent_tools`. `cost_report._load_agent_tools` is
  removed (internal, no external callers).

## Decisions

- `allowed_tools: []` (explicit empty list) is semantically equivalent to omitting the
  field → rationale: empty list as "deny everything" is a foot-gun with no current
  use case; a future explicit opt-out can use a distinct sentinel.
- Widening validation lives in `dispatch.py`, not `_load_contract()` → rationale: the
  parser has no access to agent frontmatter; dispatch already resolves the agent name.
- `agent: inline` + `allowed_tools:` emits a runtime warning rather than a
  `ContractError` → rationale: inline steps do not spawn tool-using agents, so a hard
  error overstates the problem; `orchestrator doctor` can harden this later (OQ-7).
- No schema migration for `step_events` → rationale: report-time detection satisfies
  the acceptance criteria; eager persistence can be added later without contract
  changes.
- No wildcard syntax in `allowed_tools` values this iteration → rationale: exact names
  keep the anomaly check trivial; wildcards are additive and can ship later.
- The orchestrate skill's consumption mechanism (Agent-tool parameter vs. prompt
  constraint) is out of scope for this iteration → rationale: M1's contract is the
  action-dict key; the skill is a separate concern and does not block this feature.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
