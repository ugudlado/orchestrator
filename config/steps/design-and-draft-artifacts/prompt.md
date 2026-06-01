# Design and Draft Artifacts

**Intent:** Generate design approaches, select one, then write all phase artifacts
(design.md, tasks.yaml) in a single architect pass. Show artifacts to user for review
on interactive schemas (feature/bugfix); autopilot runs straight through.

## Inputs

- `discovery_result` — handle from the explore/diagnose step.
- `discovery.md` at `spec/changes/<slug>/discovery.md` — the discovery brief this step
  reads for constraints, integration points, and recommended approach.

## Outputs

- `updated_artifact_set` — list of artifact files generated this pass.
- `design_direction` — name of the selected design approach.
- `complexity` — complexity rating of the selected approach (XS/S/M/L/XL).
- Artifact `design.md` at `spec/changes/<slug>/design.md`
  (`$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md`).
- Artifact `tasks.yaml` at `spec/changes/<slug>/tasks.yaml`
  (`$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml`).

## Flags

- `tdd_required` — Every implementation task must have a preceding test task.

## Pre-Execute: approach statement required

Before executing the instructions below, emit an APPROACH block — this step writes multi-file
artifacts, so it MUST state its approach first:

```
APPROACH:
  files: <paths that will be created or modified>
  approach: <one sentence describing the mechanism, not the goal>
  not_doing: <what's deliberately out of scope>
```

## Instructions

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
   a. Read the template:
      - design.md → $ORCHESTRATOR_HOME/config/steps/design-and-draft-artifacts/templates/$SCHEMA/design.md
      - tasks.yaml → $ORCHESTRATOR_HOME/config/steps/design-and-draft-artifacts/templates/$SCHEMA/tasks.yaml
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

8. Return COMPLETION (driver calls orchestrator done).
   The COMPLETION `outputs:` block MUST carry all five declared outputs:
   - `design.md` and `tasks.yaml` — path-named artifacts; the value is the
     relative path the step wrote (e.g. `spec/changes/$CHANGE_ID/tasks.yaml`).
   - `updated_artifact_set` — the list of artifact files generated this pass.
   - `design_direction` — the name of the selected design approach.
   - `complexity` — the complexity rating of the selected approach (XS/S/M/L/XL).
   Omitting any of these makes `orchestrator done` reject the step with
   `missing_outputs` (exit 3).

   ```
   COMPLETION:
     status: completed
     outputs:
       design.md: spec/changes/<change_id>/design.md
       tasks.yaml: spec/changes/<change_id>/tasks.yaml
       updated_artifact_set: [design.md, tasks.yaml]
       design_direction: "<selected approach name>"
       complexity: <XS|S|M|L|XL>
   ```

## Part 3: Artifact Review (interactive schemas only)

9. If state.yaml's `schema` is `autopilot`: skip this pause and return STATUS:
   completed immediately — an autonomous run has no human to answer the prompt.
   Otherwise (feature/bugfix):
   - Print a summary of each artifact written: file name, section count, task count.
   - Print the full contents of tasks.yaml so the user can review scope.
   - Pause and prompt: "Review design.md, tasks.yaml in
     $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/. Reply 'ok' to continue, or describe changes needed."
   - If the user requests changes: apply them to the relevant artifacts and re-present.
   - Once confirmed: proceed to next step.

### Rules (constraints on how)

