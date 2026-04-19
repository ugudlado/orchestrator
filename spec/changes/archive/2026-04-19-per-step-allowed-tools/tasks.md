# Tasks: Per-Step allowed_tools Enforcement

- [x] T-1 Write tests: `StepContract.allowed_tools` field + shared `resolver.load_agent_tools` (RED — tests must fail)
  - **Files**: `config/scripts/orchestrator_next/tests/test_parser.py` (extend), `config/scripts/orchestrator_next/tests/test_resolver.py` (new)
  - **Why**: FR-1, FR-2, FR-3, AC-7, UC-E4 — parser surfaces the new field with correct defaults; resolver is the single source of truth for role tool sets.
  - **Scenarios**:
    - Parser: contract without `allowed_tools:` → `contract.allowed_tools == []`.
    - Parser: contract with `allowed_tools: null` → `contract.allowed_tools == []`.
    - Parser: contract with `allowed_tools: []` → `contract.allowed_tools == []`.
    - Parser: contract with `allowed_tools: [Read, Grep]` → list preserved in declared order.
    - Resolver: agent file with valid `tools:` frontmatter returns a `set[str]`.
    - Resolver: missing file returns `None`.
    - Resolver: frontmatter without `tools:` key returns `None`.
    - Resolver: malformed YAML returns `None` (no exception).
    - Resolver: search order — `$ORCHESTRATOR_HOME/agents/` wins over `~/.claude/agents/`.
  - **Verify**: `pytest config/scripts/orchestrator_next/tests/test_parser.py config/scripts/orchestrator_next/tests/test_resolver.py` runs and FAILS (red) for the right reason (missing field / missing module).

- [x] T-2 Implement: add `allowed_tools` to `StepContract`; create `resolver.py` with relocated `load_agent_tools`; update `cost_report.py` to import from `resolver` (GREEN) (depends: T-1)
  - **Files**: `config/scripts/orchestrator_next/parser.py`, `config/scripts/orchestrator_next/resolver.py` (new), `config/scripts/orchestrator_next/cost_report.py`
  - **Why**: FR-1, FR-2, FR-3 — make T-1 pass without duplicating agent-frontmatter parsing.
  - **Approach**:
    - `StepContract`: add `allowed_tools: list[str] = field(default_factory=list)` following the `inputs`/`outputs` pattern.
    - `_load_contract()`: `allowed_tools=data.get("allowed_tools", []) or []` (the `or []` handles explicit YAML null).
    - `resolver.py`: move the body of `cost_report._load_agent_tools` verbatim; rename to `load_agent_tools`; preserve search order (`$ORCHESTRATOR_HOME/agents/` → `~/.claude/agents/`), graceful `None` on any parse failure.
    - `cost_report.py`: delete the private copy; `from .resolver import load_agent_tools`; update the one existing call site.
  - **Verify**: all T-1 tests green; `pytest` entire orchestrator_next suite stays green; `mypy` / type-check clean.

- [x] T-3 Write tests: dispatch `resolved_allowed_tools` intersection + widening guard + graceful degradation (RED) (depends: T-2)
  - **Files**: `config/scripts/orchestrator_next/tests/test_dispatch.py` (extend)
  - **Why**: FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, AC-1, AC-2, AC-4, AC-5, AC-6, AC-7 — wire the narrowing contract into every action-dict branch.
  - **Scenarios**:
    - `allowed_tools: [Read, Grep, Glob, Bash]` against full developer role → action dict has `resolved_allowed_tools == ["Bash", "Glob", "Grep", "Read"]`.
    - No `allowed_tools:` declared → `resolved_allowed_tools` equals sorted full role list.
    - `allowed_tools: []` declared → identical to absent (backward-compat equality).
    - Widening attempt (`[NewTool]` not in role) → `ContractError` with `NewTool` named in the message.
    - Role unresolvable (agent file missing) → stderr warning emitted, `resolved_allowed_tools == []`, no exception.
    - `agent: inline` + `allowed_tools: [Read]` → stderr warning, `resolved_allowed_tools == []`, action dict otherwise well-formed.
    - All 4 action-dict construction sites (`run_inline`, `run_step` new path, `run_step` legacy `contract.run` path, `retry_step`) carry the `resolved_allowed_tools` key.
  - **Verify**: tests run and FAIL (red) — dispatch does not yet emit the key.

