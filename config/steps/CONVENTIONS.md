# Step Contract Conventions

Rules for designing, evaluating, and modifying step contracts.
Read by workflow-improver (when auditing and when editing).

## Minimal contracts (ORC-104)

A step contract is **pure routing**. It answers one question: which agent (or
script) runs this step. Everything else — intent, inputs, outputs, rules, and
verification — lives in `prompt.md` as prose the agent reads directly.

```
config/steps/<id>/contract.yaml   ← routing only: id, version, agent (or run:)
config/steps/<id>/prompt.md       ← agent kind: all instruction/I-O/rules/verify prose
config/steps/<id>/script.sh       ← script kind: executable payload
```

### contract.yaml fields

Agent step — three keys:

```yaml
id: <step-id>
version: <int>
agent: <agent>     # routing target; resolved to a model via scripts/routes.yaml
```

Script step:

```yaml
id: <step-id>
version: <int>
run: script.sh     # presence of run: marks it a script step
```

- **`kind:` is inferred**, not declared: a `run:` field means `script`; otherwise
  `agent`. Declaring `kind:` explicitly is still accepted (back-compat) but no
  longer required or needed.
- **`inputs` / `outputs` / `rules` are no longer contract fields.** They default
  to `[]` when absent. Their content moves into `prompt.md` (see below). The
  dispatcher's typed-I/O gating becomes a no-op when these are empty — file
  existence is now the agent's responsibility, enforced via the prompt's
  `## Verify` section, not by the dispatch loop.
- The legacy flat-file form (`config/steps/<id>.yaml`) and the older full
  contract shape still load for backward compatibility.

### prompt.md structure (agent kind)

Each agent prompt is organized into these sections so the agent has everything
it needs without reading the contract:

```markdown
# <Step Title>

**Intent:** <one sentence — the one thing this step does>

## Inputs
<named handles + artifact paths this step reads, or "None.">

## Outputs
<COMPLETION output handles + artifact paths this step writes>

## Instructions
<numbered steps — the happy path + error handling>

### Rules (constraints on how)
<declarative constraints; learned rules keep their <!-- learned: ... --> comments>

## Verify
<checkable assertions the agent confirms before returning COMPLETION>
```

Steps with behavioral flags add a `## Flags` section; steps that must emit an
APPROACH block add a `## Pre-Execute` section (see § Pre-Execute Approach
Statement). Embedded format contracts (Discovery Brief, Design, Tasks YAML, Fix
Plan) follow the standard sections, separated by `---`.

> **Consequence (ORC-104):** moving inputs/outputs/rules to prose removed four
> machine checks — typed-I/O dispatch gating, the producer/consumer dataflow
> test, `flags_read` validation in doctor, and `/learn`'s structured rule
> routing/decay. These are now prompt-prose responsibilities. See the ORC-104
> ticket for the tracked follow-ups.

## Contract Files

Detailed format contracts have been extracted into focused files under `contracts/`.
Load only the contracts relevant to your step:

| Contract | File | Used By |
|----------|------|---------|
| Discovery Brief Format Contract | `config/steps/explore/prompt.md` § Discovery Brief Format Contract | explore, design-and-draft-artifacts, run-phase-review |
| Diagnosis Format Contract | `config/steps/diagnose/prompt.md` § Diagnosis Format Contract | diagnose, design-and-draft-artifacts, run-phase-review |
| Design Format Contract | `config/steps/design-and-draft-artifacts/prompt.md` § Design Format Contract | design-and-draft-artifacts, run-phase-review |
| Tasks YAML Format Contract | `config/steps/design-and-draft-artifacts/prompt.md` § Tasks YAML Format Contract | design-and-draft-artifacts, expand-plan, run-phase-review |
| Fix Plan Format Contract | `config/steps/run-phase-review/prompt.md` § Fix Plan Format Contract | design-and-draft-artifacts, run-phase-review |
| Error Recovery (state transitions, blocked protocol, escalation) | `contracts/error-recovery.md` | orchestrate skill, execute-one-task, run-phase-review, phase-signoff |
| Rule Merge (evaluation, merge algorithm, change type detection) | `contracts/rule-merge.md` | orchestrate skill, /learn |
| Resume Token | `contracts/resume-token.md` | orchestrate skill, workflow-state.sh, auto-continue.sh |
| UX Artifacts | `contracts/ux-artifacts.md` | ux-design, design-and-draft-artifacts, execute-one-task |
| Auto-Commit | `contracts/auto-commit.md` | execute-one-task |
| Metrics Schema | `contracts/metrics-schema.md` | compute-swe-metrics, telemetry, learn, workflow-improver |
| Step Dispatch (CLI interface, JSON schema, exit codes) | `contracts/step-dispatch.md` | orchestrate skill, adapter authors, callers of `orchestrator next` |
| Done Payload (`orchestrator done` JSON stdin, COMPLETION block) | `contracts/done-payload.md` | orchestrate skill, developer skill, all agent-spawned steps |

