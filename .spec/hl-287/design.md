---
feature-id: hl-287
linear-ticket: HL-287
---

# HL-287 Audit — Design (thin)

## Context

HL-287 is an audit of the orchestrator's 45 step contracts **plus** a single
holistic rework plan. Discovery produced the raw categorization in
`discovery.md`. This design describes how that raw categorization is turned
into a single consolidated `spec.md` containing (a) the canonical audit and
(b) an execution plan organized by milestones M1..M8, in one architect pass,
with no implement-phase code.

### Why a Single Execution Plan

An earlier framing split the follow-on work into two follow-up specs (scope
#2 refactor, scope #3 agent-role alignment). That framing is abandoned. Both
scopes share (i) the typed-dispatcher prerequisite — neither can land without
M1's `orchestrator next` / `orchestrator record` extension; (ii) the
workflow-schema resolution risk — every rename or deletion must be validated
against the same three schemas; and (iii) the final CI-grep gate — a single
M8 cross-check is cheaper and more coherent than two per-scope gates.
Splitting would have forced artificial serialisation and duplicated the
cross-cutting work. One plan with parallel lanes (refactor: M2→M3→M4→M5;
role: M6→M7; converging at M8) captures the dependency truth.

## Goals / Non-Goals

### Goals
- Produce one canonical categorization document that scopes #2 and #3 consume.
- Pre-specify the two follow-up tickets (scope #2 refactor, scope #3 role
  alignment) so their architects can pick up immediately.
- Resolve all ambiguous categorization cases per user decisions — zero rows
  remain `ambiguous` in the final audit.

### Non-Goals
- Designing the refactor itself (scope #2).
- Rewriting agent definitions (scope #3).
- Categorizing the bootstrap schema (deferred to its own ticket).
- Any executable code changes.

## Approaches Considered

### Approach 1: Implement phase produces a separate audit document
Run a full specify → implement cycle where the developer agent writes
`audit_proposal.md` as a re-format of `discovery.md`.

- Pros: fits the feature-schema phase gates cleanly.
- Cons: the transformation is cosmetic — discovery.md already has the
  categorization. Pays LLM cost for re-typing. Generates ceremony, not insight.

### Approach 2 (selected): Collapsed plan — architect writes the deliverables during specify
The specify-phase artifacts themselves are the deliverables. Architect writes
`audit.md` + two follow-up spec drafts in one pass, informed by discovery.

- Pros: avoids cosmetic re-work; follow-up tickets ship with drafted specs;
  real architectural thinking (scope decomposition) happens up front.
- Cons: bends the feature schema — implement phase is reduced to verification.
  Phase gates around test coverage don't semantically apply.

### Approach 3: Spike schema instead of feature
Run this as a spike (`/orchestrate spike`) that produces findings only.

- Pros: spike is the canonical schema for "design exercise, not code."
- Cons: spike output is less structured than feature-spec/design/tasks; loses
  the three-deliverable shape. The follow-up ticket specs work better as
  architect-written artifacts under the feature schema.

**Selected: Approach 2.** Pre-specified follow-ups are the highest-value
output; the schema-bending cost is small (two thin verification tasks).

## High-Level Design

```
discovery.md ──► architect ──► spec.md (audit + §Rework Execution Plan)
                                   │
                                   ├──► M1 typed dispatcher
                                   │        │
                                   │   ┌────┴────┐
                                   │   │         │
                                   │  M2..M5   M6..M7
                                   │   │         │
                                   │   └────┬────┘
                                   │        │
                                   └──►     M8 final gate
```

### Component Breakdown

| Component                           | Producer             | Consumer(s)                              |
|-------------------------------------|----------------------|------------------------------------------|
| Categorization table (31 rows)      | discoverer           | architect (spec.md § Audit) → rework plan |
| Resolved ambiguities (AQ-1..4)      | user decisions       | architect (spec.md)                      |
| Misclassified-math identification   | discoverer           | architect (spec.md, M3)                  |
| Rework execution plan (M1..M8)      | architect            | future architects/developers executing each milestone |
| Verification script / grep checks   | developer (implement)| reviewer                                 |

## Constraints

- All evidence cells in `audit.md` must cite file paths + contract field/line refs.
- No `ambiguous` rows may remain in the final table.
- Feature-schema's `test_coverage` and `tdd_required` gates don't semantically
  apply to a document-only feature — `spec.md` documents this in Phase Gate Notes.
- The 16 bootstrap-schema steps are deferred; they appear only as a follow-up
  stub in `audit.md`.

## Trade-offs

- **Schema fit vs. value.** The feature schema expects code outputs with
  TDD-shaped tasks. We accept a loose fit (implement phase is verification-only)
  because the collapsed plan's deliverables are more valuable than running a
  ceremonial implement phase.
- **Completeness vs. scope.** Auditing bootstrap too would yield one canonical
  categorization; but bootstrap has interactive user-review steps that don't
  fit the `agent-driven` / `inline` binary. Deferring avoids forcing a third
  category just for bootstrap.
- **Role consolidation vs. typed I/O.** Scope #3 aligns roles but does NOT
  declare typed inputs/outputs on role definitions — those live on step
  contracts (scope #2). This split came out of the patch-pass 2 correction.

## Open Questions

None. All discovery-phase ambiguities are resolved in `audit.md` (Ambiguous
Cases — RESOLVED section). Two design decisions within scope #3 (whether
`ux-reviewer` stays separate from `reviewer`; whether `haiku-agent`/`sonnet-agent`
move out of `agents/` or get renamed) are explicit follow-up-ticket decisions,
not open questions for this feature.

## Why No Implement-Phase Code

The HL-287 scope #1 deliverable is a proposal document, not executable code.
The implement phase is preserved only to satisfy the feature workflow schema's
phase-gating; its task list contains only verification work (structural checks
on the documents already produced).
