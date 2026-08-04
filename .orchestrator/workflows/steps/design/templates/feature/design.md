---
feature-id: FEATURE-ID
linear-ticket: HL-XXX
---

# Design: {title}

## Context

{Problem space, constraints, and existing system boundaries. The "what & why"
(motivation, impact, alternatives at the product level) lives on the ticket —
this section captures only what the design needs to stand on its own.}

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

{Which approach was chosen, its complexity (XS/S/M/L/XL), and WHY.
Reference constraints that ruled out alternatives.}

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

{Technical and business constraints. "None beyond standard project conventions" if genuinely none.}

## Trade-offs

{What was sacrificed and why it's acceptable.}

## Acceptance Criteria

- AC-1: Given {precondition}, when {action}, then {outcome}. [traces: UC-1]
- AC-2: Given {precondition}, when {action}, then {outcome}. [traces: UC-2, UC-E1]

## Decisions

- {Decision} → {Rationale} → {Consequence}

## Open Questions

- {Unresolved questions that may affect implementation}

## Review

_(Filled by design-review — do not author. See design-reviewer/templates/feedback.md.)_

<!-- Format contract: architect/SKILL.md § Design Format Contract -->
