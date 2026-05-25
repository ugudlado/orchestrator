---
feature-id: orc-7
linear-ticket: ORC-7
---

# Design: Explicit Error Recovery Step Contract — Reference Sweep

## Context

ORC-7 was filed when the orchestrate dispatch loop referenced an "Error
Recovery Contract" that did not exist, and three step contracts each defined
their own inline retry/escalation semantics. The substantive work has since
landed: `config/steps/contracts/error-recovery.md` is a 228-line canonical
contract covering state transitions, Fix Task Protocol, Agent Blocked
Protocol, Escalation Protocol, Retry Context Contract, Quarantine Protocol,
Missing STATUS Rule, State Recording, and Structured Error Events. The three
originally-cited step contracts have either been deleted (`phase-signoff`,
`execute-next-task`) or restructured to defer to the contract
(`run-phase-review`, replacement `execute-one-task`).

A grep sweep of `config/steps/`, `skills/`, `agents/`, and `scripts/` against
the canonical contract surfaced three concrete drift points that survive:

- `config/steps/CONVENTIONS.md:67` lists deleted `phase-signoff` in the
  consumers column for the Error Recovery row.
- `agents/developer.md:118` reads "After max_retry_rounds attempts: Escalate
  to orchestrator with what you tried and why it didn't work" — inline
  retry/escalation prose that duplicates the Escalation Protocol.
- `agents/reviewer.md:200` reads "If NEEDS WORK: generate fix tasks in
  tasks.md format (T-N+1, etc.) with code references" — the canonical Fix
  Task Protocol writes tasks.yaml, not tasks.md.

## Goals / Non-Goals

### Goals

- Every reference in `config/steps/**`, `skills/**`, and `agents/**` to
  "Error Recovery", "retry", "escalation", or "fix task" either points to
  `contracts/error-recovery.md` (or a named § section inside it) or leaves
  no inline duplication of the contract's semantics.
- The CONVENTIONS.md consumer table reflects the current set of step
  contracts (no references to deleted steps).
- Reviewer and developer agent prompts defer to the canonical contract for
  retry/escalation/fix-task behavior rather than carrying their own prose.

### Non-Goals

- Rewriting `contracts/error-recovery.md` itself — it is already canonical.
- Changing dispatcher behavior in `dispatch.py` / `reconcile.py` — the code
  is already aligned with the contract.
- Touching `/learn`'s cross-feature retry aggregation in
  `agents/workflow-learner.md` — UC-E1 stands as a hypothetical, not a live
  bug, and OQ-3 is explicitly deferred.
- Per-tool wiring or LLM-specific recovery instructions — schemas stay
  tool-agnostic.
- Adding tooling (validators, lint scripts) to detect future drift — a one-
  off audit covers the gap today.

## Approaches Considered

### Approach A: Close as already-done

Mark ORC-7 superseded by `subprocess-per-step-observability` and the
`execute-one-task` refactor; file no changes.

Pros: zero risk, zero diff.

Cons: leaves three concrete drift points standing — the CONVENTIONS.md
consumer entry for a deleted step (UC-E2 broken reference), and two agent
prompts that duplicate Escalation Protocol / Fix Task Protocol semantics
inline (UC-E1 divergent escalation behavior).

### Approach B: Targeted reference sweep (Selected)

Make three localized edits:
1. Remove `phase-signoff` from CONVENTIONS.md line 67's consumers column.
2. Rewrite `agents/developer.md:117–118` to defer escalation to
   `contracts/error-recovery.md § Escalation Protocol` instead of restating
   the rule inline.
3. Rewrite `agents/reviewer.md:200` to reference Fix Task Protocol and use
   tasks.yaml as the artifact name.

Pros: closes the spirit of the ticket (no broken references, no inline
duplication of contract semantics); diff stays under ~10 lines.

Cons: requires discipline to avoid "while I'm here" scope creep into
unrelated retry references.

### Approach C: Approach B plus a `consumers:` field validator

B plus a script under `config/scripts/inline/` that parses CONVENTIONS.md
consumer references and confirms each name resolves to a real
`config/steps/<name>/` directory.

Pros: prevents future drift mechanically.

Cons: speculative — a single drift incident over the lifetime of this table
does not justify a tool. Premature tooling.

### Selected Approach

**Approach B**. The auto-selection heuristic prefers A as lowest complexity,
but A leaves UC-E2 unaddressed by design (the broken reference is exactly
what the ticket was filed against). B is the next-lowest complexity that
satisfies the acceptance criteria. C is rejected as premature tooling.

