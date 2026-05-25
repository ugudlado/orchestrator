---
feature-id: orc-7
linear-ticket: ORC-7
---

# Discovery Brief: Explicit Error Recovery Step Contract

## Feature Summary

ORC-7 was filed against a state where the orchestrate SKILL.md referenced an
"Error Recovery Contract" that did not exist, and three step contracts
(`execute-next-task.yaml`, `run-phase-review.yaml`, `phase-signoff.yaml`) each
defined their own inline retry/escalation semantics. Survey of the current
codebase shows the premise has been largely overtaken by subsequent work:
`config/steps/contracts/error-recovery.md` now exists as a comprehensive 170+
line contract (state-transition table, Fix Task Protocol, Agent Blocked
Protocol, Escalation Protocol, Quarantine Protocol, Missing STATUS Rule,
error_events schema). The three offending steps have been restructured —
`phase-signoff` was deleted, `execute-next-task` was replaced by
`execute-one-task` (single-task, no retry logic), and `run-phase-review` now
explicitly defers to `contracts/error-recovery.md` for both Fix Task Protocol
and Escalation Protocol. The remaining work, if any, is a verification sweep
to confirm no stale duplications or broken references survived — likely a
chore-class change rather than a feature.

## Personas & Actors

- **Orchestrator dispatcher** (`scripts/orchestrator-cli` / `dispatch.py` /
  `reconcile.py`) — reads step_history, applies state transitions.
- **Step agents** (developer, reviewer, architect) — return STATUS:
  completed/blocked/escalate_to_architect per contract.
- **Workflow author** (human or `/learn`) — modifies step contracts and must
  reference the canonical recovery semantics rather than duplicating them.
- **`/learn` cross-feature analyzer** — aggregates retry data from
  state.yaml; needs uniform retry semantics to compare across step types.

## Use Cases

### Happy Path

UC-1: Verify references — A workflow author opens `orchestrate/SKILL.md` and
sees "Follow Error Recovery Contract (contracts/error-recovery.md)" so that
they can click through to the canonical document without dead links.

UC-2: Single source of truth — When `run-phase-review` fails verification, the
prompt directs the agent to Fix Task Protocol via
`contracts/error-recovery.md` so that fix-task generation, retry counting, and
escalation behavior match what the dispatcher does for other steps.

### Error & Edge Cases

UC-E1: Stale inline retry logic — What happens when a step contract still
embeds its own retry/rejection loop instead of deferring to the canonical
contract: `/learn` cross-step retry analysis miscompares step semantics, and
divergent escalation behavior surfaces only in production.

UC-E2: Broken reference — What happens when a contract references a section
heading (e.g., `§ Fix Task Protocol`) that no longer exists in
`error-recovery.md` after a rename: agents see a dangling anchor and may
improvise their own recovery, defeating determinism.

## Scope

### In Scope

- Audit every reference to "Error Recovery", "retry", "escalation", "rejection
  loop", and "fix task" across `config/steps/**`, `skills/**`, and
  `agents/**` to confirm each one points to `contracts/error-recovery.md` (or
  a named § section within it) rather than redefining semantics inline.
- Confirm that the three originally-cited steps (`execute-next-task`,
  `run-phase-review`, `phase-signoff`) and their current replacements
  (`execute-one-task`, `run-phase-review`) contain no orphaned retry logic.
- Verify `CONVENTIONS.md` § anchor table (lines 86–89) still matches actual
  section headings inside `contracts/error-recovery.md`.
- If any drift is found, replace the inline logic with a one-line reference
  to the contract.

### Out of Scope

- Rewriting `contracts/error-recovery.md` itself — it is the canonical doc
  and already covers state transitions, Fix Task Protocol, Agent Blocked
  Protocol, Escalation Protocol, Quarantine Protocol, Missing STATUS Rule,
  and `error_events` schema. Rationale: the original ticket asked for the
  contract to *exist*; expanding it is a separate concern.
- Changing dispatcher behavior (`dispatch.py` / `reconcile.py`). Rationale:
  the contract documents observed behavior; the code is the implementation
  and is already aligned (see archived feature
  `subprocess-per-step-observability` and ORC-81).
- Per-tool wiring or LLM-specific recovery instructions. Rationale: schemas
  remain tool-agnostic per the project rule.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Re-scope the ticket as a chore-class verification sweep rather than a
  feature.** Rationale: the substantive work (creating
  `contracts/error-recovery.md` and unifying the three steps' retry semantics)
  already landed in prior features
  (`subprocess-per-step-observability`, the `execute-one-task` refactor,
  the deletion of `phase-signoff`). Only an audit + targeted cleanup remains.
  The architect should confirm before design-and-draft-artifacts proceeds.
- **Selected design direction: Approach B — Targeted reference sweep.**
  Rationale: Approach A (close-as-done) was lower complexity but left three
  concrete drift points standing: (1) `config/steps/CONVENTIONS.md:67` lists
  deleted `phase-signoff` as a consumer of `contracts/error-recovery.md`;
  (2) `agents/developer.md:118` encodes inline "After max_retry_rounds
  attempts: Escalate to orchestrator" language that duplicates the
  Escalation Protocol; (3) `agents/reviewer.md:200` says "fix tasks in
  tasks.md format" while the canonical Fix Task Protocol writes tasks.yaml.
  Approach C (add a consumer-reference validator) was rejected as
  speculative tooling — a one-off audit covers the gap today. Complexity: S.
- **OQ-1 resolved**: Keep ORC-7 open and re-scope in design as the targeted
  sweep above; do not open a smaller chore ticket. The fix is contained
  enough to land under this ID.
- **OQ-2 resolved**: The grep above (extended to `agents/` and `scripts/`)
  surfaced two non-step-contract callers (developer.md, reviewer.md) with
  inline retry/escalation prose. Both are addressed in Approach B.
- **OQ-3 deferred**: Whether `/learn` special-cases legacy step IDs is out
  of scope; this sweep does not touch `agents/workflow-learner.md` retry
  aggregation logic. UC-E1 stands as a hypothetical hazard, not a live bug.

## Open Questions

- OQ-1: Should this feature be closed as already-done and a smaller chore
  ticket opened for the audit sweep, or kept open and re-scoped in design?
  Architect input needed at design-and-draft-artifacts.
- OQ-2: Are there any non-step-contract callers (e.g., scripts in `scripts/`,
  tests, agent prompts under `agents/`) that still encode retry semantics
  inline? The grep above covered `config/`, `skills/`, and root-level
  references but did not exhaustively walk `agents/` or `scripts/`.
- OQ-3: Does `/learn`'s cross-feature retry aggregation (CONVENTIONS.md line
  563 mentions retry analysis) actually consume `retries.<step_id>` uniformly
  today, or does it still special-case the three legacy step IDs? Verifying
  this confirms whether UC-E1 is hypothetical or live.
