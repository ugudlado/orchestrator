# Artifact Format Contracts

Format contracts between producer and consumer steps. Each artifact has a
structural contract that ensures consistent handoff between workflow steps.

---

## Tasks YAML Format Contract

The `tasks.yaml` file is a machine-readable structural contract between
`design-and-draft-artifacts` (producer) and `expand-plan` (consumer). Both
steps MUST use this exact format.

### Format

```yaml
version: 1
tasks:
  - id: T-1
    title: "Wire X to Y"
    agent: developer
    depends_on: []
    files:
      - path/to/file.py
    verify:
      - pytest tests/test_x.py::test_wire
    test_scenarios:
      - "Y observes X's emission"
    # optional fields:
    why: "AC-3"
    change: "edit file.py:42 to call y_emit() instead of y_set()"
  - id: T-2
    title: "Add regression test"
    depends_on: [T-1]
    files:
      - tests/test_x.py
    verify:
      - pytest tests/test_x.py
```

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| version | Yes | Integer `1` |
| tasks | Yes | List of task objects |
| id | Yes | `T-<N>` or `fix-<N>`, unique within the file |
| title | Yes | One line, imperative verb |
| agent | No | `developer` (default when absent) |
| depends_on | No | List of other task ids; empty list or absent means no deps |
| files | Yes | List of file paths the task is allowed to touch |
| verify | Yes | List of commands the developer runs before COMPLETION |
| test_scenarios | No | List of human-readable test cases |
| why | No | Which design.md AC this task serves |
| change | No | The mechanism — what edit, at which file:line |

### Validation rules

- `id` values must be unique within the file (no duplicates).
- `depends_on` references must resolve to another task `id` in the same file.
- No dependency cycles (validated via `expand-plan`'s topo-sort).
- Missing required fields (`id`, `title`, `files`, `verify`) are rejected by
  `validate-tasks-yaml.sh`.

### Validator

`config/scripts/inline/validate-tasks-yaml.sh <path-to-tasks.yaml>` — exits 0
on a well-formed file, exits non-zero with a diagnostic message otherwise.

### Consumers

- `expand-plan` — reads this file to build task-nodes in `workflow_plan[implement].nodes`
- `run-phase-review` (needs_work branch) — appends fix tasks to this file before
  invoking `expand-plan`

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

- `create-or-refresh-artifacts` — reads UC-N identifiers for design.md AC traceability and scope/use cases for task derivation
- `run-phase-review` — verifies structural compliance

---

## Design Format Contract

The `design.md` file is the single feature artifact — it carries both the design
("how") and the Acceptance Criteria. It is a structural contract between
`create-or-refresh-artifacts` (producer and task consumer) and `run-phase-review`
(consumer). The product-level "what & why" (motivation, impact, alternatives at
the feature level) lives on the Linear/backlog ticket, not in this file.

### Format

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

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Frontmatter | Yes | YAML block with `feature-id` and `linear-ticket` |
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
| Acceptance Criteria | Yes | Bulleted list, each with `[traces: UC-N]` referencing discovery.md use case(s) |
| Decisions | Contextual | Populated when non-obvious choices made |
| Open Questions | Yes | Empty section means no blockers |

### Traceability rules

- Every AC item MUST include `[traces: UC-N]` or `[traces: UC-N, UC-EN]`
- The referenced UC-N must exist in the corresponding discovery.md
- Every discovery.md use case (UC-N and UC-EN) should be traced by at least one AC
- AC identifiers: `AC-1`, `AC-2`, ... sequential with no gaps

### Consumers

- `run-phase-review` — reads Acceptance Criteria for AC verification (implement phase) and verifies structural compliance and traceability

---

## Diagnosis Format Contract

The bugfix phase-opening brief lives in `discovery.md` (same filename as feature/spike
`explore` output). Internal structure follows this contract; `diagnose` is the producer
and `design-and-draft-artifacts` / `run-phase-review` are consumers. Only produced in
the bugfix schema.

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
Root cause reference: {from discovery.md Root Cause section}

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
| Fix Strategy | Yes | Prose referencing discovery.md Root Cause |
| Affected Files | Yes | Bulleted list, format: `` `file_path:line_number` — description `` |
| Regression Test | Yes | Structured block with Test file, Test name, Asserts, fail-before/pass-after |
| Could This Break Other Things? | Yes | Prose analysis or "No — isolated change" |
| Rollback Plan | Yes | Concrete revert steps or "git revert <commit>" |
| Out of Scope | Yes | Bulleted list or "None — fix is self-contained" |

### Consumers

- `run-phase-review` — verifies structural compliance and discovery.md reference