- [x] T-4 Implement: wire `resolved_allowed_tools` into `dispatch.py` (GREEN) (depends: T-3)
  - **Files**: `config/scripts/orchestrator_next/dispatch.py`
  - **Why**: FR-4 through FR-9 — produce the narrowed list at every dispatch exit.
  - **Approach**:
    - Import `resolver.load_agent_tools`.
    - Compute `resolved_allowed_tools` once per dispatch using the intersection pseudocode from design.md § Components; raise `ContractError` on widening; warn to stderr for the two graceful-degradation cases.
    - Inject `resolved_allowed_tools` into all 4 action-dict build sites. Do not thread the value through function signatures if a single local variable above the branches works.
    - No signature changes on `dispatch()` exports; this is a pure additive key.
  - **Verify**: T-3 green; pre-existing dispatch tests still green; type-check clean; `orchestrator next` on an unmodified workspace produces an action dict whose other keys are byte-identical modulo the new `resolved_allowed_tools` key.

- [x] T-5 Write tests: cost-report "Tool not in step allowlist" anomaly subsection (RED) (depends: T-2)
  - **Files**: `config/scripts/orchestrator_next/tests/test_cost_report.py` (extend)
  - **Why**: FR-10, FR-11, AC-3 — report-time detection of drift against the step's declared allowlist, rendered as a distinct subsection.
  - **Scenarios**:
    - `tool_calls` row exists for `(step_id=X, tool=WebSearch)` and step X's contract declares `allowed_tools: [Read, Grep, Glob]` → aggregate result includes a row in the new `anomalies_step_allowlist` list naming agent, tool, step, and call count; the existing `anomalies` ("not in role") list is unaffected.
    - Contract for step X has empty `allowed_tools` → no entry in the new list even if the tool is exotic (no false positives).
    - Contract for step X cannot be located at report time → row is silently skipped (no crash, no entry).
    - Rendered markdown contains a "Tool not in step allowlist" subsection header only when that list is non-empty; "Tool not in role" subsection continues to render as before.
  - **Verify**: tests run and FAIL (red) — function and subsection do not yet exist.

- [x] T-6 Implement: add `_step_allowlist_anomalies` and render new Anomalies subsection in `cost_report.py` (GREEN) (depends: T-5)
  - **Files**: `config/scripts/orchestrator_next/cost_report.py`
  - **Why**: FR-10, FR-11 — close the observability loop.
  - **Approach**:
    - Implement `_step_allowlist_anomalies(conn, repo_root, change_id)` per design.md § Components (SQL group-by on `(phase, step_id, agent_name, tool_name)`; skip rows whose contract has empty `allowed_tools`; skip silently if contract is missing).
    - Hook into `aggregate_feature()` alongside the existing `_anomalies()` call; attach under a new key `anomalies_step_allowlist`.
    - Extend `render_markdown_feature()` Anomalies block with a second subsection titled "Tool not in step allowlist", rendered only when the list is non-empty. Leave the existing "Tool not in role" subsection untouched.
  - **Verify**: T-5 green; existing cost-report tests still green; `orchestrator cost` on a seeded DuckDB fixture produces the new subsection when drift is present and omits it when clean.

- [x] T-7 Review checkpoint (phase gate) (depends: T-6)
  - **Verify**:
    - `pytest config/scripts/orchestrator_next/tests -q` all green.
    - Coverage ≥ 90% overall; `resolver.py` at 100%.
    - Type-check clean on all modified modules.
    - `orchestrator next` smoke run against an unmodified contract in the repo produces an action dict containing `resolved_allowed_tools` equal to the agent's sorted full tool list (backward-compat spot check).
    - `orchestrator cost --change-id <recent>` renders the Anomalies section with both subsections correctly when drift is seeded.
    - Architect signoff pass reads spec.md, design.md, and git diff; no unjustified complexity added; existing module boundaries respected.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->

<!-- VERIFICATION BUGS: If verification reveals new issues, add them as tasks -->
<!-- before proceeding. Do NOT skip ahead. -->
