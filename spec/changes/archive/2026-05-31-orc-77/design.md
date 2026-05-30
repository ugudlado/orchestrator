---
feature-id: orc-77
linear-ticket: ORC-77
---

# Design: Remove agent: inline sentinel — migrate to None-based dispatch

## Context

The orchestrator engine uses the string `"inline"` as a magic sentinel value in
five engine files. It serves two conflicting roles:

1. **Default fallback**: parser.py and generate_plan.py assign `"inline"` when no
   `agent:` field is present in a contract, so downstream code never sees `None`.
2. **Branch condition**: dispatch.py, doctor.py, and record.py compare
   `agent == "inline"` to detect non-spawning steps.

This conflation couples "no agent declared" with a magic string, creates false
positives in metrics (DuckDB stores `"inline"` as if it were a named agent), and
forces every consumer to know about the sentinel. The dispatch path itself
(dispatch.py:585–641) already uses `if contract.agent:` truthiness — it does not
need the sentinel string. Removing the sentinel makes `None` the canonical
representation for "no agent assigned".

The dispatch routing has three modes:
- **Spawn**: `contract.agent` is set → emit agent JSON (exit 0 + JSON)
- **Script**: `contract.run` or `contract.main` is set → execute inline script (exit 0)
- **Error**: neither → `ParserContractDispatchError` (exit 3)

Steps with only an `instruction:` and no `run:` are driver-inline steps (the
driver executes the instruction in-context). These always have `agent: <name>` or
belong to the "spawn" path; the "no agent + instruction" test fixtures simulate the
error case.

## Goals / Non-Goals

### Goals

- Replace `agent == "inline"` sentinel checks with `agent is None` across all five
  engine files (parser.py, generate_plan.py, dispatch.py, record.py, doctor.py).
- Store `NULL` in DuckDB `agent_name` / `agent` column for script steps instead of
  `"inline"`, correctly reflecting the absence of an agent.
- Migrate all test fixtures that carry `agent: inline` (2 contract fixtures, 10+
  state YAML fixtures, 1 SQL baseline fixture).
- Update the `inline-steps-are-tokenless` learning in spec/project.yaml to describe
  the NULL-based pattern.
- Ensure test suite passes with zero regressions after migration.

### Non-Goals

- CONVENTIONS.md, metrics-schema.md, rule-merge.md — these files do not exist in
  the repo; no action required.
- The `inline: bool` field on `StepContract` (parser.py line 58) — this is the
  HL-287 M3 `inline: true + run:` feature flag; it is semantically distinct and
  must not be touched.
- DuckDB schema changes: the `agent` column already accepts NULL; no DDL change needed.
- Changes to workflow YAML schemas or step directory structure.
- compute-swe-metrics step logic — NULL agent steps are already handled by the
  step's bucketing; no code change required there.

## Approaches Considered

### Approach 1: None-replacement (hard removal)

Remove `"inline"` as default value and as comparison target across all five engine
files. Change defaults to `None`. Change `!= "inline"` to `is not None`. Change
`== "inline"` to `is None`. Migrate all fixtures.

- **Pros**: Clean semantic model. `None` means "no agent" — no sentinel knowledge
  required by consumers. DuckDB stores NULL correctly.
- **Cons**: Requires per-site logic analysis — some sites use `!= "inline"` 
  (negation) while others use `== "inline"` (positive check); mechanical
  find-replace introduces bugs.
- **Complexity**: M

### Approach 2: Alias (keep sentinel as accepted input, add None support)

Accept both `None` and `"inline"` as equivalent in all dispatch/record paths.
Normalize to `None` at parse time but keep `"inline"` as a recognized alias in
record.py validation and doctor.py.

- **Pros**: Backward compatible with any external state.yaml files that carry
  `agent: inline` in step_history.
- **Cons**: No production contracts use the explicit sentinel; no external consumers
  exist. Keeping the alias preserves complexity with no benefit.
- **Complexity**: M

### Approach 3: Named sentinel replacement ("script")

Replace `"inline"` with `"script"` as the canonical string for non-agent steps.
Change DuckDB storage to `"script"`.

- **Pros**: Explicit string is more readable than NULL in DuckDB queries.
- **Cons**: Introduces a new sentinel to replace the old one. NULL is semantically
  correct and is standard SQL for "no value". Any downstream query writer must learn
  the new string.
- **Complexity**: M

### Selected Approach

**Approach 1 (None-replacement)** is selected.

Complexity is equal across all three (M). Approach 1 has the highest module reuse:
it modifies 5 existing files without adding abstractions. Approach 2 preserves dead
code. Approach 3 replaces one magic string with another.

