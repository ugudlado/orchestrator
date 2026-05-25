## Part 1: Design Selection

1. Read the discovery brief at $WORKFLOW_STATE_DIR/$CHANGE_ID/discovery.md for
   constraints, integration points, open questions, and recommended approach.
2. Generate 2-3 design approaches with trade-offs:
   - Each approach: name, description, pros, cons, complexity (XS/S/M/L/XL).
   - Cover different dimensions: simplicity vs extensibility, performance vs maintainability.

3. Select an approach using the auto-selection heuristic (always applied — no interactive pause here):
   a. Map complexity: XS=1, S=2, M=3, L=4, XL=5.
   b. Select the lowest numeric complexity.
   c. On ties: prefer higher module reuse count.
   d. On further ties: select alphabetically by name.
   e. Document criteria, values, and selection.

4. Record the chosen direction and rationale in discovery.md's "Key Decisions" section
   per the Discovery Brief Format Contract in config/steps/explore/prompt.md.

## Part 2: Artifact Generation

5. For each output file (design.md, tasks.yaml) — in dependency order:
   - **Missing**: does not exist → generate.
   - **Stale**: state.yaml records a review rejection or `refresh_artifacts: true` → regenerate.
   - **Current**: exists and no refresh signal → skip.

6. For each file needing generation:
   a. Read the template from $ORCHESTRATOR_HOME/config/templates/$SCHEMA/<template>.
   b. Read the artifact's format contract from the relevant section below.
   c. Generate using available context (discovery brief, design direction, change description).
   d. Write to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/<file>.

7. Generate tasks.yaml:
   - Read design.md for approach, component breakdown, and acceptance criteria.
     (Product-level motivation/impact lives on the ticket — read it from
     state.yaml's linear-ticket/change description if more context is needed.)
   - If ux-artifacts.yaml exists: reference ux-prototype.html in UI task descriptions.
   - Generate the fewest tasks that cover all acceptance criteria.
   - Write tasks.yaml using the Tasks YAML Format Contract below.
   - When tdd_required: every implementation task has a preceding test task.

8. Return COMPLETION per contracts/done-payload.md (driver calls orchestrator done).
   The COMPLETION `outputs:` block MUST carry all five declared outputs:
   - `design.md` and `tasks.yaml` — path-named artifacts; the value is the
     relative path the step wrote (e.g. `spec/changes/$CHANGE_ID/tasks.yaml`).
   - `updated_artifact_set` — the list of artifact files generated this pass.
   - `design_direction` — the name of the selected design approach.
   - `complexity` — the complexity rating of the selected approach (XS/S/M/L/XL).
   Omitting any of these makes `orchestrator done` reject the step with
   `missing_outputs` (exit 3).

## Part 3: Artifact Review (interactive mode only)

9. If auto=false:
   - Print a summary of each artifact written: file name, section count, task count.
   - Print the full contents of tasks.yaml so the user can review scope.
   - Pause and prompt: "Review design.md, tasks.yaml in
     $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/. Reply 'ok' to continue, or describe changes needed."
   - If the user requests changes: apply them to the relevant artifacts and re-present.
   - Once confirmed: proceed to next step.
   If auto=true: skip this pause and return STATUS: completed immediately.

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
