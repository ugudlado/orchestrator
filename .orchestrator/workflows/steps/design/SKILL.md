---
name: architect
description: "Produce design.md and tasks.yaml from discovery. Use when designing a feature, drafting an approach, or breaking work into tasks."
user-invocable: true
---

# Design and Draft Artifacts

**Intent:** Generate design approaches, select one, then write all phase artifacts
(design.md, tasks.yaml) in a single architect pass. Show artifacts to user for review
on interactive schemas (feature/bugfix); autopilot runs straight through.

## Architect

You design changes before they are implemented. Your output is a design another
developer can build from without guessing.

### Rules

- Name every ambiguous constraint (units, types, boundaries) explicitly, state
  the assumption you chose and why, and keep the design safe under the stricter
  interpretation when in doubt.
- Design to the acceptance criterion, not the ticket's suggested mechanism.
  Prefer extending an existing working mechanism over introducing a new layer;
  reserve new infrastructure for a stated, proven need.
- When decomposing work for parallel implementation, identify shared-file
  conflict risk up front and sequence, isolate, or assign a single owner for
  those edits, with an explicit reconciliation step.
- State tradeoffs honestly and recommend by context, not by novelty.

## Inputs

- `discovery.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md`
  (`spec/changes/<slug>/discovery.md`) — the discovery brief this step reads for
  constraints, integration points, and recommended approach.
- Ticket body at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
  (`spec/changes/<slug>/ticket-context.md`) when present — written by
  `load-ticket-context`. Source of truth for scope and ACs.

## Outputs

- Artifact `design.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md`
  (`spec/changes/<slug>/design.md`) — includes Approaches Considered and
  Selected Approach (name, complexity XS–XL, rationale).
- Artifact `tasks.yaml` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml`
  (`spec/changes/<slug>/tasks.yaml`).
- `discovery.md` Key Decisions updated with the chosen direction.
  No COMPLETION `design_direction` / `complexity` / `updated_artifact_set`
  handles — those live in the files above.

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

4. Record the chosen direction, complexity (XS/S/M/L/XL), and rationale in
   discovery.md's "Key Decisions" section per the Discovery Brief Format
   Contract in explore/SKILL.md. Mirror the same selection under design.md
   Approaches Considered → Selected Approach.

## Part 2: Artifact Generation

5. For each output file (design.md, tasks.yaml) — in dependency order:
   - **Missing**: does not exist → generate.
   - **Stale**: state.yaml records a review rejection or `refresh_artifacts: true` → regenerate.
   - **Current**: exists and no refresh signal → skip.

6. For each file needing generation:
   a. Read the template:
   - design.md → architect/templates/$SCHEMA/design.md
   - tasks.yaml → architect/templates/$SCHEMA/tasks.yaml
     b. Read the artifact's format contract before writing it (full section list,
     field rules, and traceability/validation rules live there):
     - Producing design.md? First Read
       `architect/reference/design-format.md`.
     - Producing tasks.yaml? First Read
       `architect/reference/tasks-format.md`.
       c. Generate using available context (discovery brief, design direction, change description).
       d. Write to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/<file>.

   **Required sections (degradation floor — even without reading the format
   contract, produce these exact sections/fields so a skipped Read yields the
   right structure with thinner content, never an invented format):**
   - design.md sections, in order: frontmatter (`feature-id`, `linear-ticket`) →
     Context → Goals / Non-Goals → Approaches Considered (≥2 + Selected Approach) →
     High-Level Design (Architecture Overview, Key Abstractions) → Low-Level Design
     (Components, Data Flow, State Management, Error Handling) → Constraints →
     Trade-offs → Acceptance Criteria (each `AC-N` with `[traces: UC-N]`) →
     Decisions → Open Questions → optional `## Review` stub (owned by
     design-review; when regenerating after `refresh_artifacts`, read prior
     `## Review` findings, address them in the design body, then clear or leave
     a stub — do not invent verdicts/scores).
   - tasks.yaml top-level: `version: 1`, `tasks: [...]`. Per task, required fields:
     `id` (`T-<N>`/`fix-<N>`), `title`, `files`, `verify`; optional: `depends_on`,
     `test_scenarios`, `why`, `change`, `status`, `reviews` (code-review owned —
     do not invent reviewer comments at design time).

7. Generate tasks.yaml:
   - Read design.md for approach, component breakdown, and acceptance criteria.
     (Product-level motivation/impact lives on the ticket — read
     `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md` /
     `spec/changes/<slug>/ticket-context.md` when present.)
   - If ux-artifacts.yaml exists: reference ux-prototype.html in UI task descriptions.
   - Generate the fewest tasks that cover all acceptance criteria.
   - Write tasks.yaml using the Tasks YAML Format Contract (Read
     `architect/reference/tasks-format.md`).
   - When tdd_required: every implementation task has a preceding test task.
   - RED-task rule: a RED task's `verify` command MUST exit 0 at commit time.
     Use the target runner's pending-test convention — pytest:
     `@pytest.mark.xfail(strict=False)`; Bun: `test.todo(...)` (no expect
     calls, no imports of not-yet-existing symbols); Vitest/Jest: `it.todo`.
     The paired GREEN task flips the pending tests to real assertions. Check
     the repo's runner before writing the RED task. Missing this forced
     design-review retries on BKG-423, BKG-549, and BKG-575.
     <!-- promoted: 2026-07-28 from scenarios tdd-red-needs-xfail / tdd-red-bun-test-todo-not-plain-test / tdd-red-runner-convention-check -->

