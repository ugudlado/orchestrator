---
feature-id: orc-86
linear-ticket: ORC-86
---

# Discovery Brief: Strip agent-protocol fields from script-kind step contracts

## Feature Summary

Script-kind step contracts under `config/steps/*/contract.yaml` only need `id`, `kind`, `run` (plus optional `version`, `flags_read`) for the dispatcher to execute them. Fields like `inputs`, `outputs`, `rules`, `instruction`, and `verify` are agent-protocol surface — the script dispatcher path never consumes them for execution. They are documentation at best and drift bait at worst (rules that look authoritative but the engine ignores). This change removes those fields from the 8 script-kind contracts and adds a test that locks the schema-minimal shape in place. Agent-kind contracts are out of scope.

## Personas & Actors

- **Orchestrator engine maintainer** — needs the agent-vs-script contract distinction to be explicit and self-enforcing so future edits don't reintroduce drift.
- **Workflow author** (writing step contracts and workflows) — needs an unambiguous template for what belongs in a script contract.
- **Dispatcher / `record.py` / `generate_plan.py`** — system actors that read contracts; their behavior must not regress.

## Use Cases

### Happy Path

UC-1: Strip empty IO from minimal script contracts — Maintainer wants to remove `inputs: []` / `outputs: []` from the 7 already-minimal contracts (archive-completed-change, capture-test-baseline, compute-prediction-accuracy, compute-swe-metrics, expand-plan, mark-change-completed, preview-route) so that the canonical shape contains only `id`, `kind`, `run` (and `version`).

UC-2: Strip agent-protocol surface from complete-workflow — Maintainer wants to remove the `rules:`, `instruction:`, `verify:`, and the 9-line `# No outputs:` explanatory comment from `complete-workflow/contract.yaml`, leaving only `id`, `kind`, `run`, `version`, so the script-vs-agent distinction is implicit in the schema.

UC-3: Lock schema-minimal shape with a regression test — Maintainer wants a test under `config/steps/__tests__/` that walks every `kind: script` contract and asserts none of `inputs`, `outputs`, `rules`, `instruction`, `verify` is present, so future edits can't silently reintroduce the drift.

UC-4: Verify end-to-end workflow still completes — Maintainer wants `complete-workflow` to dispatch and the workflow to archive cleanly after the comment/`rules:` block is removed, proving `generate_plan.py` and the wrapper don't depend on the stripped keys for script steps.

### Error & Edge Cases

UC-E1: Existing contract regression test must still pass — `test_all_contracts_have_agent_or_run.py` asserts every step has either `agent:` or `run:`. Removing only the agent-protocol fields preserves `run: script.sh`, so this test must remain green.

UC-E2: `test_complete_workflow_contract.py::test_complete_workflow_contract_shape` already asserts `contract.get("outputs") in (None, [])` for complete-workflow. After this change `outputs` is absent (None), so the test continues to pass — but the assertion's accompanying explanatory comment in the test (lines 50–56) and the now-obsolete reference to the contract's own `# No outputs:` comment block should be reviewed; either keep the comment as historical context or trim it to match the new "script contracts declare no outputs" rule.

UC-E3: `generate_plan.py._build_step_block` reads `contract_raw.get("inputs") or []` and `contract_raw.get("outputs") or []` when emitting workflow_plan nodes. With keys absent the `or []` default kicks in — no behavior change for script nodes. But verify by running a real workflow that the emitted plan node for a script step still has `inputs: []` / `outputs: []` (or whatever the new convention is) and `dispatch.py` is unaffected.

## Scope

### In Scope

- Edit 7 minimal script contracts to remove `inputs: []` and `outputs: []`:
  - `config/steps/archive-completed-change/contract.yaml`
  - `config/steps/capture-test-baseline/contract.yaml`
  - `config/steps/compute-prediction-accuracy/contract.yaml`
  - `config/steps/compute-swe-metrics/contract.yaml`
  - `config/steps/expand-plan/contract.yaml`
  - `config/steps/mark-change-completed/contract.yaml`
  - `config/steps/preview-route/contract.yaml`