When step contracts reference `CONVENTIONS.md § <Section>`, check whether the
section now lives in a contract file above. The `§` references in step contracts
use short names that map to the contract files:

- `§ Discovery Brief Format Contract` → `config/steps/explore/prompt.md`
- `§ Diagnosis Format Contract` → `config/steps/diagnose/prompt.md`
- `§ Design Format Contract` → `config/steps/design-and-draft-artifacts/prompt.md`
- `§ Tasks YAML Format Contract` → `config/steps/design-and-draft-artifacts/prompt.md`
- `§ Fix Plan Format Contract` → `config/steps/run-phase-review/prompt.md`
- `§ Error Recovery Contract` → `contracts/error-recovery.md`
- `§ Fix Task Protocol` → `contracts/error-recovery.md`
- `§ Agent Blocked Protocol` → `contracts/error-recovery.md`
- `§ Escalation Protocol` → `contracts/error-recovery.md`
- `§ Rules-When Evaluation` → `contracts/rule-merge.md`
- `§ Rule Merge Contract` → `contracts/rule-merge.md`
- `§ Change Type Detection` → `contracts/rule-merge.md`
- `§ Resume Token Format Contract` → `contracts/resume-token.md`
- `§ UX Artifact Contract` → `contracts/ux-artifacts.md`
- `§ Auto-Commit Convention` → `contracts/auto-commit.md`
- `§ Metrics Schema` → `contracts/metrics-schema.md`

Sections that remain in this file are referenced directly (e.g., `CONVENTIONS.md § State Updates`).

## Single Responsibility Principle

Each step does ONE thing. Its `**Intent:**` line in `prompt.md` must be a single
sentence describing that one thing. If the intent uses "and" to join unrelated
verbs, it's doing too much — split it.

**Test**: Can you describe what this step does in 5 words? If not, it's too broad.

## Structure

The instruction prose in `prompt.md` is organized into these sections, each with a
distinct purpose (see § prompt.md structure above for the layout):

| Section | Purpose | Contains |
|---------|---------|----------|
| `## Inputs` | What the step reads | Named handles and artifact paths, or "None." |
| `## Outputs` | What the step produces | COMPLETION output handles and artifact paths. |
| `## Instructions` | Sequential steps for the one thing | Numbered steps the agent follows. Only the happy path + error handling. |
| `### Rules (constraints on how)` | Constraints on HOW to do the one thing | Short declarative statements. Guards and quality criteria. |
| `## Verify` | Assertions that the one thing was done correctly | Checkable conditions. Must be evaluable without re-reading instructions. |

## Evidence-Required Verification

Verify assertions are normally prose checked by the agent itself — the dispatch
loop trusts the agent's self-report. For high-stakes steps, we can require
machine-visible evidence that survives the step's return.

Mark applicable steps with:

```yaml
verify:
  evidence_required: true
  assertions:
    - <existing assertions unchanged>
```

When present, the orchestrate dispatch loop refuses to advance past the step
unless `state.yaml step_history[-1].evidence` is populated with one or more of:

- `commands`: list of `{cmd, exit_code, stdout_tail}` — stdout_tail is last 20 lines
- `file_checks`: list of `{path, exists, sha256, lines}` proving artifacts were written
- `counts`: map of named integer counts (e.g., `{tests_passing: 47, tasks_marked: 3}`)

At least one of the three must be non-empty. If the step's agent returns without
an evidence block, treat as `STATUS: blocked` per Error Recovery Contract.

**When to require it**: any step whose verify assertions make quantitative or
behavioral claims the agent cannot fulfill by narration alone — test runs, AC
verification, artifact creation at specific paths, metric computation. Skip for
steps whose verify only checks state.yaml field presence (tautological for the
dispatch loop).

## Pre-Execute Approach Statement