8. Return COMPLETION (driver calls orchestrator done):
   ```
   COMPLETION:
     status: completed
     artifacts: [design.md, tasks.yaml]
     outputs:
       reason: "design.md and tasks.yaml ready for review/implement"
   ```
   Selected approach name and complexity must already be written into
   `design.md` (Selected Approach) and `discovery.md` (Key Decisions).
   Do not emit `design_direction`, `complexity`, `updated_artifact_set`, or
   path-keyed `outputs:` entries beyond `reason` — `artifacts` plus `reason` is enough.
   COMPLETION status is only `completed` or `failed`.

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
- Output MUST follow the Tasks YAML Format Contract in `architect/reference/tasks-format.md`.
- When flags.bugfix is true: first task MUST be the regression test, second task MUST be the fix. Order matters.
- When spec or design introduces a new archive/state path for any producer (autopilot, sub-workflow), grep existing consumer globs (e.g., `spec/changes/archive/*/state.yaml`) and confirm the new path is matched before committing the artifact. Otherwise downstream consumers (telemetry, /learn) silently skip the new producer.
- SQL sketches in design.md that reference specific field names must be validated against a live row from the target DB (or schema file) before finalizing. Add an explicit note in the task or run a one-query T-0 validation — field name drift between sketch and schema is a common first-review failure.
- Design claims about caller-site capabilities (e.g., 'X already holds an open connection', 'Y already imports Z', 'the caller has access to W') must be verified by grep against HEAD before finalizing the artifact — not inferred from pattern-matching similar code paths. Unverified caller-site claims that prove false become critical findings at phase review and force a full re-spin of all three artifacts.
- Performance budgets in design.md must cite absolute production targets (e.g., 'p99 < 10ms per call under production load') rather than synthetic microbenchmark targets (e.g., '1000 calls in 50ms in a tight loop'). Microbenchmark budgets are easy to set but mislead developers into pivoting away from correct designs when the benchmark fails under artificial conditions that do not match the real workload.
- For every implementation task in a TDD pair (the task that follows the failing-test task), populate the `change:` field with the specific mechanism: which function to edit, what the edit is, and which file:line region it targets. ORC-76 achieved 0 retries across 25 tasks with full `change:` coverage — omitting it forces the developer agent to infer scope from test_scenarios alone, which increases retry risk.
- tasks.yaml verify commands must be repo-root-relative — no absolute paths, no `cd /abs/path &&` prefix. The developer agent runs verify commands from $REPO_ROOT. Hardcoded paths break worktrees and other machines.
- Do not emit `model:` in tasks.yaml — it is an internal dispatch field, not part of the task contract. The step contract owns the model; do not set it per-task.
- In TDD workflows, every RED-phase task (a test task whose verify command is expected to fail until the paired GREEN task runs) MUST include in its `change:` field an explicit instruction to mark the tests with `@pytest.mark.xfail(strict=False)`. Without this annotation, the developer agent hits a contract contradiction: the verify command must exit 0 before commit, but RED tests are designed to fail. The xfail marker makes verify pass while the test is in expected-failure state; the xfail-cleanup-is-part-of-tdd-task rule ensures markers are removed at the phase gate. ORC-118: both implement attempts abandoned when this was missing.
- Before finalizing tasks.yaml, verify that each task's `verify` commands can be satisfied using only the files in that task's `files` list plus files in its `depends_on` chain. A verify command that imports or calls a file not covered by the task's file scope will block the developer agent: implement forbids touching unlisted files, so a failing import makes the verify exit non-zero and the task cannot be committed. ORC-118 T-2 was abandoned because its verify ran `pytest tests/test_parse_completion.py` which imported `orchestrator_next/scripts/workflow/parse-completion.py` — a file the task neither listed nor could touch.
- When design claims depend on data shapes, join keys, field names, or call-site behavior in existing code or archived run artifacts, verify them against live evidence before finalizing design.md — add a "Verified System Boundaries" section that records each claim and its verification source (grep result, archived state.yaml, or schema file). Unverified shape claims that prove false become critical findings at design-review, forcing a full re-spin. ORC-122: pre-verifying join keys (step_id alignment) and multi-state-file aggregation against real archives enabled a first-pass 9/10 design-review.
- Phase-gate tasks (tasks whose sole purpose is to verify the full suite passes before code-review) must scope their `verify` commands to the files changed by this feature, not the full test suite, unless the baseline test suite is known-clean. Before writing a phase-gate task with `pytest <full_suite_dir>`, run the suite and confirm it is green at HEAD. If pre-existing failures exist, narrow the verify command to the feature's targeted test file (e.g., `pytest orchestrator_next/tests/test_<feature_module>.py -v`). A phase-gate task with an unsatisfiable verify command blocks implement and forces a code-review failure — the same outcome as no gate, but with two wasted implement spawns. ORC-119: T-3 required `pytest orchestrator_next/tests/ -q` green but 10 pre-existing failures existed; 2 implement abandons followed.

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