Constraint that ruled out Approach 2: no production contracts carry `agent: inline`
explicitly — grepping `config/steps/` returned zero matches. The alias buys nothing.

Constraint that ruled out Approach 3: `NULL` is SQL-canonical for "absent"; a named
string like `"script"` re-introduces the sentinel anti-pattern. DuckDB's `agent`
column already NULLs are handled correctly by `_compute_cost_usd` (pricing.py:242
takes the fallback path for inline/unknown agents).

## High-Level Design

### Architecture Overview

Five engine files each contain the `"inline"` string as either a default assignment
or a branch condition. The migration touches each file in isolation — there is no
new component, no new interface. The only coordination point is the DuckDB
`agent` column: record.py writes it, and compute-swe-metrics tests read it
(via baseline.duckdb.sql). The baseline fixture must be updated alongside record.py.

```
parser.py        ──(parses contract)──►  StepContract.agent = None  (was "inline")
generate_plan.py ──(builds state node)──► agent field = None         (was "inline")
dispatch.py      ──(routes step)──────►  if agent is None: (tools)  (was == "inline")
record.py        ──(writes state+DB)──►  agent = None in history     (was "inline")
doctor.py        ──(validates)────────►  if name is None: skip       (was == "inline")
```

### Key Abstractions

No new abstractions. The change is: `"inline"` → `None` as the canonical "no agent"
value throughout the engine.

## Low-Level Design

### Components

#### parser.py (line 374)

`_parse_history_entry` defaults `agent` to `"inline"` when absent:
```python
agent=raw.get("agent", "inline"),   # BEFORE
agent=raw.get("agent"),             # AFTER (defaults to None)
```

#### generate_plan.py (lines 276, 280)

`_build_node_for_step` assigns `agent = "inline"` as default, then reads
`contract_raw.get("agent", "inline")`:
```python
agent = "inline"                          # BEFORE (line 276)
agent = None                              # AFTER

agent = contract_raw.get("agent", "inline")  # BEFORE (line 280)
agent = contract_raw.get("agent")            # AFTER
```

#### dispatch.py (line 267)

`_resolve_allowed_tools` guards tool resolution:
```python
if not contract.agent or contract.agent == "inline":  # BEFORE
if contract.agent is None:                             # AFTER
```

The `not contract.agent` clause and the `== "inline"` clause covered the same case;
collapsing to `is None` is correct because an empty string is not a valid agent name
(parser would have rejected it), and truthiness check was the original defense.

#### record.py (lines 1499, 1516, 1520, 1648)

Four sites, two patterns:

**Pattern A — positive sentinel test (flip operator):**
```python
# line 1499
if status == "completed" and contract_agent and contract_agent != "inline":  # BEFORE
if status == "completed" and contract_agent is not None:                      # AFTER

# line 1516
agent = payload.get("agent", "inline")  # BEFORE
agent = payload.get("agent")            # AFTER

# line 1520
if status == "completed" and agent != "inline":  # BEFORE
if status == "completed" and agent is not None:   # AFTER

# line 1648
"agent": payload.get("agent", "inline"),  # BEFORE
"agent": payload.get("agent"),            # AFTER
```

**Critical invariant**: changing line 1516 default from `"inline"` to `None` and
line 1520 check from `!= "inline"` to `is not None` must happen together. A partial
change where the default is `None` but the condition still says `!= "inline"` would
cause `None != "inline"` → True → script steps wrongly enter the agent-token-required
validation branch.

#### doctor.py (lines 227, 241)

`_check_agent_files` iterates contracts and skips inline steps:
```python
if not name or name == "inline":  # BEFORE (line 227)
if name is None:                   # AFTER
```

The `not name` clause was also a guard against empty string — after migration only
`None` is the absent case, so `is None` is the correct check. Empty string is still
an invalid agent name (parse-time error), so removing `not name` is safe.

### Data Flow

```
bin/orchestrator next → dispatch.py → StepContract.agent (None)
                                    → if agent is None: no tool resolution
                                    → emit run: action (no agent key)

bin/orchestrator done → record.py → agent = payload.get("agent")  # None for scripts
                                  → history entry agent: None
                                  → DuckDB: agent_name = NULL
```

### State Management

- `StepContract.agent` transitions from Optional[str] with "inline" default to
  Optional[str] with None default. The field type already allows None.