Implementation-heavy steps — any step that writes code, runs destructive commands,
or produces multi-file artifacts — must emit an approach statement before executing
their `instruction:` block. This guards against the #1 friction type (wrong-approach),
which a rear-view review or test gate cannot catch (the wasted work already happened).

Mark applicable steps with:

```yaml
pre_execute:
  approach_required: true
```

When present, the orchestrate dispatch loop requires the agent to emit three lines
before any other action:

```
APPROACH:
  files: <comma-separated paths that will be created or modified>
  approach: <one sentence describing the mechanism, not the goal>
  not_doing: <what's deliberately out of scope>
```

The dispatch loop records this block verbatim in `state.yaml` under the step's
`step_history` entry as `approach:`, so the decision is durable across resume.

**When to require it**: any step whose agent is `developer`, `architect`, `reviewer`
with edit authority, or any inline step that calls `git`, `rm`, or writes more than
one file. Exploration, read-only review, and single-field state updates do not need it.

## Agent → Model Routing

[`scripts/routes.yaml`](../../scripts/routes.yaml) is the single source of truth for
agent→model mapping. Step contracts declare only `agent:`; the dispatcher resolves
each agent to a model via `routes.yaml`. Do not add a top-level `model:` field to step
contracts — routing belongs in one place so pricing, cost estimates, and the
dashboard stay consistent.

## Where learned rules go

When `/learn` discovers a new rule, route it to the right section **of the step's
`prompt.md`** (ORC-104 — rules no longer live in `contract.yaml`):

