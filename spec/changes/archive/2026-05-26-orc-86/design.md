---
feature-id: orc-86
linear-ticket: ORC-86
---

# Design: Strip agent-protocol fields from script-kind step contracts

## Context

Step contracts under `config/steps/*/contract.yaml` carry one of two protocols:

- **Agent-kind** (`kind: agent`, `agent: <subagent>`) — the dispatcher reads `inputs`, `rules`, `instruction`, and `outputs` via `dispatch.py._resolve_inputs` and threads them into the spawned subagent's prompt.
- **Script-kind** (`kind: script`, `run: script.sh`) — the dispatcher shells out to `script.sh`; the script reads what it needs from env/state and returns a COMPLETION block. The contract's `inputs`, `outputs`, `rules`, `instruction`, `verify` fields are never read by the script path.

Today, 8 script-kind contracts still carry these agent-protocol fields. Seven contain only empty `inputs: []` / `outputs: []` stubs. The eighth, `complete-workflow/contract.yaml`, carries a full `rules:` list, an `instruction:` block, a `verify:` list, and a 9-line `# No outputs:` explanatory comment. None of it executes; all of it is drift bait — rules and instructions that look authoritative but the engine ignores.

This change removes the unused fields from the 8 script contracts and locks the schema-minimal shape with a regression test. Agent-kind contracts are explicitly out of scope.

## Goals / Non-Goals

### Goals

- Remove `inputs`, `outputs`, `rules`, `instruction`, `verify` from all 8 script-kind contracts so the canonical script-contract shape is `id`, `kind`, `run` (+ optional `version`, `flags_read`).
- Lock that shape with a regression test that fails if any future script contract reintroduces a stripped field.
- Prove the end-to-end workflow still completes after the strip — specifically that `complete-workflow` dispatches and archives cleanly without the removed fields.

### Non-Goals

- Touching agent-kind contracts (`execute-one-task`, `design-and-draft-artifacts`, `explore`, `diagnose`, `run-phase-review`, `run-ux-critique`, `ux-design`, `run-learn-cycle`). Those genuinely consume `inputs`/`rules`/`instruction` via the agent dispatch path.
- Moving `complete-workflow`'s removed `rules:` / `instruction:` / `verify:` content into a sibling `notes.md` or `prompt.md`. The rationale survives in commit history and in `test_complete_workflow_contract.py`'s docstring; re-creating a parallel docs surface re-creates the drift this change exists to eliminate.
- Changing `generate_plan.py`, `dispatch.py`, or `parser.py`. Their `or []` defaults already handle missing keys; no engine change is required.
- Auditing the `version:` field on script contracts. Retained as-is per discovery OQ-3.
- Repairing or renaming the existing `test_all_contracts_have_agent_or_run.py` (which appears to use a stale flat-form path). If it's stale, that's a pre-existing condition; address separately if discovered during verification.

## Approaches Considered

### Approach 1: Two-file test layout

Add a new test file `config/steps/__tests__/test_script_contracts_minimal.py` that walks every `kind: script` contract and asserts none of `{inputs, outputs, rules, instruction, verify}` is present. The existing `test_all_contracts_have_agent_or_run.py` stays untouched.

- Pros: Clear file-naming separation between "has agent or run" and "script-kind minimal-shape" assertions.
- Cons: Two test files asserting overlapping contract shape; small redundancy in fixture/glob setup; one more artifact to maintain.

### Approach 2: Extend the existing contract-shape test

Add a new test function `test_script_contracts_have_no_agent_protocol_fields` inside the existing `test_all_contracts_have_agent_or_run.py`. The file already collects every step contract from disk — reusing the same glob walk and yaml-load helpers means one test artifact carries all contract-shape assertions.

- Pros: Single source of truth for contract-shape rules; reuses existing helpers; one less test file.
- Cons: The existing test file's `_load_contract` helper uses a stale flat-form path (`steps/<id>.yaml`) — extending it forces either a parallel glob or a small refactor. Manageable: the new test can do its own `glob("config/steps/*/contract.yaml")` walk and not depend on the stale helper.

### Approach 3: Strip fields + relocate `complete-workflow` rules into a sibling notes file