- `StepHistoryEntry.agent` (parser.py) transitions from always-str to Optional[str].
- DuckDB `agent` column in `step_events`: already nullable; NULL replaces `"inline"`.
- `tests/fixtures/step_contracts/step-inline-only.yaml`: remove `agent: inline`.
- `tests/fixtures/step_contracts/step-typed-io.yaml`: remove `agent: inline`.
- `tests/fixtures/state-*.yaml` (10 files): replace `agent: inline` with absent field.
- `tests/__tests__/fixtures/baseline.duckdb.sql`: replace `'inline'` with `NULL`
  in the `agent` column position for the 8 affected INSERT rows.

### Error Handling

The dispatch.py `else:` branch (line 638) already raises
`ParserContractDispatchError("step_contract_missing_run: ...")` for steps with
neither `agent` nor `run`. This is unchanged — it is the UC-E1 guard.

After migration, a step with `run: None` and `agent: None` (both absent from YAML)
reaches this branch correctly. Previously the parser injected `"inline"` as default
and the dispatch truthiness check `if contract.agent:` would be False, falling through
to `elif contract.run` — so the error path was already reachable. The migration does
not change this; it only removes the intermediate string representation.

## Constraints

- `StepHistoryEntry.agent` type annotation must be updated if it is currently `str`
  (not `Optional[str]`). Verify before implementation.
- baseline.duckdb.sql INSERT row positional format must be preserved exactly; only
  the agent column value changes from `'inline'` to `NULL`.
- Test changes must be surgical: only references to the `"inline"` sentinel string
  need updating. Tests that assert dispatch routing logic (which uses truthiness,
  not the string) are unaffected.

## Trade-offs

- **NULL in DuckDB vs named string**: NULL requires `IS NULL` in queries rather than
  `= 'value'`. This is correct SQL semantics and more explicit than a magic string.
  Any new query author must know to filter on `agent IS NULL` for script steps —
  but this is standard SQL idiom.
- **record.py per-site analysis overhead**: The four record.py sites have two
  different patterns (default assignment vs. comparison), so a mechanical find-replace
  is unsafe. Per-site analysis is required but bounded to 4 lines.

## Acceptance Criteria

- AC-1: Given a step contract with `kind: script` and `run: script.sh` but no `agent:` field, when dispatch.py processes it, then `contract.agent` is `None` and the step is routed to the `elif contract.run` branch without entering the agent-spawn path. [traces: UC-1]
- AC-2: Given a step contract with `agent: developer`, when dispatch.py processes it, then named-agent dispatch proceeds unaffected — `contract.agent` is `"developer"`, not `None`. [traces: UC-2]
- AC-3: Given `orchestrator doctor` runs against a repo with script steps and named-agent steps, when doctor checks agent file presence, then script steps (agent=None) are skipped and only named-agent steps are validated. [traces: UC-3]
- AC-4: Given a completed script step's done-payload with no `agent` field, when record.py processes it, then the state history entry has `agent: null` (or absent) and the DuckDB `step_events` row stores `NULL` in the agent column. [traces: UC-4]
- AC-5: Given a step contract with neither `agent:` nor `run:`, when dispatch.py processes it, then a `ContractDispatchError` is raised (exit 3) — the old inline-fallback path does not silently mask the misconfiguration. [traces: UC-E1]
- AC-6: Given all test fixtures, when `pytest orchestrator_next/tests/ -q` is run, then zero tests fail and no assertions reference `agent_name == "inline"` as a passing assertion. [traces: UC-E2]
- AC-7: Given the baseline.duckdb.sql fixture, when compute-swe-metrics tests run, then the fixture uses `NULL` in the agent column for script steps and the tests pass. [traces: UC-E3]

## Decisions

- NULL over empty string for DuckDB agent column → NULL is SQL-canonical for "no value"; empty string is ambiguous and would require its own guard. Consequence: downstream queries use `agent IS NULL` instead of `agent = ''`.
- Hard removal over alias → no production contracts carry `agent: inline` explicitly; aliasing preserves dead code with no benefit. Consequence: any external state.yaml with `agent: inline` in step_history would parse as agent=None; this is correct.
- Per-site analysis for record.py → four record.py sites have two distinct patterns (default assignment vs comparison); a mechanical find-replace would flip the wrong boolean logic at lines 1499/1520. Consequence: developer must treat each site individually.

## Open Questions

- None — OQ-1 resolved (NULL), OQ-2 resolved (no live consumers of `agent_name = 'inline'` found in compute-swe-metrics script or tests), OQ-3 resolved (test count is 24 test files / 123 occurrences; the discovery brief's "5+ fixtures" was an undercount).
