# Artifact Format Contracts

Format contracts between producer and consumer steps. Each artifact has a
structural contract that ensures consistent handoff between workflow steps.

---

## Task Format Contract

The `tasks.md` file is a structural contract between `create-or-refresh-artifacts`
(producer) and `execute-next-task` (consumer). Both steps MUST use this exact format.

### Format

```markdown
# Tasks — <Change Title>

- [ ] T-1: <one-line description>
  Verify: <concrete verification check>

- [ ] T-2: <one-line description>
  Verify: <concrete verification check>
  depends: T-1
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Checkbox | Yes | `- [ ]` (pending) or `- [x]` (done) |
| ID | Yes | `T-<N>:` sequential within the file |
| Description | Yes | One line, imperative verb |
| Verify | Yes | Indented 2 spaces, concrete check (command output, file exists, etc.) |
| depends | No | Indented 2 spaces, `depends: T-N` or `depends: T-N, T-M` |

---

## Discovery Brief Format Contract

The `discovery.md` file is a structural contract between `explore` (producer) and
`create-or-refresh-artifacts` / `run-phase-review` (consumers). Both producer and
consumer steps MUST use this exact format.

### Format

```markdown
---
feature-id: FEATURE-ID
linear-ticket: HL-XXX
---

# Discovery Brief: {title}

## Feature Summary

{One paragraph: what this feature does and why it matters.}

## Personas & Actors

{Who interacts with this feature — user roles, system actors, external services.}

## Use Cases

### Happy Path

UC-1: {title} — {actor} wants to {action} so that {outcome}.
UC-2: {title} — {actor} wants to {action} so that {outcome}.

### Error & Edge Cases

UC-E1: {title} — what happens when {error condition}.

## Scope

### In Scope

- {explicit list items}

### Out of Scope

- {explicit list items with rationale}

## UI Direction

{For UI features: playground description. For non-UI: "N/A — no UI components."}

## Key Decisions

- {Decision}: {rationale}

## Open Questions

- OQ-N: {question}
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Frontmatter | Yes | YAML block with `feature-id` and `linear-ticket` |
| Feature Summary | Yes | Single paragraph, no bullet lists |
| Personas & Actors | Yes | At least one actor identified |
| Happy Path Use Cases | Yes | Minimum 2, format: `UC-<N>: title — actor wants to action so that outcome` |
| Error & Edge Cases | Yes | Minimum 1, format: `UC-E<N>: title — what happens when condition` |
| In Scope | Yes | Bulleted list, at least one item |
| Out of Scope | Yes | Bulleted list with rationale per item |
| UI Direction | Yes | "N/A — no UI components" if non-UI |
| Key Decisions | Contextual | Populated by design-exploration step if design=true |
| Open Questions | Yes | Empty section means no blockers. Format: `OQ-<N>: question` |

### Identifier conventions

- Use case IDs: `UC-1`, `UC-2`, ... for happy path; `UC-E1`, `UC-E2`, ... for error/edge
- IDs are sequential within their category with no gaps
- Open question IDs: `OQ-1`, `OQ-2`, ... sequential with no gaps

### Consumers

- `create-or-refresh-artifacts` — reads UC-N identifiers for spec.md traceability and scope/use cases for task derivation
- `run-phase-review` — verifies structural compliance

---

## Specification Format Contract

The `spec.md` file is a structural contract between `create-or-refresh-artifacts`
(producer and task consumer) and `run-phase-review` / `run-feature-verification`
(consumers).

### Format

