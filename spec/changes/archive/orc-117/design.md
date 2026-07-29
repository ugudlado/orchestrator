---
feature-id: orc-117
linear-ticket: ORC-117
---

# Design: Engine Workflow-Agnosticism

## Context

The orchestrator engine (`orchestrator_next/`) currently hardcodes workflow-specific knowledge in four spots:

1. `record._OPTIONAL_STEP_HISTORY_KEYS` (record.py:76-88) — a fixed 11-entry whitelist that copies select payload keys onto the `step_history[]` entry; anything else a step emits is dropped.
2. `run_loop._agent_payload` (run_loop.py:227-229) — a 3-entry key-hoist list (`learn_result`, `phase_review_report`, `discovery_result`) that lifts payload-root keys into `payload["outputs"]`.
3. `record._validate_phase_review_output` + `record._validate_design_review_output` + `record._normalize_review_payload_status` (record.py:111-209) — three functions gated by literal step_ids (`"review"`, `"design-review"`) that reach into named output keys (`phase_review_report.verdict`, `design_review_result`) to enforce the anti-rubber-stamp guardrail.
4. `report.render_report` (report.py:114) — reads only `entry["briefing"]` from history, treating it as a fixed column rather than one output key among many.

The result is that the engine — which is meant to be a generic dispatcher — bakes in knowledge of specific workflow steps and specific output shapes. Adding a new review-style step or renaming an existing one requires editing engine source.

## Goals / Non-Goals

### Goals

- Persist the full `outputs` dict emitted by every step; drop the whitelist.
- Remove the run_loop hoist whitelist; hoist generically.
- Delete the three step-specific validator/normaliser functions from `record.py`.
- Introduce ONE generic contract-driven check: `contract.yaml` declares required output values for `completed` status; engine enforces uniformly.
- Extend `report.render_report` to iterate all persisted output keys per step (truncated), not just `briefing`.
- Preserve today's anti-rubber-stamp behaviour for `design-review` and `review` via their contract declarations.

### Non-Goals

- Changing the COMPLETION protocol or step payload schema.
- Modifying any step's SKILL.md / prompt.md (only `contract.yaml` edits for the two review steps).
- ORC-116 (surface briefing in report) — becomes a no-op prompt tweak after this ticket.
- Fixing the 10 pre-existing pytest failures (unrelated modules).

## Approaches Considered

### Approach 1: Flat `entry["outputs"]` + contract-driven check (Selected)

Store the full outputs dict as `entry["outputs"]` at the top level of each step_history entry (in addition to what `_merge_evidence_block` already puts under `evidence.outputs`). Delete `_OPTIONAL_STEP_HISTORY_KEYS`. Add a new optional field `required_outputs_for_completed: [{key, value}, ...]` on `AgentStepContract`; engine reads it and rejects `completed` payloads where any required key mismatches. Delete the three specific validators. Update `report.py` to read from `entry["outputs"]` first (fallback to `entry.get("briefing")` for legacy state files).

- Pros: shortest diff (deletions dominate), consumers get direct access, symmetric with ticket AC-1 wording, no schema-migration puzzle for downstream `report.py` / `retro`.
- Cons: introduces one new top-level field (`outputs`) on history entries, adjacent to the existing `evidence.outputs`. Slight redundancy for one release cycle until callers migrate.
- Complexity: **XS**

### Approach 2: Nested-only via `entry["evidence"]["outputs"]`

Rely on the existing `_merge_evidence_block` which already puts full outputs under `evidence.outputs`. Delete `_OPTIONAL_STEP_HISTORY_KEYS`. Update every consumer (report.py, learn/retro steps, tests) to read `entry["evidence"]["outputs"][key]` instead of `entry[key]`.

- Pros: no new top-level key on the schema.
- Cons: verbose access path for every consumer; higher-churn diff across `report.py`, learn scenarios, workflow report step, and tests; the ticket's AC-1 explicitly says "`entry['outputs']`", so this reads against the locked scope.
- Complexity: **S**