## High-Level Design

### Architecture Overview

No runtime architecture change. The change is documentation-level:
references in three files are updated to point at the canonical contract
already in place at `config/steps/contracts/error-recovery.md`.

### Key Abstractions

The change reinforces an existing abstraction: **named § sections inside
`contracts/error-recovery.md` are the single source of truth for retry,
escalation, fix-task, and blocked-protocol semantics.** Other documents
reference these sections by name (`§ Fix Task Protocol`,
`§ Escalation Protocol`, `§ Agent Blocked Protocol`) and never restate the
mechanism.

## Low-Level Design

### Components

Three files are touched, each with a single localized edit:

| File | Edit |
|------|------|
| `config/steps/CONVENTIONS.md` | Line 67: remove `, phase-signoff` from consumers column |
| `agents/developer.md` | Lines 117–118: replace "After max_retry_rounds attempts: Escalate to orchestrator..." with a one-line reference to `contracts/error-recovery.md § Escalation Protocol` |
| `agents/reviewer.md` | Line 200: replace "fix tasks in tasks.md format" with "fix tasks per `contracts/error-recovery.md § Fix Task Protocol` (appended to tasks.yaml)" |

### Data Flow

N/A — no runtime data flow changes.

### State Management

N/A — no state changes.

### Error Handling

N/A — this change is itself part of the error-handling documentation
surface; it does not introduce new failure modes.

## Constraints

- All edits must preserve existing § section anchors referenced elsewhere
  (verified: CONVENTIONS.md § anchor table lines 86–89 already matches
  `error-recovery.md`'s `## State Transition Table`, `## Fix Task Protocol`,
  `## Agent Blocked Protocol`, `## Escalation Protocol` headings).
- No changes to `contracts/error-recovery.md` itself.
- No tooling additions.

## Trade-offs

Accepting that the audit is a one-shot rather than a continuous validator.
If the consumer table drifts again, a future ticket can either repeat the
audit or invest in Approach C. The cost of repeating a 30-second grep audit
is lower than the cost of building and maintaining a validator.

## Acceptance Criteria

- AC-1: `config/steps/CONVENTIONS.md` line 67 lists only currently-existing
  step contracts in the consumers column for the Error Recovery row;
  specifically, the token `phase-signoff` does not appear. Verifiable by:
  `grep -n 'Error Recovery' config/steps/CONVENTIONS.md` returns a line
  whose consumers column matches `^.*orchestrate skill, execute-one-task,
  run-phase-review\s*\|.*$` and does not contain `phase-signoff`.
  [traces: UC-1, UC-E2]
- AC-2: `agents/developer.md` does not contain the substring "After
  max_retry_rounds attempts" and contains a reference to
  `contracts/error-recovery.md` (Escalation Protocol section). Verifiable
  by: `! grep -q 'After max_retry_rounds attempts' agents/developer.md`
  exits 0 AND `grep -q 'error-recovery.md' agents/developer.md` exits 0.
  [traces: UC-2, UC-E1]
- AC-3: `agents/reviewer.md` line referencing fix-task generation cites
  `contracts/error-recovery.md` (Fix Task Protocol) and names `tasks.yaml`
  (not `tasks.md`). Verifiable by:
  `grep -q 'tasks.md format' agents/reviewer.md` exits non-zero AND
  `grep -q 'error-recovery.md' agents/reviewer.md` exits 0.
  [traces: UC-2, UC-E1]
- AC-4: No new occurrences of inline retry-counter or escalation prose are
  introduced; the diff for this change touches only the three files above
  and adds no other content. Verifiable by:
  `git diff --name-only HEAD` lists at most `config/steps/CONVENTIONS.md`,
  `agents/developer.md`, `agents/reviewer.md`. [traces: UC-1]

## Decisions

- Edit prose in-place rather than appending a deprecation comment →
  CONVENTIONS.md is a living reference, not a changelog → readers get the
  current truth without grepping for active vs deprecated entries.
- Keep the two agent-prompt edits as short reference lines (one
  sentence each) rather than embedding bullet lists of the contract's
  semantics → preserves the single-source-of-truth invariant.
- Do not touch `agents/workflow-learner.md` retry aggregation → OQ-3 is
  deferred; UC-E1 remains a hypothetical hazard, not a live bug.

## Open Questions

- None blocking. OQ-3 from discovery.md is explicitly deferred.
