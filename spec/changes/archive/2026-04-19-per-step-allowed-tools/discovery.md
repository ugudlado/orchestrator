---
feature-id: per-step-allowed-tools
linear-ticket: HL-295
---

# Discovery Brief: Per-Step allowed_tools Enforcement

## Feature Summary

Step contracts today assign a fixed `agent:` but cannot restrict which tools that agent uses for the specific step. Every invocation of the developer role, for example, gets the full 13-tool list — even when a particular step only needs Read, Grep, and Glob. This feature adds an optional `allowed_tools:` field to step contracts. The dispatcher intersects this list with the agent role's full frontmatter tools to produce `resolved_allowed_tools` in the action dict. The orchestrate skill then uses this narrowed set when spawning the agent. The cost report's anomaly section is extended to flag tools used outside the step's allowlist (in addition to the existing "tool not in role" check).

## Personas & Actors

- **Workflow author** (primary) — an engineer writing or editing step contracts who wants to declare minimal-privilege tool access for a specific step to reduce risk and cost.
- **Orchestrate skill runner** — the shell/agent executing `orchestrator next` and spawning agents; it must receive and honor `resolved_allowed_tools` from the action dict.
- **Cost-report consumer** — an engineer reading `orchestrator cost` output who wants to know when an agent used a tool it shouldn't have needed for that step.
- **Platform developer** — a developer maintaining the orchestrator codebase who needs confidence that existing contracts without `allowed_tools:` continue working.

## Use Cases

### Happy Path

UC-1: Narrowed developer step — a workflow author adds `allowed_tools: [Read, Grep, Glob, Bash]` to a contract whose agent is `developer`. The dispatcher loads the developer role's frontmatter (13 tools), intersects, and puts `resolved_allowed_tools: [Bash, Glob, Grep, Read]` in the action dict. The orchestrate skill spawns the agent with only those four tools.

UC-2: Backward-compatible run — an existing step contract has no `allowed_tools:` field. The dispatcher omits intersection, includes the full role tool list in `resolved_allowed_tools`, and behavior is identical to today.

UC-3: Cost report anomaly flagged — an agent calls `WebSearch` during a step whose contract declared `allowed_tools: [Read, Grep, Glob]`. At report time `orchestrator cost --change-id <cid>` includes a new anomaly row: "developer used WebSearch (1 call) — not in step allowlist for step write-spec".

### Error & Edge Cases

UC-E1: Widening attempt caught — a contract declares `allowed_tools: [WebSearch, NewTool]` where `NewTool` is not in the developer role's frontmatter. The dispatcher (or contract loader) raises a `ContractError`: "allowed_tools declares 'NewTool' which is not in agent 'developer' frontmatter tools — step contracts cannot widen the agent's tool set".

UC-E2: Agent frontmatter missing — a step contract declares `allowed_tools:` but the agent `.md` file doesn't exist or has no `tools:` in frontmatter. The dispatcher must degrade gracefully: emit a warning to stderr and fall back to the full role list (same as `cost_report._load_agent_tools` returning None).

UC-E3: Inline step sentinel — a step has `agent: inline` and declares `allowed_tools:`. The dispatcher skips intersection (inline steps have no agent frontmatter) and should warn that `allowed_tools:` on inline steps is ignored.

UC-E4: Explicit empty `allowed_tools: []` — a contract declares `allowed_tools:` as an explicit empty list rather than omitting the field entirely. The semantic question is whether `[]` means "no tools allowed" (agent spawns with zero tools) or "no restriction, use full role list" (same as absent). The current design treats empty list as "no restriction" — the architect must confirm this interpretation.

## Scope

### In Scope

- `allowed_tools: list[str]` field added to `StepContract` dataclass (optional, default `[]`).
- `_load_contract()` in `parser.py` populates the new field from YAML; absent → empty list.
- `_load_agent_tools()` refactored out of `cost_report.py` into a shared location (new `resolver.py` or `parser.py`) to avoid duplicating file-search logic.
- Dispatcher (`dispatch.py`) calls agent tool resolution and performs intersection when `contract.allowed_tools` is non-empty; puts `resolved_allowed_tools` in all three action dict variants (`run_inline`, `run_step`, `retry_step`).
- Widening validation: if a name in `contract.allowed_tools` is not present in the agent's frontmatter tools, raise `ContractError`.
- Anomaly detection in `cost_report.py` extended with a per-step check: for each `(step_id, agent_name, tool_name)` row in `tool_calls`, cross-reference the step's contract `allowed_tools`; emit a new anomaly category distinguishable from the existing "not in role" check.
- Backward compatibility: all existing contracts (no `allowed_tools:`) produce `resolved_allowed_tools` equal to the full role list — no behavior change.
- Tests updated/added for: parser contract loading (with and without field), dispatch intersection, widening error, backward-compat, and anomaly detection new check.

