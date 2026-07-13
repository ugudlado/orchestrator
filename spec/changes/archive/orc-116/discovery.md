---
feature-id: orc-116
linear-ticket: ORC-116
---

# Discovery Brief: Persist and surface a step "briefing"

## Feature Summary

When an LLM agent step completes or fails, the engine records `status` and usage metrics into `step_history[]` but silently drops the agent's plain-English reasoning. The `COMPLETION:` block already emits an `outputs` dict that can carry a `reason` or `briefing` field, but `_build_history_entry` in `record.py` only copies a fixed whitelist of keys (`_OPTIONAL_STEP_HISTORY_KEYS`) — `reason` and any new `briefing` are not in that list. This feature adds a single `briefing` field to the shared `_COMPLETION_CONTRACT` prompt (so every agent step is instructed to emit it), persists it through `_OPTIONAL_STEP_HISTORY_KEYS`, and renders it as a truncated column in `workflow_report_step.py`.

## Personas & Actors

- **Operator / engineer running a workflow**: reads the workflow report after a run to understand why each step succeeded or blocked without digging through raw agent stdout.
- **Agent (LLM step)**: emits the `briefing` field in its `COMPLETION:` outputs block per the shared contract.
- **Engine (record.py + run_loop.py)**: passes the field through to `step_history[]` and renders it in the report.

## Use Cases

### Happy Path

UC-1: Briefing emitted on success — agent completes a step, emits `briefing: "Implemented X and verified via Y"` in COMPLETION outputs; the field persists in `step_history[]` and appears in the workflow report table.
UC-2: Briefing emitted on abandon — agent abandons a step, emits `briefing: "Could not locate config file; dependency missing"` in COMPLETION outputs; the field persists and renders alongside `status: abandoned` in the report.

### Error & Edge Cases

UC-E1: Briefing absent (script/shell step) — step has no agent reasoning (script.sh step); `briefing` is not in the payload; `record.py` tolerates absence (optional key), report renders `—` for that row.
UC-E2: Briefing absent (agent forgot) — agent omits `briefing` from outputs; same tolerance path as UC-E1; no routing change, no error.
UC-E3: Briefing too long — agent emits a multi-sentence paragraph; report renderer truncates to ~120 chars with ellipsis, matching existing `detail` field behavior.

## Scope

### In Scope

- Add `briefing` to `_COMPLETION_CONTRACT` in `run_loop.py` as an always-emit instruction (applies to all agent steps via the shared prompt).
- Add `briefing` and `reason` to `_OPTIONAL_STEP_HISTORY_KEYS` in `record.py` so both persist into `step_history[]`.
- Render `briefing` (truncated to ~120 chars) in `workflow_report_step.py` table — tolerate absence with `—`.
- Expose `briefing` per step in the structured JSON output of `workflow_report_step.py`.

### Out of Scope

- Per-prompt edits to individual step `prompt.md` files — `_COMPLETION_CONTRACT` injection is global; no per-step work needed.
- New schema or contract machinery — this rides the existing outputs passthrough; no parallel reasoning channel.
- `reason` field semantics change — `reason` already emitted on abandon; adding it to `_OPTIONAL_STEP_HISTORY_KEYS` just persists what was already validated for routing and discarded.
- Backfilling historical `step_history[]` entries in existing state files.
- UI or dashboard rendering — report is stderr table + stdout JSON, unchanged output channels.

## UI Direction

N/A — no UI components. Output is a stderr table and stdout JSON in `workflow_report_step.py`.

## Key Decisions

- **Single `briefing` field, not reusing `reason`**: `reason` is only emitted on abandon; `briefing` is always-present for uniform post-run readability. Both are persisted, but `briefing` is the primary rendering field.
- **Shared `_COMPLETION_CONTRACT` injection**: editing `run_loop.py:56` once covers all agent steps without touching individual prompts.
- **~120-char truncation in the report**: matches existing `detail` field behavior in `workflow_report_step.py`; keeps the table readable.
- **Build vs. reuse**: Pure extension of three existing locations (`run_loop.py`, `record.py`, `workflow_report_step.py`). No new files, no new abstractions.
- **Design direction selected**: Approach 1 (single `briefing` via shared contract) — complexity S. Rationale: aligns with ticket's "no parallel reasoning channel" scope note; Approach 2 (overload `reason`) conflates routing signal with UX; Approach 3 (per-step contract machinery) violates "no new schema/contract machinery" and touches 21 contracts for one string field.
- **OQ-1 resolved**: Yes, `briefing` appears in the structured JSON output per-step. Encoded in AC-4.

## Open Questions

(none — see resolutions above)
