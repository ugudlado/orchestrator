---
feature-id: orc-117
linear-ticket: ORC-117
---

# Discovery Brief: Remove flags system from codebase

## Feature Summary

The `flags` field in `state.yaml` and the associated `rules_when`/`when:` conditional machinery are dead weight. The `worktree` flag was removed when `create-worktree` was made unconditional; the `linear` flag was never read by any step or rule. The `rules_when` and `when:` conditional mechanisms in `generate_plan.py` remain coded but are inert: no workflow YAML file or step contract has ever populated these fields. This change removes all flag-related code and state from three files (`seed_write_state.py`, `seed_parse_overrides.py`, `generate_plan.py`) and updates tests accordingly.

## Personas & Actors

- **Orchestrator maintainer** — reads and modifies the orchestrator engine code; benefits from reduced surface area.
- **CI / test runner** — must continue to pass after the deletion.

## Use Cases

### Happy Path

UC-1: Seed new state — `seed_parse_overrides.py` produces JSON without a `flags` key; `seed_write_state.py` writes state.yaml without a `flags` field.
UC-2: Generate plan — `generate_plan.py` reads state.yaml that has no `flags` field and promotes the workflow_plan to nodes shape without attempting flag evaluation.

### Error & Edge Cases

UC-E1: Legacy state.yaml with `flags` field — `generate_plan.py` encounters a state file that still has `flags: {worktree: false}` (from an old run); the code must not crash on unexpected keys (no code reads it after deletion, so it is silently ignored in the YAML dict).
UC-E2: Test helper still passes `flags` param — `_make_state_yaml` helper in `test_generate_plan.py` currently accepts a `flags` dict and writes it to state; after removal the helper must still produce valid state without emitting a `flags` key.

## Scope

### In Scope

- Remove `"flags": d["flags"]` write in `seed_write_state.py` (line 49)
- Remove `flags` dict construction and JSON output key in `seed_parse_overrides.py` (lines 30–36, 53)
- Remove `_evaluate_rules_when` function and all call sites from `generate_plan.py`
- Remove `flags` param from `_merge_rules`, `_build_step_block`, and `generate_plan` entrypoints in `generate_plan.py`
- Remove `flags.get(when_flag, ...)` filter for named rules in `_merge_rules` (lines 227–233)
- Remove `verify_when` evaluation block in `generate_plan` (lines 477–482)
- Remove `flags: dict[str, Any] = state.raw.get("flags") or {}` from `generate_plan` (line 414)
- Update `test_generate_plan.py` to remove `flags` from `_make_state_yaml` helper and all callers
- Update `test_dispatch_missing_contract.py`, `test_dispatch_no_path3.py`, `test_dispatch_step_context.py` fixtures that include `"flags": {}`
- Confirm all pytest tests pass after deletion

### Out of Scope

- `resolved_flags` in `test_dispatch.py` / `test_dispatch_allowed_tools.py` / `test_dispatch_resume.py` — these appear in a stale `plan.yaml` shape fixture, not in state.yaml; they are a separate cleanup concern (plan.yaml is a legacy format already replaced by state.yaml nodes)
- `doctor.py` line 418 — the word "flags" there refers to CLI argument flags, not the state.yaml `flags` field; no change needed
- Documenting `rules_when` design before deletion (per implementation note): the design is already documented inline in `generate_plan.py` docstrings and in `rule-merge.md` referenced in those docstrings; no separate design extraction is warranted since the feature was never activated and the docstrings will be removed along with the code

## UI Direction

N/A — no UI components.

## Key Decisions

- **Selected design direction: Atomic delete-and-fix per commit** (complexity S). Each flag-bearing region is removed together with any coupled test assertion in the same task, so `pytest` stays green at every commit. Chosen over RED-then-GREEN TDD pairs because this is deletion, not construction — there is no new behavior to test-drive, and `tdd_required` is unset on this run. See design.md "Selected Approach".
- **Delete without extracting a standalone design doc**: the ticket note says "if worth keeping, extract first." The discovery brief originally assumed the design lived in `rule-merge.md` — but that file **has never existed** (ORC-77's archived discovery confirms it). The rules_when design is already preserved in git history and in the archived `2026-04-20-generate-plan-yaml-at-init/` and `2026-05-22-orc-66/` artifacts. design.md records where the intent survives; no new doc is written. Survey confirms zero active consumers across all workflow YAMLs, schema `defaults`, and step contracts.
- **`verify_when` removal is forced (resolves OQ-1)**: the `verify_when` override loop is `flags.get(...)`-dependent; once `flags` is gone it can never fire. It is removed under the ticket's "when: conditional" scope, not left as dead YAML. The verify resolution collapses to `verify_block = base_verify`.
- **`extra_rules` is retained**: it is injected unconditionally and is NOT part of the flags system. Only the `rules_when` half of merge tier 1 is removed. The `extra_rules`-before-contract ordering assertion in `test_generate_plan.py` is preserved.
- **Test sweep is broader than the original In-Scope list**: the brief named only three dispatch fixtures but missed `test_generate_plan_directory_layout.py` (passes `flags` **positionally** into the changed signature → genuine TypeError, must-fix) and several inert-fixture files (`test_record_agent_field`, `test_complete_workflow`, `test_endtoend_migrated_workflow`, `test_pre_stamp_idempotency`, `test_workflow_schemas_load`, `test_prose_contracts`). The two genuinely-breaking files are AC-bound; the inert `flags: {}` fixtures are swept as honesty cleanup (T-4).
- **`resolved_flags` confirmed out of scope**: grep confirms it appears only in `plan.yaml`-shape test fixtures, read by no source code. Separate legacy concern.
- **Override-parse loop is deleted, not repurposed**: an earlier draft proposed making `seed_parse_overrides.py` *reject* `key=value` overrides with a non-zero exit. That contradicts AC-2 ("flags **parsing removed**") and adds a new behavior to a deletion ticket. HEAD grep confirms no caller passes overrides, so the loop (and `raw_overrides = args[4:]`) is removed outright. See design.md "Selected Approach" / Decisions.
- **Baseline is dirty (8 pre-existing failures)**: `pytest orchestrator_next/tests/` on HEAD already fails 8 cases unrelated to flags (5× `test_agent_runner` from an untracked ORC-118 file, 1× `test_step_runner`, 2× `test_workflow_schemas_load` terminal-tail). AC-4 is therefore scoped to "no failure **outside** that named set", not literal zero — a clean worktree may not even contain the untracked file. See design.md "Constraints".
- **Seed pipeline fused into one task (T-1)**: `seed_write_state.py` hard-reads `d["flags"]` from `seed_parse_overrides.py`'s JSON. Split-order removal raises a `KeyError` that no unit test catches (no seed-pipeline integration test exists), so T-1 removes both edits together and adds an explicit parse→write pipe-through verify.

## Open Questions

- None. OQ-1 (verify_when) is resolved above — it must be removed, not left as dead YAML.