- Edit `config/steps/complete-workflow/contract.yaml` to remove `inputs: []`, `outputs: []`, the `rules:` list, the `instruction:` block, the `verify:` list, and the trailing `# No outputs:` explanatory comment block (lines 39–47 of current file).
- Add a new contract test (e.g. `config/steps/__tests__/test_script_contracts_minimal.py`) that loads every `config/steps/*/contract.yaml` with `kind: script` and asserts none of `{inputs, outputs, rules, instruction, verify}` is present.
- Verify existing tests still pass: `test_all_contracts_have_agent_or_run.py`, `test_complete_workflow_contract.py`, and the broader `config/scripts/orchestrator_next/tests/` suite.
- Run an end-to-end workflow (this feature, or a small dry-run) through `complete-workflow` to prove no dispatcher `KeyError` arises from missing fields.

### Out of Scope

- **Agent-kind contracts** (`execute-one-task`, `design-and-draft-artifacts`, `explore`, `diagnose`, `run-phase-review`, `run-ux-critique`, `ux-design`, `run-learn-cycle`) — these contracts genuinely use `inputs`/`rules`/`instruction` via `dispatch.py._resolve_inputs` and `parser.py` instruction/rules threading. Touching them risks regressing agent dispatch and is the subject of a separate follow-up (decide whether to move agent rules into `prompt.md`).
- **Moving `complete-workflow`'s rules/instruction/verify into a `prompt.md` or docs file** — the rules were never executed; archive them in commit history if useful, but creating a parallel docs surface re-creates the drift problem this change exists to solve.
- **Changing `generate_plan.py` or `parser.py`** — they handle missing keys via `or []` defaults; no engine changes are needed and any change here would expand scope.
- **Adjusting the `select-workflow.yaml` flat-form contract** — it's an `excluded` step (per `test_all_contracts_have_agent_or_run.py`) and not dispatched.
- **`# No outputs:` explanatory comment relocation** — the rationale (state-mutating pre-record, `_check_declared_outputs` interplay) is preserved in commit history and in `test_complete_workflow_contract.py`'s own docstring; no new doc target needed.

## UI Direction

N/A — no UI components. This is a config-only refactor.

## Key Decisions

- **Selected approach: extend the existing contract test** (`test_all_contracts_have_agent_or_run.py`) with a new `test_script_contracts_have_no_agent_protocol_fields` function rather than create a separate `test_script_contracts_minimal.py`. Rationale: co-locates contract-shape rules; reuses the same test artifact; resolves OQ-1 in favor of `config/steps/__tests__/`. Complexity: XS.
- **Delete the `# No outputs:` rationale comment block from `complete-workflow/contract.yaml`** rather than relocate it to a sibling notes file. Rationale: the historical reasoning survives in commit history and in `test_complete_workflow_contract.py`'s docstring; a parallel docs surface would re-create the drift this refactor exists to eliminate. Resolves OQ-2.
- **Retain `version:` field on script contracts as-is.** Outside ORC-86's stated scope per OQ-3.
- **New test does its own glob walk** (`config/steps/*/contract.yaml`) instead of extending the file's existing `_load_contract` helper (which uses a stale flat-form path). Avoids an incidental refactor in this change.

## Open Questions

- OQ-1: Should the new contract test live next to the existing `test_all_contracts_have_agent_or_run.py` (under `config/steps/__tests__/`) or under `config/scripts/orchestrator_next/tests/` alongside `test_complete_workflow_contract.py`? Both work; the former groups contract-schema tests, the latter groups orchestrator-engine tests. Lean: place it in `config/steps/__tests__/` to keep contract-shape assertions co-located with the contracts.
- OQ-2: Does the `# No outputs:` comment block in `complete-workflow/contract.yaml` carry information that should be preserved in `test_complete_workflow_contract.py`'s docstring (which already covers the same ORC-66 pre-record rationale), or is the test docstring sufficient? Lean: test docstring is sufficient; delete the comment block.
- OQ-3: The `version:` field exists on most script contracts (e.g., `version: 7` on archive-completed-change). It is mentioned in the ticket as "optional" but is not grep-confirmed as consumed by the engine. Should it be retained as-is (status quo, low risk) or also audited? Lean: retain as-is; auditing `version:` is a separate concern outside ORC-86's stated scope.