```markdown
---
feature-id: FEATURE-ID
linear-ticket: HL-XXX
---

# Specification: {title}

## Motivation

{What problem does this solve and why.}

## What Changes

{High-level description of new or modified capabilities.}

## Requirements

### Functional

1. **FR-1**: {requirement description}
2. **FR-2**: {requirement description}

### Non-Functional

1. **NFR-1**: {requirement description}

## Architecture

{Components, data flow, file modification table.}

## Test Strategy

### Test File Paths

{Map each component to its test file.}

### Coverage Targets

{Minimum 90% overall. Per-module targets if needed.}

### Key Test Scenarios

{Critical paths that MUST have test coverage.}

## Acceptance Criteria

- AC-1: {testable criterion using Given/When/Then} [traces: UC-N]
- AC-2: {testable criterion} [traces: UC-N, UC-EN]

## Alternatives Considered

**Alternative N: {name}**
Rejected. {Why rejected or why chosen approach is better.}

## Impact

{Breaking changes, migration, affected areas.}

## Decisions

- {Decision}: {rationale}
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Frontmatter | Yes | YAML block with `feature-id` and `linear-ticket` |
| Motivation | Yes | One or more paragraphs |
| What Changes | Yes | Prose or bulleted list |
| Functional Requirements | Yes | Numbered list, format: `N. **FR-N**: description` |
| Non-Functional Requirements | Yes | Numbered list, format: `N. **NFR-N**: description`. Use "N/A" if genuinely none |
| Architecture | Yes | File modification table for implementation-oriented specs; prose for conceptual |
| Test Strategy | Contextual | Required when code changes exist. "N/A" for YAML/markdown-only changes |
| Acceptance Criteria | Yes | Bulleted list, each with `[traces: UC-N]` referencing discovery.md use case(s) |
| Alternatives Considered | Yes | At least one alternative per major design choice |
| Impact | Yes | "No breaking changes" if none |
| Decisions | Contextual | Populated when non-obvious choices were made |

### Traceability rules

- Every AC item MUST include `[traces: UC-N]` or `[traces: UC-N, UC-EN]`
- The referenced UC-N must exist in the corresponding discovery.md
- Every discovery.md use case (UC-N and UC-EN) should be traced by at least one AC
- AC identifiers: `AC-1`, `AC-2`, ... sequential with no gaps

### Consumers

- `run-feature-verification` — reads Acceptance Criteria for final verification
- `run-phase-review` — verifies structural compliance and traceability

---

## Design Format Contract

The `design.md` file is a structural contract between `create-or-refresh-artifacts`
(producer and task consumer) and `run-phase-review` (consumer).
Only produced in the feature schema when `design=true`.

### Format

```markdown
# Design: {title}

## Context

{Problem space, constraints, and existing system boundaries.}

## Goals / Non-Goals

### Goals

- {What this design achieves}

### Non-Goals

- {What this design explicitly does NOT do}

## Approaches Considered

### Approach 1: {name}

{Brief description, pros, cons.}

### Approach 2: {name}

{Brief description, pros, cons.}

### Selected Approach

{Which approach was chosen and WHY. Reference constraints that ruled out alternatives.}

## High-Level Design

### Architecture Overview

{System-level view — how components interact.}

### Key Abstractions

{Core interfaces, patterns, or concepts introduced.}

## Low-Level Design

### Components

{Component breakdown with responsibilities, inputs, outputs, dependencies.}

### Data Flow

{How data moves through the system.}

### State Management

{What state exists, where it lives, how it changes.}

### Error Handling

{Error handling strategy — what can fail and how.}

## Constraints

{Technical and business constraints.}

## Trade-offs