| Rule type | Target section | Example |
|-----------|---------------|---------|
| Quality constraint | `### Rules (constraints on how)` | "For FIXED claims, re-verify from scratch" |
| Verification check | `## Verify` | "Catalog count matches full-tree grep count" |
| Process guidance | `## Instructions` (only if it's a step in the existing flow) | Rarely — prefer rules over instruction additions |

**Never** add a rule as a paragraph in `## Instructions`. Instructions describe the
flow; rules constrain it. If you're tempted to add a "### Special Rule" section
inside instructions, it belongs in `### Rules` instead.

Learned rules keep their `<!-- learned: ... -->` metadata comments verbatim when
folded into prose. Because they now live in markdown rather than a YAML `rules:`
list, automated decay routing is no longer wired — see the ORC-104 ticket.

## Flag Dependencies (`## Flags`)

Steps that change behavior based on runtime flags (from `state.yaml.flags`) declare
them in a `## Flags` section in `prompt.md`, so the agent sees which flags shape its
behavior (ORC-104 — formerly the `flags_read:` contract field, now prose).

**Gating vs behavioral flags**: A few flags under `gates:` in `config/workflow.yaml`
(currently just `phase_review`) control *whether* a listed step runs — seed-state
pre-filters those steps out when the gate is false, so they never load. Otherwise the
workflow file's step list is authoritative: a workflow that lists a step runs it
(e.g. ux-design runs on feature, which lists it; not on autopilot, which omits it).
Only flags that change *how* a step runs go in `## Flags`.

### Format

```markdown
## Flags

- `auto_approve_phases` — Pick recommended approach automatically instead of asking user.
- `tdd_required` — Require a test task before each implementation task.
```

### Rules

- **Only list behavioral flags** — flags that change how the step executes, not
  whether it runs. Gating is the schema's job (`if:` / `if not`).
- **Do NOT duplicate skip logic in instructions** when the schema already gates the
  step. The step should assume it will only run when the condition is met.
- **Describe the effect in one sentence** — what the flag changes in the step's behavior.

## Usage Block Contract

Every `step_history` entry MUST include a `usage:` block. Minimum required fields differ
by step type:

| Field | Agent step | Inline step |
|-------|-----------|-------------|
| `duration_ms` | Required | Required |
| `tool_uses` | Required | Required |
| `total_tokens` | Required (0 if unavailable) | Omit (treated as 0) |
| `input_tokens` | Optional | Omit |
| `output_tokens` | Optional | Omit |
| `cost_usd` | Optional | Omit |

### Agent step usage block

```yaml
usage:
  input_tokens: 12000
  output_tokens: 3500
  cache_creation_input_tokens: 2800
  cache_read_input_tokens: 200
  total_tokens: 18500
  cost_usd: 0.0023
  tool_uses: 7
  tool_calls:
    Read: 3
    Bash: 2
    Edit: 1
    Grep: 1
  duration_ms: 42000
```

### Inline step usage block

Inline steps (no `agent:` field, or `agent: inline`) record duration and tool uses only.
Token fields are omitted — consumers treat them as 0.

```yaml
agent: inline
usage:
  tool_uses: 2
  duration_ms: 5000
```

- `duration_ms` = milliseconds between the step's `started_at` and `completed_at`.
- `tool_uses` = count of tool invocations made by the dispatch loop while executing
  the inline step's `instruction:` block.
- The `agent: inline` marker allows `compute-swe-metrics.sh` to bucket inline steps
  separately in `per_agent_tokens` and `per_step` aggregations.

### Rules

- **Every entry must have a usage: block.** Entries without it are flagged by the
  `mark-change-completed` validator (non-blocking stderr warning).
- **Required fields are `duration_ms` and `tool_uses`** for all step types.
- **Token fields are agent-step only.** Never write token fields on inline entries.
- **Inline steps use `agent: inline`.** Do not use a real agent name for inline steps.

## Artifact Layout

Worktrees are required. All workflow files live under the worktree:

| Variable | Contents | Committed? | Location |
|----------|----------|------------|----------|
| `$WORKTREE_ROOT/spec/changes/$CHANGE_ID/` | `state.yaml`, `plan.yaml`, `design.md`, `tasks.yaml`, `diagnose.md`, UX files | Artifacts yes; state gitignored | `$WORKTREE_BASE_DIR/<slug>/spec/changes/<slug>/` |

`WORKTREE_ARTIFACT_DIR` resolves to `$WORKTREE_ROOT/spec/changes` — agents always
write to the worktree. `WORKFLOW_STATE_DIR` ($REPO_ROOT/spec/changes) is the
fallback for CLI invocations outside a worktree context only.

**Lifecycle invariant**: `archive-completed-change` MUST run before
`remove-worktree`. The archive step **moves** the active session dir
(`spec/changes/<slug>/`) to `spec/changes/archive/<slug>/` on the feature
worktree and commits there. `complete-workflow` does
**not** merge or remove the worktree. `orchestrator complete` runs merge
(unconditional — invoking the verb is the signal) then `scripts/complete-feature-teardown.sh`; merge failure
exits without teardown. Removing the worktree before archiving destroys state
with no recovery path.

Steps that write tracked artifacts (design.md, tasks.yaml, diagnose.md, UX files)
MUST use `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`, not `$WORKFLOW_STATE_DIR/$CHANGE_ID/`, as
the artifact destination.

## State Updates

**Agents MUST NOT edit `state.yaml` directly.** All writes go through
`orchestrator done` with a JSON payload (see `contracts/done-payload.md`).
The dispatch driver calls `orchestrator done` after each step; step agents
return a COMPLETION block that the driver maps into the payload.

The section below documents the **resulting** `step_history` entry shape —
reference only, not an editing instruction.

### Standard step_history entry

```yaml
step_history:
  - step_id: <step contract id>
    phase: <current phase name>
    status: completed          # or: failed, blocked
    agent: <agent name or "inline">
    artifacts: [<files created or modified>]  # optional, list artifact filenames
    review_score: <object>     # only for run-phase-review; see State Field Registry for structure
    started_at: <ISO 8601>     # when step execution began
    completed_at: <ISO 8601>   # when step execution finished
    usage:                       # only for agent-spawned steps
      input_tokens: 12000
      output_tokens: 3500
      cache_creation_input_tokens: 2800
      cache_read_input_tokens: 200
      total_tokens: 18500
      cost_usd: 0.0023           # from proxy pricing lookup; omit if unavailable
      tool_uses: 7
      tool_calls:
        Read: 3
        Bash: 2
        Edit: 1
        Grep: 1
      duration_ms: 42000
```

### Rules

- **Always append** — never overwrite existing entries.
- **Use exact field names** — `step_id`, `phase`, `status`, `agent`, `artifacts`.
- **Status values**: `completed`, `failed`, `blocked` — no other values.
- **Artifacts field**: only include files the step created or modified in
  `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/` (tracked artifacts) or `$WORKFLOW_STATE_DIR/$CHANGE_ID/`
  (state files). Omit for steps that don't produce artifacts.
- **`review_score`**: only present on `run-phase-review` entries. Must be a structured object — see State Field Registry for format.
- **Timestamps**: `started_at` and `completed_at` use ISO 8601 format (`2026-04-04T20:00:00Z`).
  Both are required on all entries. The orchestrator records `started_at` before
  dispatching the step and `completed_at` after the step finishes.

### In step contracts

Instead of "Update state.yaml with X completion status", reference:

```yaml
instruction: |
  N. Return a COMPLETION block per contracts/done-payload.md (driver calls
     orchestrator done — agents MUST NOT edit state.yaml directly).
```

This replaces all variants of "update state.yaml with discovery/design/artifact/task/
verification status."

## Repeat Conditions

Schemas use `repeat_until:` to loop step execution. Each condition has a formal
definition so all agents evaluate it identically.

| Condition | Definition |
|-----------|------------|
| `all_tasks_completed` | ORC-65: removed. Task completion is now tracked via per-task step_history entries (one per task-node with status=completed). Use `compute_task_counts()` in `record.py` to derive task counts from `step_history` and `workflow_plan`. |

## State Field Registry

Steps that write to `state.yaml` MUST use the exact field paths below. This
prevents field name drift across agents and ensures resume/metrics consumers
find data where they expect it.

| Field Path | Type | Written By | Values / Format |
|------------|------|-----------|-----------------|
| `status` | string | check-bootstrap-state, mark-change-completed, final-signoff | `active`, `paused`, `completed` |
| `phase` | string | load-project-context, phase-signoff | Current phase name (lowercase, e.g., `specify`, `implement`, `complete`) |
| `next_step` | object | phase-signoff, any step advancing flow | See `contracts/resume-token.md` |
| `step_history` | list | All steps (append-only) | See § State Updates above |
| `flags` | object | load-project-context | Resolved runtime flags (e.g., `{ tdd_required: true, ff: true }`) |
| `ticket_id` | string | create-linear-ticket, `ticket-state-update.sh` (shell loop) | Issue ID (e.g., `HL-123`, `task-42`). Also stored in `.spec.yaml`. |
| `ticket_status` | string | `ticket-state-update.sh` (shell loop) | Last known lane from ticketing backend (e.g., `In Progress`, `Code Review`) |
| `ticket_status_checked_at` | string | `ticket-state-update.sh` (shell loop) | ISO 8601 UTC when `ticket_status` was last polled |
| `ticket_rework` | boolean | `ticket-reconcile.sh` (shell loop) | `true` when ticket moved from review lane back to `In Progress` |
| `ticketing` | string | `ticket-state-update.sh` (shell loop) | `backlog` or `linear` — mirrors `spec/project.yaml` |
| `flags.rework_from_review` | boolean | `ticket-reconcile.sh` (shell loop) | `true` when external ticket rework detected; clear manually or on lane change |
| `archive_path` | string | mark-change-completed | Relative to repo root (e.g., `spec/changes/archive/2026-04-04-HL-123/`) |
| `completed_at` | string | mark-change-completed | ISO 8601 UTC timestamp when the change completed |
| `metrics` | object | compute-swe-metrics (via archive-completed-change) | Full metrics block or `{ status: script_unavailable, reason: "..." }` |
| `approval` | object | phase-signoff, final-signoff | `{ type: user|auto, phase: <name>, timestamp: <ISO> }` |
| `rejection` | object | phase-signoff, final-signoff | `{ phase: <name>, feedback: "...", fix_tasks_created: [T-N, ...] }` |
| `retries` | object | run-phase-review, execute-one-task | `{ <step_id_or_task_id>: <count> }` — per-step/task retry counter |
| `refresh_artifacts` | boolean | run-phase-review (on fail) | `true` when artifacts need regeneration |
| `change_type` | string | design-and-draft-artifacts (after task creation) | `code` or `config_docs` — per `contracts/rule-merge.md` § Change Type Detection |
| `flag_adaptations` | list | design-and-draft-artifacts (when change_type adapts flags) | `[{ flag, original, effective, reason }]` |
| `task_checkpoint` | object | execute-one-task | NOT PERSISTED — `record.py` (`orchestrator done`) drops unknown keys silently; this field will never appear in state.yaml. Per-task completion is now tracked via step_history entries (one per task-node). This row is retained for historical reference only. <!-- learned: 2026-05-19, source: orc-59, cycle: 1, repo: orchestrator --> |
| `workflow_plan` | object | load-project-context | `{ <phase>: { active: [...], filtered: [...] } }`. Includes resolved from schema. Dispatch loop MUST walk ALL phases/steps — workflow is not complete until every active step in every phase is dispatched. |
| `step_history[].review_score` | object | run-phase-review | `{ overall: 9, dimensions: { spec_compliance: 9, correctness: 10, security: 9, simplicity: 9, code_quality: 9 } }` |
| `step_history[].usage` | object | orchestrate skill (dispatch loop) | `{ input_tokens: N, output_tokens: N, cache_creation_input_tokens: N, cache_read_input_tokens: N, total_tokens: N, tool_uses: N, tool_calls: { ToolName: N, ... }, duration_ms: N }`. Only for steps with agent: field. `tool_calls:` is a per-tool-type breakdown where the sum of all values equals `tool_uses`. Omit `tool_calls:` or write `tool_calls: {}` when no tool calls were made. Compute-swe-metrics reads these fields for cost calculation and tool attribution. |
| `escalation_events` | list | orchestrate skill (escalation routing) | See `contracts/architect-escalation.md` § State Recording. Each entry: `{ task_id, type, question, decision, design_amended, tasks_changed, timestamp }` |

### Rules

- **Append-only for lists**: `step_history` is append-only. Never overwrite or reorder.
- **Exact field names**: Use the paths above verbatim. Do not invent aliases.
- **Null means absent**: If a field has no value yet, omit it entirely — do not write `null`.
- **Timestamps**: Use ISO 8601 format (`2026-04-04T20:00:00Z`).

## Idempotent Re-Entry

Steps may be re-executed after partial completion (e.g., crash mid-step, session
interrupted, agent restarted). Every step contract MUST be safe to run again without
producing duplicate or inconsistent effects.

### Rules

- **Check outputs on entry**: At the start of a step, check whether its outputs already
  exist in `state.yaml` or on disk. If the output is present and valid, skip the work
  and continue.
- **Skip completed sub-work**: When a step has multiple sub-operations (e.g., write file,
  commit, update state), check each individually before re-executing. Only re-execute
  the sub-operations that did not complete.
- **Handle stale/incomplete state**: If a prior run left partial output (e.g., file
  written but not committed, checkpoint recorded but step_history not updated), detect
  the inconsistency and resolve it before proceeding with new work.
- **Never duplicate step_history entries**: Before appending to `step_history`, check
  whether an entry with the same `step_id` and `phase` for the current execution already
  exists. If it does, skip the append.
- **Idempotent writes**: Writing the same value to `state.yaml` a second time is safe.
  Prefer overwriting scalar fields rather than guarding them, unless the field is
  append-only (e.g., `step_history`).

### In step contracts

Steps that have meaningful re-entry risk SHOULD document their re-entry check in a
dedicated step 0 in `instruction:`:

```yaml
instruction: |
  0. Re-entry check: read state.yaml for <output_field>. If present and valid, skip
     to step N (post-completion). If present but incomplete, resolve inconsistency first.
  1. ...
```

## Phase Name Matching

When looking up `signoff_policy` from `project.yaml`, normalize the phase name:

1. Convert to lowercase.
2. Replace spaces and hyphens with underscores (e.g., `Design Phase` → `design_phase`).
3. Look up the normalized name in `signoff_policy`.
4. If key not found → **default to `required`** (conservative).

This ensures new phases get signoff by default rather than silently skipping approval.

## Anti-patterns

- **Instruction bloat**: Adding paragraphs of conditional logic to `## Instructions`. Move to `### Rules`.
- **Multi-intent**: Step that computes metrics AND archives AND writes logs. Split into separate steps.
- **Verify-as-instruction**: Writing verification logic in `## Instructions` instead of `## Verify`.
- **Rules in wrong place**: Workflow rules belong in the step's `prompt.md` (`### Rules`). Project-specific learnings belong in project.yaml `learnings:`. CLAUDE.md is a pointer only.
- **Fields back in the contract**: Re-adding `inputs`/`outputs`/`rules`/`verify`/`intent` to `contract.yaml`. The contract is routing only — those belong in `prompt.md` (ORC-104).

## When to split a step

Split when:
1. The intent has two unrelated verbs (e.g., "compute metrics and archive")
2. The step frequently fails at one part but not the other
3. Different agents should handle different parts (e.g., metrics = reviewer, archive = haiku)

Don't split when:
1. Steps are sequential parts of one investigation (reproduce → trace → document)
2. Steps are tightly coupled (check → decide based on check)
3. Splitting would add overhead with no quality benefit

## Rule Lifecycle Convention

Rules in step contracts have two classes: **permanent** (hand-written, original to the step) and **learned** (added by `/learn` via workflow-improver). Only learned rules are subject to decay evaluation.

### Metadata Comment Format

Every rule added by `/learn` MUST include a metadata comment on the same line, immediately after the rule text:

```yaml
rules:
  - "When keeping an intentionally broad catch, annotate with a justification comment. <!-- learned: 2026-04-05, source: HL-194, cycle: 5, hits: 3, misses: 0, repo: shell -->"
```

**IMPORTANT**: Learned rules with metadata comments MUST be quoted (double quotes around the entire string). The `<!-- learned: ... -->` comment contains colons followed by spaces (e.g., `source: HL-194`) which YAML parsers interpret as mapping values, breaking the file. Always wrap the full rule + metadata in double quotes.

| Field | Format | Required | Default |
|-------|--------|----------|---------|
| `learned:` | `YYYY-MM-DD` — date the rule was added | Yes | — |
| `source:` | Feature ID (e.g., `HL-194`) that triggered this rule | Yes | — |
| `cycle:` | `/learn` cycle count when this rule was added | Yes | — |
| `hits:` | Count of features where the rule's step had zero retries | No | `0` |
| `misses:` | Count of features where the rule's step had retries | No | `0` |
| `repo:` | Repo name this rule applies to, or `*` for universal | No | `*` |

**Repo scoping**: Learned rules are scoped to the repo that generated them. When `/learn`
writes a rule, it includes `repo: $REPO_NAME` (e.g., `repo: shell`). The Rule Merge
Contract (`contracts/rule-merge.md`) filters learned rules so only rules matching the
current repo (or `repo: *` universal rules) are applied. This prevents rules learned in
one repo from incorrectly constraining a different repo with a different tech stack.

- **Repo-scoped** (`repo: <name>`): Default. Tech-stack or domain rules — apply only to the originating repo.
- **Universal** (`repo: *`): Workflow mechanics — rules about the workflow system itself that apply everywhere. The evaluator must explicitly classify a rule as universal.

**Backward compatibility**: Rules without `repo:` are treated as `repo: *` (universal).
Rules without `hits`/`misses` fields are treated as `hits: 0, misses: 0`.
The `/learn` cycle updates counters automatically (see `/learn` skill § Rule Effectiveness Update).

### Permanent Rules

Rules **without** a `<!-- learned: ... -->` metadata comment are permanent. They:
- Were hand-written as part of the original step contract
- Are never evaluated for decay
- Are never removed by the decay evaluation process

### Learned Rules (Lifecycle)

Rules **with** a `<!-- learned: ... -->` metadata comment are subject to decay evaluation.

**Effectiveness-based removal**: A learned rule is eligible for removal when ANY of:
- `hits == 0 AND (current_cycle - rule_cycle) > 5` — rule has never demonstrably helped after 5+ features
- `misses / (hits + misses) > 0.7 AND (current_cycle - rule_cycle) > 10` — rule is mostly ineffective (>70% miss rate) over a sufficient sample
- The `source:` feature-id does not appear in recent retry analysis or evaluator findings, AND `hits == 0`

**Effectiveness-based retention**: A learned rule is retained when:
- `hits > 0 AND misses / (hits + misses) <= 0.7` — rule is demonstrably working

A learned rule is flagged for resolution when:
- It offers opposing advice to a newer learned rule in the same step contract on the same topic

### Evaluation Trigger

Decay evaluation runs every 5th `/learn` invocation (see `/learn` skill § Rule Decay Evaluation). Flagged rules are routed to workflow-improver for pruning — never removed inline. Rules without metadata are never touched.

## Metrics Schema

Every workflow that runs `compute-swe-metrics` produces a `metrics:` block in its
archived `state.yaml`. The canonical definition of this block — field registry,
per-schema field variants (required / null / omitted), and consumer contracts for
null-skip and key-absence — is in `contracts/metrics-schema.md`.

When writing or evaluating a step that reads or writes `metrics:` fields, load
`contracts/metrics-schema.md` for the authoritative field list and the explicit-null
vs omit contract. Key rules summarized:

- `resolution.*` fields are explicit YAML null (`~`) for spike; real values for feature/bugfix/chore.
- `review_scores` is omitted entirely (no key) for spike.
- `tokens`, `cost`, `churn`, `per_agent_*` are always present for all schemas.
- `category` identifies the schema so consumers can group across schema types.