### Selected Approach

**Approach 1.** XS beats S; ticket AC-1 names `entry["outputs"]` explicitly; the small transient redundancy with `evidence.outputs` is tolerable and can be revisited once every consumer reads from the new location. Ponytail-mode: deletion-dominant diff wins.

## High-Level Design

### Architecture Overview

The change is confined to three engine files and two step contracts. No new modules, no new packages, no new abstractions:

```
run_loop._agent_payload            [DELETE hoist whitelist; generic hoist]
       ↓ payload
record._validate_payload           [DELETE _normalize_review_payload_status call]
       ↓ (step_id, status, outputs, contract)
record._validate_outputs           [DELETE _validate_phase_review_output, _validate_design_review_output]
       ↓                            [ADD _enforce_required_outputs(contract, status, outputs)]
record._build_history_entry        [DELETE _OPTIONAL_STEP_HISTORY_KEYS loop; ADD entry["outputs"] = outputs]
       ↓ state.yaml step_history[]
report.render_report               [READ entry.get("outputs") first, fallback to entry.get("briefing")]
```

### Key Abstractions

- **`required_outputs_for_completed`** (new field on `AgentStepContract`): optional list of `{key: str, value: str}` pairs. When present and `status == "completed"`, the engine asserts `outputs[key] == value` for every pair; a mismatch either coerces status to `failed` (parity with today's `_normalize_review_payload_status`) or raises `_RecordError` (parity with today's `_validate_phase_review_output`). See the Decisions section for which behaviour applies.

## Low-Level Design

### Components

**`orchestrator_next/parser.py`**

- `AgentStepContract` gains one field: `required_outputs_for_completed: list[dict] = field(default_factory=list)`.
- `_make_contract` reads `data.get("required_outputs_for_completed")` and normalises to a list of `{"key": str, "value": Any}` dicts; malformed entries logged and skipped.

**`orchestrator_next/record.py`**

- DELETE `_OPTIONAL_STEP_HISTORY_KEYS` constant.
- DELETE `_validate_phase_review_output`, `_validate_design_review_output`, `_normalize_review_payload_status` functions.
- DELETE `_PHASE_REVIEW_VERDICTS` (only referenced by the deleted validator; keep only if consumed elsewhere — grep confirms not).
- ADD `_enforce_required_outputs(contract, status, outputs) -> str`: if `status not in _SUCCESS_STATUSES` or contract has no declaration → return status unchanged. Otherwise, walk the list; on any mismatch, write a stderr line matching today's `[record] <step_id>: coercing status ...` format and return `"failed"`. This coerces (like today's normaliser), rather than raising, so `on_failure` edges still fire.
- REPLACE the call chain in `_validate_payload`: drop `_normalize_review_payload_status`, drop `_validate_outputs` (the wrapper) — call `_enforce_required_outputs` in its place after `_apply_default_outputs`.
- MODIFY `_build_history_entry`: replace the `for key in _OPTIONAL_STEP_HISTORY_KEYS` loop with a single line `entry["outputs"] = dict(outputs)`. The existing `evidence` block is retained unchanged (backward-compat during one release cycle).

**`orchestrator_next/run_loop.py`**

- MODIFY `_agent_payload`: replace the 3-key literal loop (lines 227-229) with a generic hoist that moves every non-reserved payload-root key into `payload["outputs"]`. Reserved keys are the completion-protocol fields already handled by the payload wrapper: `step_id`, `phase`, `status`, `agent`, `agent_id`, `attempt`, `started_at`, `usage`, `outputs`, `evidence`, `state_patch`. Any other top-level key is moved into `outputs` (without overwriting an existing key of the same name).

**`orchestrator_next/report.py`**

