Task ORC-116 - Persist and surface a step "briefing" — why each LLM step completed or failed
==================================================
Status: Ready
Priority: medium
Labels: orchestrator, observability, metrics

Description:
--------------------------------------------------
## Problem

When an LLM (agent) step finishes, the engine records `status` + usage into `step_history[]`, but the agent's *reasoning* for why it completed or failed is not captured in a readable form. The `COMPLETION:` block already carries an `outputs` dict (with fields like `reason`, `known_concerns`, `implementation_result`), but:

- `record._build_history_entry` only copies a whitelist of keys (`_OPTIONAL_STEP_HISTORY_KEYS`) into `step_history[]`. `reason` / `known_concerns` are NOT in that list, so they're validated for routing then dropped. (orchestrator_next/record.py:49-60, 556-558)
- `workflow_report_step.py` renders only step_id/status/attempt/tokens/cost/duration — no reasoning column. (config/steps/workflow-report/workflow_report_step.py:69-78)

Net: after a run, you can see *that* a step completed or blocked, but not *why*, without digging through raw agent stdout.

## Proposed change (two small knobs)

1. **Prompt side** — instruct agents to always emit a one-line `briefing` field in `outputs`: a plain-English summary of why the step reached its terminal status (what got done / what blocked it). Add to the shared `_COMPLETION_CONTRACT` in run_loop.py:56 so it applies to every agent step, rather than editing each prompt.

2. **Engine side** — add `briefing` (and persist `reason`) to `_OPTIONAL_STEP_HISTORY_KEYS` (record.py:50) so it lands in `step_history[]`, then render it as a column/line in `workflow_report_step.py`.

## Scope notes / decisions to confirm during design
- One field `briefing` covering both success and failure, vs. reusing existing `reason` (only emitted on abandon). Recommend a single always-present `briefing` for uniformity.
- Keep it one line (truncate in report like existing detail fields at ~120 chars).
- Shell/script steps have no agent reasoning — briefing is agent-steps-only; report should tolerate its absence.
- No new schema/contract machinery — this rides the existing outputs passthrough. (ponytail: reuse the passthrough, don't build a parallel reasoning channel.)

Acceptance Criteria:
--------------------------------------------------
- [ ] #1 Agents emit a one-line `briefing` in COMPLETION outputs for every terminal status (added to the shared _COMPLETION_CONTRACT, not per-prompt)
- [ ] #2 `briefing` (and `reason`) persist into step_history[] entries via _OPTIONAL_STEP_HISTORY_KEYS
- [ ] #3 workflow_report_step.py renders the briefing per step (truncated), tolerating absence for shell/script steps
- [ ] #4 Existing routing/validation behavior unchanged; a step with no briefing still records and routes normally