### Out of Scope

- Changes to the DuckDB schema (`step_events`, `tool_calls`) — anomaly detection reads contracts at report-time; no new column needed for M1.
- Persisting `resolved_allowed_tools` in `step_events` (schema migration) — deferred, see OQ-4.
- Wildcard or prefix matching in `allowed_tools` values (e.g., `mcp__*`).
- MCP server prefix aliasing — MCP tool names like `mcp__plugin_context7_context7__query-docs` must be listed verbatim.
- Per-flag or runtime-conditional `allowed_tools` (e.g., allowed_tools depends on a flag value).
- `orchestrate` skill changes — how `resolved_allowed_tools` is consumed when spawning agents is an Open Question; this feature's scope is to produce the correct value in the action dict. The skill is a separate concern.
- New CLI flags or subcommands.

## UI Direction

N/A — no UI components. All changes are in Python modules and YAML contracts.

## Key Decisions

**Build or reuse?** Build new additions, extend existing code. All three affected modules (`parser.py`, `dispatch.py`, `cost_report.py`) are the right and only extension points. There is no external library for step-contract tool scoping — this is a bespoke internal concern.

**Where does `_load_agent_tools` live?** It currently lives in `cost_report.py` (lines 62-102) as a private function. Both the dispatcher (which needs to perform intersection) and cost_report (which needs to detect anomalies) will now require it. The shared function should be moved to a new `config/scripts/orchestrator_next/resolver.py` module (or into `parser.py`). Keeping it in `cost_report.py` and importing it from dispatch would create an upward dependency (`dispatch → cost_report`) that inverts the module hierarchy. A shared `resolver.py` is cleaner.

**Where does intersection happen?** In `dispatch.py`, not in `_load_contract()`. The contract loader is a pure file parser — it should not need access to agent frontmatter. Dispatch already knows the resolved agent name from the contract and is the right place to apply role-level context.