- Design selection and artifact writing happen in one spawn — no round-trip between them.
- Keep artifact content traceable to accepted requirements.
- Avoid implementation details in design.md — those belong in tasks.
- Keep scope explicit — design.md must declare both goals and non-goals.
- Make acceptance criteria testable — each AC must be verifiable by a concrete command or assertion.
- Resolve major design decisions before implementation begins — do not defer to the implementation phase.
- Tasks must be small, verifiable, and ordered.
- Attach verification criteria per task.
- Output MUST follow the Tasks YAML Format Contract in config/steps/design-and-draft-artifacts/prompt.md.
- When flags.bugfix is true: first task MUST be the regression test, second task MUST be the fix. Order matters.
- When spec or design introduces a new archive/state path for any producer (autopilot, sub-workflow), grep existing consumer globs (e.g., `spec/changes/archive/*/state.yaml`) and confirm the new path is matched before committing the artifact. Otherwise downstream consumers (telemetry, /learn) silently skip the new producer. <!-- learned: 2026-04-16, source: HL-278, cycle: 10, hits: 32, misses: 1, repo: orchestrator -->
- SQL sketches in design.md that reference specific field names must be validated against a live row from the target DB (or schema file) before finalizing. Add an explicit note in the task or run a one-query T-0 validation — field name drift between sketch and schema is a common first-review failure. <!-- learned: 2026-04-17, source: learn-and-telemetry-on-duckdb, cycle: 12, hits: 30, misses: 1, repo: orchestrator -->
- Design claims about caller-site capabilities (e.g., 'X already holds an open connection', 'Y already imports Z', 'the caller has access to W') must be verified by grep against HEAD before finalizing the artifact — not inferred from pattern-matching similar code paths. Unverified caller-site claims that prove false become critical findings at phase review and force a full re-spin of all three artifacts. <!-- learned: 2026-04-20, source: pricing-table-in-duckdb, cycle: 16, hits: 27, misses: 1, repo: orchestrator -->
- Performance budgets in design.md must cite absolute production targets (e.g., 'p99 < 10ms per call under production load') rather than synthetic microbenchmark targets (e.g., '1000 calls in 50ms in a tight loop'). Microbenchmark budgets are easy to set but mislead developers into pivoting away from correct designs when the benchmark fails under artificial conditions that do not match the real workload. <!-- learned: 2026-04-20, source: pricing-table-in-duckdb, cycle: 16, hits: 27, misses: 1, repo: orchestrator -->
- For every implementation task in a TDD pair (the task that follows the failing-test task), populate the `change:` field with the specific mechanism: which function to edit, what the edit is, and which file:line region it targets. ORC-76 achieved 0 retries across 25 tasks with full `change:` coverage — omitting it forces the developer agent to infer scope from test_scenarios alone, which increases retry risk. <!-- learned: 2026-05-25, source: orc-76, cycle: 1, hits: 12, misses: 0, repo: orchestrator -->
- tasks.yaml verify commands must be repo-root-relative — no absolute paths, no `cd /abs/path &&` prefix. The developer agent runs verify commands from $REPO_ROOT. Hardcoded paths break worktrees and other machines. <!-- learned: 2026-05-26, source: orc-86, cycle: 1, hits: 9, misses: 1, repo: orchestrator -->
- Do not emit `agent:` in tasks.yaml — it is an internal dispatch field, not part of the task contract. The default is developer; set it only via state.yaml flags, not per-task. <!-- learned: 2026-05-26, source: orc-86, cycle: 1, hits: 9, misses: 1, repo: orchestrator -->

## Verify

Before returning COMPLETION, confirm:

- design.md exists in $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/
- tasks.yaml exists and passes validate-tasks-yaml.sh
- design.md has Acceptance Criteria section with testable criteria
- tasks.yaml follows the Tasks YAML Format Contract
- tasks.yaml covers every acceptance criterion from design.md
- Every task has a verify field listing the behaviors its tests cover
- No verify command in tasks.yaml contains an absolute path or cd /abs/path prefix
- Key Decisions section populated in discovery.md

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
`design-and-draft-artifacts` (producer) and `implement-tasks` (consumer).
Both steps MUST use this exact format.

The authoritative template is `$ORCHESTRATOR_HOME/config/steps/design-and-draft-artifacts/templates/$SCHEMA/tasks.yaml`
— read it in step 6 and use it as the structural skeleton for generation.

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| version | Yes | Integer `1` |
| tasks | Yes | List of task objects |
| id | Yes | `T-<N>` or `fix-<N>`, unique within the file |
| title | Yes | One line, imperative verb |
| depends_on | No | List of other task ids; empty list or absent means no deps |
| files | Yes | List of file paths the task is allowed to touch |
| verify | Yes | List of repo-root-relative commands (no absolute paths, no `cd /abs/path &&`) |
| test_scenarios | No | List of human-readable test cases |
| why | No | Which design.md AC this task serves |
| change | No | The mechanism — what edit, at which file:line |
| status | No | `pending` (default) or `completed`; updated by `implement-tasks` after each commit |
| tokens_in | No | Input tokens used for this task; written by `implement-tasks` on completion |
| tokens_out | No | Output tokens used for this task; written by `implement-tasks` on completion |
| duration_s | No | Wall-clock seconds for this task; written by `implement-tasks` on completion |

### Validation rules

- `id` values must be unique within the file (no duplicates).
- `depends_on` references must resolve to another task `id` in the same file.
- No dependency cycles.
- Missing required fields (`id`, `title`, `files`, `verify`) are rejected by
  `validate-tasks-yaml.sh`.
- `verify` commands must be repo-root-relative — no absolute paths, no `cd /...` prefix.
  The developer agent runs them from `$REPO_ROOT`. Absolute paths break worktrees
  and other machines.

### Validator

`config/steps/design-and-draft-artifacts/validate-tasks-yaml.sh <path-to-tasks.yaml>` — exits 0
on a well-formed file, exits non-zero with a diagnostic message otherwise.

### Consumers

- `implement-tasks` — reads this file, executes pending tasks in order, sets `status: completed` per task
- `run-phase-review` (needs_work branch) — appends fix tasks with `status: pending` before re-dispatch
