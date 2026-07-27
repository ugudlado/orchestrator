# Design Format Contract (design.md)

The `design.md` file is the single feature artifact — it carries both the design
("how") and the Acceptance Criteria. It is a structural contract between
`design` (producer and task consumer) and `review`
(consumer). The product-level "what & why" (motivation, impact, alternatives at
the feature level) lives on the Linear/backlog ticket, not in this file.

## Format

```markdown
---
feature-id: FEATURE-ID
linear-ticket: HL-XXX
---

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

## Acceptance Criteria

- AC-1: {testable criterion using Given/When/Then} [traces: UC-N]
- AC-2: {testable criterion} [traces: UC-N, UC-EN]

## Decisions

- {Decision} → {Rationale} → {Consequence}

## Open Questions

- {Unresolved questions that may affect implementation}
```

## Field rules

| Field                 | Required   | Format                                                                         |
| --------------------- | ---------- | ------------------------------------------------------------------------------ |
| Frontmatter           | Yes        | YAML block with `feature-id` and `linear-ticket`                               |
| Context               | Yes        | Prose describing problem space                                                 |
| Goals                 | Yes        | Bulleted list, at least one                                                    |
| Non-Goals             | Yes        | Bulleted list, at least one                                                    |
| Approaches Considered | Yes        | At least 2 approaches with pros/cons                                           |
| Selected Approach     | Yes        | References constraints that ruled out alternatives                             |
| Architecture Overview | Yes        | System-level component interaction                                             |
| Key Abstractions      | Yes        | Core interfaces or patterns introduced                                         |
| Components            | Contextual | Required when >2 components involved                                           |
| Data Flow             | Contextual | Required when data passes through >1 component                                 |
| State Management      | Contextual | Required when mutable state exists                                             |
| Error Handling        | Contextual | Required when external dependencies or user input involved                     |
| Constraints           | Yes        | "None beyond standard project conventions" if genuinely none                   |
| Trade-offs            | Yes        | At least one trade-off articulated                                             |
| Acceptance Criteria   | Yes        | Bulleted list, each with `[traces: UC-N]` referencing discovery.md use case(s) |
| Decisions             | Contextual | Populated when non-obvious choices made                                        |
| Open Questions        | Yes        | Empty section means no blockers                                                |

## Traceability rules

- Every AC item MUST include `[traces: UC-N]` or `[traces: UC-N, UC-EN]`
- The referenced UC-N must exist in the corresponding discovery.md
- Every discovery.md use case (UC-N and UC-EN) should be traced by at least one AC
- AC identifiers: `AC-1`, `AC-2`, ... sequential with no gaps

## Consumers

- `review` — reads Acceptance Criteria for AC verification (implement phase) and verifies structural compliance and traceability