**Validation placement:** Widening validation (step declares a tool the role doesn't have) belongs in `dispatch.py` at intersection time, not in `_load_contract()`. The contract parser does not know the role's tool list — that requires the agent file lookup that dispatch performs.

## Open Questions

OQ-1: **How does the orchestrate skill consume `resolved_allowed_tools`?** The `~/.claude/skills/orchestrate/SKILL.md` file was inaccessible during discovery (read permission denied). The idea.md lists two options: pass as the Agent tool's `allowed_tools` parameter, or include in the prompt as a constraint. The architect must confirm which mechanism the skill uses or should adopt. This is the most consequential open question for end-to-end enforcement.

OQ-2: **MCP tool name format in `allowed_tools:`** — MCP tools have compound names like `mcp__plugin_context7_context7__query-docs`. Should `allowed_tools:` in contracts require the full compound name, or support a server-prefix wildcard (e.g., `mcp__plugin_context7_context7__*`)? The current anomaly detection uses exact name matching. Recommend starting with exact names; wildcards deferred.

OQ-3: **Anomaly report: "not in role" vs "not in step allowlist" — unified or separate tables?** The existing `_anomalies()` function produces one list. The new check produces a second list. Should they be rendered as distinct subsections in the Anomalies section, or merged with a `reason` column? The acceptance criteria say "distinguishes" them — separate subsections is the clearest expression.

OQ-4: **Historical accuracy of step-allowlist anomaly detection** — detecting anomalies at report-time by re-reading current contracts is fragile: if a contract's `allowed_tools:` changes after a run, the report reflects the new allowlist, not what was enforced at spawn time. Should `resolved_allowed_tools` be persisted in `step_events` (new column, DDL migration) for accurate historical reporting? Deferring to architect.

OQ-5: **`agent: inline` with `allowed_tools:` declared** — should this be a `ContractError` at load time, or a runtime warning at dispatch time? The difference is whether CI (doctor check) catches it eagerly or only when the step runs.

OQ-6: **Semantics of `allowed_tools: []`** — the proposed algorithm treats an empty list as "no restriction" (uses full role list). This means a contract author who writes `allowed_tools: []` gets the same behavior as omitting the field. Is this the intended semantics, or should `[]` mean "no tools allowed" (explicit zero-tool constraint)? The design should define this unambiguously so authors know whether omitting the field and writing `[]` differ.

OQ-7: **`orchestrator doctor` integration** — should the doctor command gain a new check that validates `allowed_tools` entries against the named agent's frontmatter for all step contracts? This would catch widening errors statically at CI time without running `orchestrator next`.

## What Already Exists

### Codebase

- `config/scripts/orchestrator_next/parser.py:22-36` — `StepContract` dataclass. Adding `allowed_tools: list[str] = field(default_factory=list)` follows the same pattern as `inputs` and `outputs` added in HL-287.
- `config/scripts/orchestrator_next/parser.py:89-129` — `_load_contract()`. Already reads optional fields (`inline`, `inputs`, `outputs`) with backward-compatible defaults. Adding `allowed_tools: data.get("allowed_tools", [])` is a two-line change.
- `config/scripts/orchestrator_next/dispatch.py:241-296` — action dict construction. All three branches (`run_inline`, `run_step`, and the `elif contract.run` run_step variant) are where `resolved_allowed_tools` must be inserted. The retry path (lines 188-215) also needs it.
- `config/scripts/orchestrator_next/cost_report.py:62-102` — `_load_agent_tools(agent_name)`. Already implements the two-location search (`$ORCHESTRATOR_HOME/agents/` → `~/.claude/agents/`), frontmatter YAML parsing, and graceful degradation on missing/invalid files. This is the exact function needed by dispatch for intersection.
- `config/scripts/orchestrator_next/cost_report.py:264-292` — `_anomalies()`. Per-agent tool check. The new per-step allowlist check follows the same SQL + loop pattern: group `tool_calls` by `(phase, step_id, agent_name, tool_name)`, load the contract for each unique `(phase, step_id)`, check `tool_name` against `contract.allowed_tools`.
- `agents/developer.md:6` — `tools:` list (13 entries). Format: JSON array in frontmatter. `_load_agent_tools()` already parses this correctly.
- `agents/discoverer.md:6` — `tools:` list (9 entries, no WebSearch or Write). Narrower than developer — confirms step-level restriction is meaningful.
- `config/steps/*.yaml` — 33 step contracts. None currently declare `allowed_tools:`. All will default to role's full list. Backward compatibility is trivially guaranteed.
- `config/scripts/orchestrator_next/upsert.py:53-70` — `_DDL_TOOL_CALLS` schema. Has `phase` and `step_id` columns, enabling the per-step anomaly query.

### External

Searched for "Claude Agent SDK allowed_tools step-level restriction" and "workflow engine per-step tool scoping". The Claude Agent SDK (`allowed_tools` parameter on Agent tool calls) is the natural enforcement point. No external library provides per-step tool scoping — this is bespoke to the orchestrator contract system.

## Approaches Considered

### Approach A: Intersect at dispatch time, load agent tools in dispatch.py (inline the function)

Copy or re-implement `_load_agent_tools` inside `dispatch.py`. Perform intersection in `dispatch()` before building action dict. Keep `cost_report.py`'s copy unchanged.

- Pros: Isolated change; no refactoring across modules.
- Cons: Duplicates `_load_agent_tools` logic (two copies to maintain). Violates DRY within the same codebase.
- Effort: Small.

### Approach B: Extract shared resolver, intersect at dispatch time (recommended)

Move `_load_agent_tools` from `cost_report.py` to a new `config/scripts/orchestrator_next/resolver.py`. Have `dispatch.py` import it for intersection and `cost_report.py` import it for anomaly detection. `dispatch.py` performs intersection when `contract.allowed_tools` is non-empty; emits `resolved_allowed_tools` in the action dict.

- Pros: No duplication. Clean module boundary. `cost_report.py` dependency on `resolver.py` is natural (upward read). `dispatch.py` dependency on `resolver.py` is clean (lateral utility).
- Cons: One additional module file. Slightly wider change surface.
- Effort: Small-Medium.

### Approach C: Intersect at contract load time, pass agent name as parameter

`_load_contract()` accepts an optional `agent_tools: set[str] | None` parameter and performs intersection internally. The caller (dispatch.py) resolves agent tools before calling `_load_contract`.

- Pros: Contract object is already fully resolved when returned.
- Cons: `_load_contract` becomes stateful/context-dependent. Breaks the parser's pure-read-of-YAML responsibility. Complicates test fixtures. Intersection at load time means the loader must know the agent name — but the agent name is in the YAML, so it could self-load, creating a circular call. Not recommended.
- Effort: Medium (complexity is disproportionate).

## Recommendation

**Approach B.** Extract `_load_agent_tools` into `resolver.py`, perform intersection in `dispatch.py`, import in `cost_report.py`. This is the smallest change that avoids duplication and respects module boundaries. The new `resolver.py` is a utility module — no new abstractions, just relocation of one function.

The anomaly detection extension should add a second function `_step_allowlist_anomalies()` in `cost_report.py` (alongside the existing `_anomalies()`), called from `aggregate_feature()` and rendered as a separate subsection in the Anomalies section.

The `allowed_tools:` field should default to `[]` (empty list = "no restriction, use full role list") for backward compatibility. Intersection only applies when the list is non-empty.

## Technical Context

| File | Role | Key Lines |
|------|------|-----------|
| `config/scripts/orchestrator_next/parser.py` | Add `allowed_tools: list[str]` field to `StepContract`; populate in `_load_contract()` | L22-36, L89-129 |
| `config/scripts/orchestrator_next/dispatch.py` | Perform intersection in `dispatch()`; emit `resolved_allowed_tools` in action dict | L241-296; retry path L188-215 |
| `config/scripts/orchestrator_next/cost_report.py` | Add `_step_allowlist_anomalies()`; extend `aggregate_feature()`; update `render_markdown_feature()` Anomalies section | L264-292, L358-375, L636-647 |
| `config/scripts/orchestrator_next/resolver.py` | New module: relocated `_load_agent_tools()` shared by dispatch and cost_report | (new) |
| `agents/developer.md` | Reference: 13-tool list that step contracts can restrict | L6 |
| `agents/discoverer.md` | Reference: 9-tool list | L6 |
| `config/steps/*.yaml` | 33 contracts — none declare `allowed_tools:` today; all must work unchanged | all |
| `~/.claude/skills/orchestrate/SKILL.md` | Skill that consumes action dict and spawns agents — inaccessible; skill consumption mechanism is OQ-1 | unknown |

### Action dict today (run_step branch, dispatch.py L269-281):
```json
{
  "action": "run_step",
  "step_id": "...",
  "phase": "...",
  "attempt": 1,
  "agent": "developer",
  "run": "...",
  "instruction": "...",
  "rules": [...],
  "inputs": {...},
  "expected_outputs": [...],
  "env": {...}
}
```

### Action dict after this feature (new field):
```json
{
  ...all existing fields...,
  "resolved_allowed_tools": ["Bash", "Glob", "Grep", "Read"]
}
```

When `contract.allowed_tools` is empty, `resolved_allowed_tools` contains the full role tool list.

### `_load_agent_tools` search order (cost_report.py:62-102, to be moved to resolver.py):
1. `$ORCHESTRATOR_HOME/agents/<agent_name>.md`
2. `~/.claude/agents/<agent_name>.md`

Returns `set[str] | None`. None = skip (file missing, no frontmatter, bad YAML, no `tools:` key).

### Intersection algorithm (pseudocode):
```python
role_tools = _load_agent_tools(contract.agent)  # set[str] | None
if contract.allowed_tools and role_tools is not None:
    # Widening guard: all declared tools must be in role's set
    illegal = set(contract.allowed_tools) - role_tools
    if illegal:
        raise ContractError(f"allowed_tools declares {illegal\!r} not in agent '{contract.agent}' tools")
    resolved = sorted(set(contract.allowed_tools) & role_tools)
elif role_tools is not None:
    resolved = sorted(role_tools)
else:
    resolved = []  # no frontmatter — degrade gracefully
```

### New anomaly detection SQL (per-step check):
```sql
SELECT phase, step_id, agent_name, tool_name, COUNT(*) AS calls
FROM tool_calls
WHERE repo_root = ? AND change_id = ?
GROUP BY phase, step_id, agent_name, tool_name
```
For each `(phase, step_id)` row: load contract, check if `contract.allowed_tools` is non-empty; if so, flag any `tool_name` not in it.
