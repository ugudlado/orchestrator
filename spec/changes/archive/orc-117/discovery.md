---
feature-id: orc-117
linear-ticket: ORC-117
---

# Discovery Brief: Engine Workflow-Agnosticism

## Feature Summary

The orchestrator engine currently hardcodes workflow-specific knowledge in three locations in `orchestrator_next/record.py` and one location in `run_loop.py`: a fixed key whitelist controls what step outputs survive into `step_history[]`, a hardcoded key-hoist list promotes select top-level payload keys into `outputs`, and three validator functions check step-specific output shapes by matching literal `step_id` strings (`"design-review"`, `"review"`). This feature removes all four coupling points so the engine stores exactly what steps emit and routes based on generic contract-driven declarations rather than inline knowledge of specific steps.

## Personas & Actors

- **Engine maintainer** — adds or renames workflow steps without touching `record.py`.
- **Workflow designer** — declares review-gate semantics in `contract.yaml`, not engine source.
- **Operator/CI** — relies on `step_history[]` containing the full structured outputs a step emitted for debugging and retro.

## Use Cases

### Happy Path

UC-1: Full outputs persisted — a step emits `outputs: {briefing: "...", implementation_result: {...}}` and both keys appear verbatim in `step_history[-1]` (under `evidence.outputs` or a top-level `outputs` key), not silently dropped because they weren't in `_OPTIONAL_STEP_HISTORY_KEYS`.

UC-2: Generic review guard — `design-review` emits `status: completed` with `design_review_result: needs_work`; the engine reads a `required_output_for_completed` declaration from that step's `contract.yaml` and coerces status to `failed`, without any `if step_id == "design-review"` branch in engine code.

UC-3: No hoist whitelist — a step emits `outputs: {discovery_result: {...}}` as a nested key (normal path) or at the top level (legacy); run_loop normalises it generically without enumerating `("learn_result", "phase_review_report", "discovery_result")`.

UC-4: Report shows actual outputs — `workflow_report` prints whatever keys each step produced (truncated), not a fixed column set. A new step with a novel output key appears in the report automatically.

### Error & Edge Cases

UC-E1: Contract missing — a step has no `contract.yaml`; engine degrades gracefully (no crash), skipping the generic contract-driven check and logging a warning, same as today for `_load_contract`.

UC-E2: Agent rubber-stamps review — `review` step emits `status: completed` with `phase_review_report.verdict: needs_work`; generic check rejects it identically to today's hardcoded validator; DAG routes to `on_failure`.

UC-E3: Unknown output key — step emits an output key not previously seen by the engine; it is stored in full, not dropped; no warning is emitted.

## Scope

### In Scope

- Replace `_OPTIONAL_STEP_HISTORY_KEYS` whitelist in `record._build_history_entry` with full `outputs` dict persistence.
- Remove the hardcoded key-hoist list in `run_loop` (lines 227–229); hoist generically or require steps to emit under `outputs:`.
- Delete `_validate_phase_review_output`, `_validate_design_review_output`, `_normalize_review_payload_status` from `record.py`.
- Add one generic contract-driven check: a new field in `contract.yaml` (e.g. `required_output_for_completed`) that the engine reads and applies for any step, replacing the three deleted functions.
- Add `required_output_for_completed` declarations to `config/steps/design-review/contract.yaml` and `config/steps/review/contract.yaml` to preserve existing guardrail behaviour.
- `AgentStepContract` dataclass gains the new field; parser reads it from `contract.yaml`.
- `report.py` / `render_report` may already be adequate (it reads `briefing` from `step_history`); if it reads only `briefing`, extend to render all keys present in `evidence.outputs` (truncated).

### Out of Scope

- Changing the COMPLETION protocol or the step payload schema — steps already emit `outputs:` today.
- ORC-116 (surface briefing in report) — this ticket makes briefing just another persisted key; ORC-116 collapses to a prompt tweak after this lands.
- Changing any step's skill/prompt files — only `contract.yaml` additions for the two review steps.
- Adding new test framework or fixtures — extend existing `tests/test_record_validation.py` patterns.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Option A locked (contract-driven validation):** The ticket explicitly locks Option A — move the anti-rubber-stamp rule into `contract.yaml` as a declarative field; the engine applies it generically. Option B (trust agent blindly) is off the table per AC-4.
- **Full outputs under `evidence.outputs`:** `_build_history_entry` already populates `entry["evidence"]` via `_merge_evidence_block(outputs, ...)`. The cleanest path is to ensure the full `outputs` dict lands in `evidence["outputs"]` (already happens for the evidence block) and drop the `_OPTIONAL_STEP_HISTORY_KEYS` loop that copies individual keys to the top-level entry. This keeps backward-compatible reading of `entry["briefing"]` by promoting `briefing` via the existing `default_outputs` mechanism or by having `render_report` read from `evidence.outputs`.
- **Build vs reuse:** This is pure deletion + a small extension to an existing contract dataclass. No new abstraction, no new module. Shortest diff wins (ponytail mode).
- **`required_output_for_completed` shape:** Likely `{key: "design_review_result", value: "pass"}` — one required key/value pair per contract. The engine checks `outputs[key] == value` when `status == "completed"`; mismatch coerces to `failed`. Exact field name TBD in design.

## Selected Direction (design step 2026-07-30)

**Selected: Flat entry["outputs"] + contract-driven check (complexity XS).** Store the full `outputs` dict at `entry["outputs"]` on each step_history entry (matches AC-1 wording exactly). Add `required_outputs_for_completed: [{key, value}, ...]` to `AgentStepContract`; engine reads it via a single `_enforce_required_outputs` function that replaces all three step-specific validators. Generic non-reserved key hoist in `run_loop._agent_payload`. `report.py` reads outputs from the new location, falls back to legacy top-level `briefing`. Rationale: XS beats the nested-only alternative (S) — matches ticket's AC-1 wording, dominates via deletion, no consumer migration puzzle. Ponytail-mode selection: shortest diff wins.

## Open Questions (resolved)

- OQ-1 (flat vs nested outputs storage): **flat `entry["outputs"]`** — AC-1 names it explicitly, and consumer-side access is direct rather than through `evidence.outputs`.
- OQ-2 (top-level briefing consumers after whitelist deletion): only `report.py:114` reads `entry.get("briefing")` outside record.py itself (verified by `grep -rn 'entry\["briefing"\]\|\.get("briefing"' orchestrator_next/`). Fallback path in report.py preserves the read.
- OQ-3 (root-level emissions of the three legacy hoisted keys): resolved by using a generic non-reserved-key hoist rather than deleting the whitelist outright — any step still emitting `learn_result`, `phase_review_report`, or `discovery_result` at payload root continues to work, and future novel root-level keys migrate into `outputs` automatically. See T-3 in tasks.yaml.