- MODIFY `render_report` step-row loop: derive `briefing` from `entry.get("outputs", {}).get("briefing")` first, fall back to `entry.get("briefing")` for legacy state files. Then, per step, print a compact one-line summary of any other keys present in `outputs` (excluding `briefing` since it's already rendered): `[outputs] key1=<truncated 80 chars>, key2=<truncated>`. Truncation via `str(value)[:80]` + `"…"` sentinel.

**`config/steps/design-review/contract.yaml`**

- ADD:
  ```yaml
  required_outputs_for_completed:
    - key: design_review_result
      value: pass
  ```

**`config/steps/review/contract.yaml`**

- ADD:
  ```yaml
  required_outputs_for_completed:
    - key: phase_review_report.verdict
      value: pass
  ```
  The `.` separator is honoured as a dotted path by `_enforce_required_outputs` (one level of nesting, no arbitrary depth — YAGNI). This preserves today's behaviour where the engine reaches into `phase_review_report.verdict`.

### Data Flow

1. Agent step emits COMPLETION with arbitrary `outputs:` map.
2. `run_loop._agent_payload` wraps: reserved keys stay at root, all other top-level keys hoist into `outputs` (generic — no whitelist).
3. Engine calls `orchestrator done`, which invokes `record._validate_payload`.
4. `_enforce_required_outputs` reads the step's `contract.yaml`; if any `required_outputs_for_completed` entry mismatches the emitted outputs, status coerces to `failed` with a stderr note (identical format to today).
5. `_build_history_entry` writes `entry["outputs"] = outputs` — the FULL dict, no whitelist.
6. `report.render_report` reads `entry["outputs"]` and prints all present keys (truncated).

### State Management

`state.yaml`'s `step_history[].outputs` is now the canonical persistence location for step outputs. `step_history[].evidence.outputs` continues to be populated by `_merge_evidence_block` unchanged, for one release cycle of backward compatibility. No migration script — old state files simply won't have `entry["outputs"]`, and `report.py`'s fallback to `entry.get("briefing")` handles them.

### Error Handling

- **Contract missing** (`_load_contract` returns `None`): `_enforce_required_outputs` returns status unchanged — degrades gracefully, same as today for `_apply_default_outputs`.
- **Malformed declaration** (`required_outputs_for_completed` not a list, entry not `{key, value}`): parser logs to stderr and drops the malformed entry; step behaves as though the declaration were absent.
- **Missing key in outputs** (declared key not present): counts as mismatch → coerces to `failed` (same as today when `outputs.get("design_review_result")` returns `None` and mismatches `"pass"`).

## Constraints

- Wire-compatible with existing `state.yaml` files: report.py falls back to `entry["briefing"]` when `entry["outputs"]` is absent.
- No new dependencies.
- No new modules.
- Engine test suite has 10 pre-existing failures (test_paths, test_parser_directory_layout, test_prompt_dir_colocation, test_models_verb, test_step_env) — unrelated; T-6 phase-gate MUST scope to `orchestrator_next/tests/test_record*.py orchestrator_next/tests/test_report.py orchestrator_next/tests/test_run_loop_git_agnostic.py orchestrator_next/tests/test_phase_review_success_routing.py orchestrator_next/tests/test_completion_contract_briefing.py orchestrator_next/tests/test_endtoend_migrated_workflow.py orchestrator_next/tests/test_parser*.py` rather than the full suite.

## Verified System Boundaries

- **Only in-engine consumer of `entry["briefing"]`**: `report.py:114`. Verified via `grep -rn 'entry\["briefing"\]\|\.get("briefing"' orchestrator_next/ --include="*.py"` — returns one hit outside record.py. Safe to switch to outputs-first read with briefing fallback.
- **All references to specific review step_ids/output keys in engine**: only `record.py:112-209` and `run_loop.py:227`. Verified via `grep -n "phase_review_report\|design_review_result" orchestrator_next/*.py`. AC-6 grep target confirmed clean after the deletion.
- **`AgentStepContract` shape and parser**: `parser.py:26-36` (dataclass) and `parser.py:272-285` (`_make_contract`) confirmed as the only construction sites. Existing `default_outputs` field pattern is the template for `required_outputs_for_completed`.
- **`_PHASE_REVIEW_VERDICTS` uses**: `grep -n "_PHASE_REVIEW_VERDICTS" orchestrator_next/` — only used inside the deleted `_validate_phase_review_output`. Safe to remove.
- **Test file inventory**: `test_record_validation.py`, `test_record_briefing_persistence.py`, `test_phase_review_success_routing.py`, `test_completion_contract_briefing.py`, `test_endtoend_migrated_workflow.py`, `test_report.py` all touch the affected surface. RED/GREEN pairs target these files.

## Trade-offs

- One-release redundancy: `entry["outputs"]` and `entry["evidence"]["outputs"]` both populated. Accepted because the migration cost of removing `evidence.outputs` is a separate concern (learn/retro depend on it) and this ticket's scope is agnosticism, not schema cleanup.
- Dotted-path support in `required_outputs_for_completed` is one level only. Accepted — the only real caller (`review` step) uses one level. YAGNI on arbitrary depth.

## Acceptance Criteria

- AC-1: `step_history[]` entries carry the full outputs dict a step emitted (`entry["outputs"]` contains every key from the payload's `outputs:` map, verbatim). [traces: UC-1, UC-E3]
- AC-2: `workflow_report` renders whatever output keys each step produced, truncated; a step emitting a novel key surfaces it in the report without engine changes. [traces: UC-4]
- AC-3: `run_loop._agent_payload` no longer contains the literal tuple `("learn_result", "phase_review_report", "discovery_result")`; hoist is generic (all non-reserved payload-root keys move into `outputs`). [traces: UC-3]
- AC-4: `_validate_phase_review_output`, `_validate_design_review_output`, and `_normalize_review_payload_status` are deleted from `record.py`; a single generic `_enforce_required_outputs(contract, status, outputs)` replaces them. [traces: UC-2]
- AC-5: `design-review` and `review` contract.yaml files declare `required_outputs_for_completed`; a `status: completed` payload with `design_review_result: needs_work` or `phase_review_report.verdict: needs_work` still coerces to `failed` and routes via `on_failure`. [traces: UC-2, UC-E2]
- AC-6: `grep -E 'design-review|run-phase-review|design_review_result|phase_review_report' orchestrator_next/*.py` returns no matches outside tests. [traces: UC-1, UC-2]
- AC-E1: A step with no `contract.yaml` still records without crash; `_enforce_required_outputs` no-ops when the contract is `None`. [traces: UC-E1]

## Decisions

- Coerce rather than raise on required-output mismatch → matches today's `_normalize_review_payload_status` behaviour (design-review) → `on_failure` edges continue to fire, no dispatch abort. The stricter raise-mode (today's `_validate_phase_review_output` for review) is intentionally consolidated to coerce; the workflow route via `on_failure` is equivalent and simpler.
- New field `entry["outputs"]` sits alongside `entry["evidence"]["outputs"]` for one cycle → avoids touching every learn/retro consumer in this ticket → schema cleanup is deferred, tracked as a follow-up (not filed — trivial).
- Dotted-path support (`phase_review_report.verdict`) one level deep only → the only real caller needs one level → deeper nesting is speculative complexity.
- `report.py` extension prints all outputs keys, truncated, on one line per step → the ticket's AC-2 requires this; ORC-116 (a separate ticket) is about presentation quality, not agnosticism.

## Open Questions

- None. The three original OQs from discovery are resolved: OQ-1 → flat `entry["outputs"]` (per AC-1 wording); OQ-2 → confirmed via grep that `report.py:114` is the only consumer of top-level `entry["briefing"]`, fallback path preserves it; OQ-3 → generic hoist replaces the whitelist, ensuring root-level keys emitted by any step migrate into `outputs` automatically.