Same edits as Approach 1/2, but additionally copy `complete-workflow`'s removed `rules:` / `instruction:` / `verify:` content into a new `config/steps/complete-workflow/notes.md` to preserve the historical rationale on disk.

- Pros: Rationale stays adjacent to the contract for future readers.
- Cons: Explicitly rejected by discovery's Out-of-Scope rationale — a parallel docs surface re-creates the drift problem (rules in a notes file the engine doesn't read are exactly the failure mode this refactor eliminates). The same content lives in commit history and the test docstring.

### Selected Approach

**Approach 2** — extend the existing contract test with a new minimal-shape assertion function. Picked because:

- Lowest complexity tie with Approach 1 (XS); chosen on higher module reuse (one test file edit vs. one new file).
- Co-locates both contract-shape rules ("every contract has agent or run" + "script contracts carry only run") in a single file, so future contract-shape rules have an obvious home.
- The new assertion does its own filesystem walk (`config/steps/*/contract.yaml`), avoiding the stale `_load_contract` helper in the same file — no incidental refactor.

Approach 3 ruled out by discovery's explicit non-goal: the change exists to eliminate parallel-doc drift, not relocate it.

## High-Level Design

### Architecture Overview

This is a config-only refactor. Two surfaces change:

1. **Contract YAMLs** (8 files under `config/steps/<step>/contract.yaml`) — lose the agent-protocol fields.
2. **Contract-shape test** (1 file under `config/steps/__tests__/`) — gains an assertion that locks the new shape.

No engine code (`dispatch.py`, `generate_plan.py`, `parser.py`, `record.py`) changes. The dispatcher's script path already ignores the stripped keys; the agent path is untouched because no agent contract is edited.

### Key Abstractions

- **Script-kind canonical shape**: `{id, kind: script, run, [version], [flags_read]}`. No other keys allowed. Enforced by the new test assertion.
- **Agent-kind canonical shape**: unchanged — `{id, kind: agent, agent, inputs, outputs, rules, instruction, [verify], ...}`. The new test asserts negatively only on `kind: script` contracts and never touches agent contracts.

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs | Dependencies |
|---|---|---|---|---|
| 7 minimal script contracts (`archive-completed-change`, `capture-test-baseline`, `compute-prediction-accuracy`, `compute-swe-metrics`, `expand-plan`, `mark-change-completed`, `preview-route`) | Declare script dispatch with no agent-protocol stubs | n/a (YAML) | Loadable by `yaml.safe_load` | None |
| `complete-workflow/contract.yaml` | Declare script dispatch for the terminal workflow step, no agent-protocol surface | n/a (YAML) | Loadable by `yaml.safe_load` | None |
| New test function `test_script_contracts_have_no_agent_protocol_fields` (in `config/steps/__tests__/test_all_contracts_have_agent_or_run.py`) | Walk every `config/steps/*/contract.yaml`, filter to `kind: script`, assert no banned keys present | Filesystem (glob + yaml load) | pytest pass/fail | `pyyaml`, `pytest` |

### Data Flow

Pre-change: `dispatch.py` reads `contract_raw`; for `kind: script` it only consumes `run` and ignores the rest. `generate_plan.py._build_step_block` reads `contract_raw.get("inputs") or []` and `contract_raw.get("outputs") or []` to emit plan-node IO shape. Post-change: with keys absent, `or []` returns the same `[]` — emitted plan-node shape is unchanged.

Test data flow: new test globs `config/steps/*/contract.yaml`, parses each with `yaml.safe_load`, filters to `kind == "script"`, and asserts `{"inputs", "outputs", "rules", "instruction", "verify"}.isdisjoint(contract.keys())` for each.

### State Management

None. Config files and one test file. No runtime state, no migration, no backfill.

### Error Handling

- **YAML parse failure** on edited contract → caught at load time by existing test suite; dispatcher would also fail loudly at run time. Mitigation: each edit is a deletion of well-formed top-level keys; manually verify each edit parses with `yamllint`/`python -c "import yaml; yaml.safe_load(open(...))"` before committing.
- **End-to-end dispatcher KeyError** on missing fields → covered by UC-4. Verified by running this very feature (or a small dry-run workflow) through to `complete-workflow` after the strip.
- **Stripped key reintroduced in a future PR** → caught by the new regression test (UC-3, AC-3).