{What was sacrificed and why it's acceptable.}

## Decisions

- {Decision} → {Rationale} → {Consequence}

## Open Questions

- {Unresolved questions that may affect implementation}
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Context | Yes | Prose describing problem space |
| Goals | Yes | Bulleted list, at least one |
| Non-Goals | Yes | Bulleted list, at least one |
| Approaches Considered | Yes | At least 2 approaches with pros/cons |
| Selected Approach | Yes | References constraints that ruled out alternatives |
| Architecture Overview | Yes | System-level component interaction |
| Key Abstractions | Yes | Core interfaces or patterns introduced |
| Components | Contextual | Required when >2 components involved |
| Data Flow | Contextual | Required when data passes through >1 component |
| State Management | Contextual | Required when mutable state exists |
| Error Handling | Contextual | Required when external dependencies or user input involved |
| Constraints | Yes | "None beyond standard project conventions" if genuinely none |
| Trade-offs | Yes | At least one trade-off articulated |
| Decisions | Contextual | Populated when non-obvious choices made |
| Open Questions | Yes | Empty section means no blockers |

### Consumers

- `run-phase-review` — verifies structural compliance

---

## Diagnosis Format Contract

The `diagnosis.md` file is a structural contract between `diagnose` (producer) and
`create-or-refresh-artifacts` / `run-phase-review` (consumers). Only produced in the
bugfix schema.

### Format

```markdown
# Diagnosis: {title}

## Symptoms

{What's broken — error messages, screenshots, logs.}

## Reproduction Steps

1. {Step 1}
2. {Step 2}
3. {Observed failure}

## Expected vs Actual

- **Expected**: {what should happen}
- **Actual**: {what happens instead}

## Investigation

### Evidence Gathered

- {What was checked — logs, git blame, recent changes, config diffs}

### Data Flow Trace

{Trace from input to error point. Where does it diverge from expected?}

## Root Cause

{The actual cause — not symptoms, not guesses.}
Reference: `file_path:line_number`

## Impact

### Severity

{One of: critical, high, medium, low}

### Affected Areas

{Users, features, or systems impacted.}

### Since When

{Commit, PR, or date when introduced. "Unknown" if not determinable.}

## Linear Ticket

{HL-XXX or "none"}
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Symptoms | Yes | Prose with concrete evidence (error messages, logs) |
| Reproduction Steps | Yes | Numbered list, must be runnable/followable |
| Expected vs Actual | Yes | Two items: `**Expected**:` and `**Actual**:` |
| Evidence Gathered | Yes | Bulleted list of what was checked |
| Data Flow Trace | Yes | Prose tracing data path to error point |
| Root Cause | Yes | Prose with `file_path:line_number` reference |
| Severity | Yes | One of: `critical`, `high`, `medium`, `low` |
| Affected Areas | Yes | Prose or bulleted list |
| Since When | Yes | Commit/PR/date or "Unknown" |
| Linear Ticket | Yes | `HL-XXX` or `none` |

### Consumers

- `create-or-refresh-artifacts` — reads Root Cause for fix-plan.md generation
- `run-phase-review` — verifies structural compliance and root cause evidence

---

## Fix Plan Format Contract

The `fix-plan.md` file is a structural contract between `create-or-refresh-artifacts`
(producer and task consumer) and `run-phase-review` (consumer). Only produced in
the bugfix schema.

### Format

```markdown
# Fix Plan: {title}

## Fix Strategy

{What will be changed and why.}
Root cause reference: {from diagnosis.md Root Cause section}

## Affected Files

- `file_path:line_number` — {what changes and why}

## Regression Test

- **Test file**: {path}
- **Test name**: {name}
- **Asserts**: {what it proves}
- **Must fail before fix**: yes
- **Must pass after fix**: yes

## Risk Assessment

### Could This Break Other Things?

{Other code paths touching the same area. Shared state, side effects, coupling.}

### Rollback Plan

{How to revert if the fix causes issues.}

## Out of Scope

- {Related issues NOT fixed in this change — file separate bugs if needed}
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Fix Strategy | Yes | Prose referencing diagnosis.md Root Cause |
| Affected Files | Yes | Bulleted list, format: `` `file_path:line_number` — description `` |
| Regression Test | Yes | Structured block with Test file, Test name, Asserts, fail-before/pass-after |
| Could This Break Other Things? | Yes | Prose analysis or "No — isolated change" |
| Rollback Plan | Yes | Concrete revert steps or "git revert <commit>" |
| Out of Scope | Yes | Bulleted list or "None — fix is self-contained" |

### Consumers

- `run-phase-review` — verifies structural compliance and diagnosis.md reference