## Constraints

- Must not modify any agent-kind contract (explicit non-goal; risks regressing agent dispatch).
- Must not modify engine code (`dispatch.py`, `generate_plan.py`, `parser.py`, `record.py`); the refactor's safety claim is "the engine already ignores these keys."
- Existing tests (`test_all_contracts_have_agent_or_run.py`, `test_complete_workflow_contract.py`, broader `config/scripts/orchestrator_next/tests/`) must remain green.

## Trade-offs

- **Lose embedded rationale in `complete-workflow/contract.yaml`.** The 9-line `# No outputs:` comment block and the `rules:` / `instruction:` / `verify:` text get deleted from the contract file. Acceptable because: (a) the rationale survives in commit history; (b) `test_complete_workflow_contract.py`'s docstring already covers the ORC-66 pre-record contract; (c) leaving them in place is exactly the drift hazard the refactor exists to fix.
- **One test file now carries two distinct contract-shape assertions.** Acceptable because both belong to the same contract: "what every step contract / script contract must declare." Co-locating them gives future contract-shape rules a natural home.

## Acceptance Criteria

- AC-1: All 8 script-kind contracts under `config/steps/<step>/contract.yaml` contain only keys from `{id, kind, run, version, flags_read}` after the change — no `inputs`, `outputs`, `rules`, `instruction`, or `verify` keys present. Verified by `yaml.safe_load` + key-set assertion in the new test. [traces: UC-1, UC-2]
- AC-2: `complete-workflow/contract.yaml` contains no inline comment block that explains why `outputs:` is omitted (the `# No outputs:` block from current lines 39–47 is removed in full). Verified by `grep -c "# No \\`outputs:\\`" config/steps/complete-workflow/contract.yaml` returning 0. [traces: UC-2]
- AC-3: A new test function `test_script_contracts_have_no_agent_protocol_fields` lives in `config/steps/__tests__/test_all_contracts_have_agent_or_run.py`, walks every `config/steps/*/contract.yaml`, filters to `kind: script`, and fails if any contract contains any of `{inputs, outputs, rules, instruction, verify}`. Verified by running the test pre-strip (must fail listing the offending contracts) and post-strip (must pass). [traces: UC-3]
- AC-4: `pytest config/steps/__tests__/` and `pytest config/scripts/orchestrator_next/tests/test_complete_workflow_contract.py` both exit 0 after the strip. [traces: UC-E1, UC-E2]
- AC-5: A real workflow run (this feature, run through `/orchestrate` or `/autopilot`) reaches and completes `complete-workflow` without the dispatcher raising `KeyError` on a missing contract field. The change is archived to `spec/changes/archive/<date>-orc-86/`. [traces: UC-4, UC-E3]
- AC-6: No file under `config/steps/<step>/contract.yaml` with `kind: agent` is modified by this change. Verified by `git diff --name-only main...HEAD` containing no agent contract paths. [traces: out-of-scope guard]

## Decisions

- Place the new assertion inside the existing `test_all_contracts_have_agent_or_run.py` rather than a new file → keeps contract-shape rules co-located; one less artifact to maintain → future contract-shape rules have an obvious home.
- Delete the `# No outputs:` rationale comment from `complete-workflow/contract.yaml` rather than relocating it → the rationale survives in commit history and in `test_complete_workflow_contract.py`'s docstring; relocating it to a notes file would re-create the drift the refactor exists to eliminate → readers who need the rationale follow `git log -p` or the test docstring.
- Keep `version:` field on script contracts as-is → outside ORC-86's stated scope (discovery OQ-3); auditing it is a separate concern → minor inconsistency (some script contracts have `version`, some don't) deferred.
- New test does its own filesystem walk instead of extending the existing `_load_contract` helper → the existing helper uses a stale flat-form path (`steps/<id>.yaml`) and is a pre-existing concern; the new test reads `config/steps/*/contract.yaml` directly to avoid coupling to that helper's repair → no incidental refactor in this change.

## Open Questions

- None blocking. Discovery OQ-1 resolved (new test added to existing file, see Decisions). OQ-2 resolved (delete the comment, test docstring sufficient). OQ-3 resolved (retain `version:` as-is, out of scope).
