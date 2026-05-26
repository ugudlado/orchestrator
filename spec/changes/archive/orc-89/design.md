---
feature-id: orc-89
linear-ticket: null
---

# Design: Emit workflow_issues live to retro.md during workflow runs

## Context

`record.py` already accepts a `workflow_issues` block on the `orchestrator done` payload and appends each entry to `spec/changes/<change_id>/retro.md` via `config/scripts/inline/append-retro.sh` (record.py:1810-1846). The consumption side works end-to-end and is best-effort. What's missing is the *emission* side: no driver, inline script, or agent currently surfaces issues, so retro.md is empty unless a human backfills it. Discovery (discovery.md) confirmed three producer surfaces (driver, inline script, agent) and selected Approach A: driver-detects + sentinel-file inline + agent passthrough.

Existing system boundaries this design must respect:
- `record.py`'s `workflow_issues` handling is strictly best-effort and must remain so — no path here may turn it into a hard gate.
- The dispatch driver lives in `skills/orchestrate/SKILL.md`; the loop calls `orchestrator next` → spawn agent → `orchestrator done` and currently has no detection hook.
- `append-retro.sh` writes one H2 block per issue but does not currently dedup; record.py invokes it once per `done` call.

## Goals / Non-Goals

### Goals

- Wire driver-side detection in `skills/orchestrate/SKILL.md` for a *named, small* set of anomalies: manual phase advance, empty agent usage when one was expected, retry-then-success, and inline-script self-reports drained from a sentinel file.
- Provide `config/scripts/inline/record-issue.sh` so inline scripts can surface non-fatal anomalies via a sentinel JSONL file under the active state dir without crashing the caller.
- Document the `workflow_issues` payload schema and the retro.md H2 block layout in a new `config/steps/contracts/workflow-issues.md` contract; reference it from `done-payload.md`.
- Make `append-retro.sh` skip an entry whose `dedup_key` already appears in the existing retro.md, so retried steps produce one block per anomaly.
- Demonstrate a real workflow run that emits a non-empty retro.md without human backfill.

### Non-Goals

- `/learn` (workflow-learner) consumption of retro.md — separate ticket.
- Final-report rendering of retro.md in complete-workflow output — separate ticket.
- Refactoring `append-retro.sh` to drop embedded python3 — works today, out of scope.
- A general post-step hook framework — sentinel file is the minimal mechanism we need.
- Tightening `workflow_issues` validation in record.py beyond what's already there — emission stays best-effort.

## Approaches Considered

### Approach A: Driver-detects + sentinel-file inline + agent passthrough (selected)

The driver (`skills/orchestrate`) inspects `state.yaml.step_history` and the just-completed agent result for the named anomalies before piping `orchestrator done`. Inline scripts that want to self-report call `record-issue.sh`, which appends a JSON line to `$WORKFLOW_STATE_DIR/<change_id>/.pending-issues.jsonl`. The driver drains this file each loop iteration, merges entries into `workflow_issues`, and deletes it after a successful `done`. Agents may include `workflow_issues:` in their COMPLETION block; the driver passes them through verbatim.

- **Pros**: Reuses every existing component (append-retro.sh, record.py, COMPLETION protocol). Driver owns the boundary so detection policy is co-located. Sentinel file is a one-line drain. Three producer paths cover all observed needs.
- **Cons**: Driver loop grows ~30 lines of detection + drain logic. Adds a small file the driver must remember to clean up.
- **Complexity**: M

### Approach B: record.py-centralized detection

Move all detection into `record.py` — on each `done` call, inspect the freshly-appended `step_history` for anomalies and synthesize `workflow_issues` server-side.

- **Pros**: Single detection point; no driver changes; agents and scripts unchanged.
- **Cons**: record.py is already heavy; can't observe driver-only signals (manual phase advance, sandbox blocks fired in the driver shell); centralizes a policy that varies by driver. Forces every detection rule into Python instead of being expressible in the dispatch loop.
- **Complexity**: L

### Approach C: Post-step hook script

Introduce a new dispatch concept — a `post_step` hook script invoked by `orchestrator done` after recording — that performs detection and may itself call record.

- **Pros**: Clean separation of concerns; reusable across drivers.
- **Cons**: New dispatch primitive for a small problem; doubles the surface area of the step contract; hook scripts can't see driver-only state (manual phase advance is a driver action, not a recorded step). YAGNI.
- **Complexity**: L

### Selected Approach

**Approach A.** It matches discovery's recommendations on OQ-1 (sentinel), OQ-2 (driver-side detection), OQ-3 (producer-supplied `dedup_key`); it has the highest module reuse (every existing piece stays); and Approach B can't see driver-only signals while Approach C introduces a new dispatch primitive (post_step hook) for a problem this small doesn't justify.

## High-Level Design

### Architecture Overview

The dispatch driver becomes the single assembly point for `workflow_issues`. Three producer paths feed into it: (1) driver-detected anomalies computed from `state.yaml.step_history` and the just-collected agent COMPLETION, (2) inline-script self-reports drained from a sentinel JSONL file under the active state dir, and (3) agent-supplied `workflow_issues:` lists passed through from COMPLETION. The driver merges all three, attaches the resulting list to the next `orchestrator done` call, and unlinks the sentinel file on success. `record.py` invokes the existing `append-retro.sh` which writes one H2 block per issue to `retro.md`, now with a `dedup_key` skip.

### Key Abstractions

- **`workflow_issues` block**: a JSON list of issue objects on the `orchestrator done` payload. Already accepted by record.py; this design formalizes its schema in a contract.
- **Sentinel file `.pending-issues.jsonl`**: append-only JSONL under `$WORKFLOW_STATE_DIR/<change_id>/`. Each line is a single issue object. Drained and unlinked by the driver after each successful loop iteration.
- **`dedup_key`**: producer-supplied opaque string, stable across retries. Used by `append-retro.sh` to skip duplicate appends. Default when omitted: hash of `(category|surfaced_at|detail)`.
- **Driver detector**: a small block in `skills/orchestrate/SKILL.md`'s loop that computes `workflow_issues` from the just-finished step's COMPLETION + `state.yaml.step_history` and merges them with drained sentinel entries.

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs |
|-----------|---------------|--------|---------|
| `config/steps/contracts/workflow-issues.md` (new) | Schema for issue block + retro.md layout + category/severity values | — | Documentation only |
| `config/scripts/inline/record-issue.sh` (new) | Append a single issue JSON object to `.pending-issues.jsonl` | env: `CHANGE_ID`, `WORKTREE_PATH`; flags: `--category`, `--severity`, `--detail`, `--dedup-key`, `--workaround`, `--fix-direction` | exit 0, sentinel line written |
| `config/scripts/inline/append-retro.sh` (edit) | Existing — extend to skip an issue whose `dedup_key` already exists in retro.md | env: `ISSUES_JSON`, `WORKTREE_PATH`, `CHANGE_ID` | `{appended: N, retro_path: …}` — N excludes dups |
| `skills/orchestrate/SKILL.md` (edit) | Add anomaly detection + sentinel drain to the dispatch loop; document the four detection categories | dispatch loop locals | `workflow_issues` injected into the next `done` payload |
| `config/steps/contracts/done-payload.md` (edit) | Document the optional `workflow_issues` field with a pointer to `workflow-issues.md` | — | Documentation only |
| validation run | Produce a non-empty `retro.md` from a real workflow run | active workflow with a triggering anomaly | retro.md with ≥1 `ISSUE-N` block |

### Data Flow

1. **Inline script self-report**: an inline script under `config/scripts/inline/` hits a non-fatal anomaly → calls `record-issue.sh --category telemetry --severity workaround-applied --detail "…" --dedup-key "…"` → helper writes one JSON line to `$WORKFLOW_STATE_DIR/<change_id>/.pending-issues.jsonl` → script continues normally.
2. **Agent self-report**: an agent observes a workflow-level problem → includes `workflow_issues: [{…}]` in its COMPLETION block → driver maps it into `orchestrator done`.
3. **Driver detection**: between agent result collection and `orchestrator done`, the driver:
   a. Reads `state.yaml.step_history` to detect retry-then-success (last entry's `attempt > 1` and `status = completed`).
   b. Inspects the COMPLETION's `agent_task_result` for `agentId:` plus empty `usage` (proxy for the empty-usage anomaly; the driver does not load the JSONL itself).
   c. Tracks manual phase advances via a driver-local flag set when the driver patches phase outside `orchestrator done`.
   d. Drains `.pending-issues.jsonl` if present, parses each line, merges entries.
4. Driver assembles `workflow_issues: [...]`, attaches to the `done` payload, calls `orchestrator done`, then `rm -f .pending-issues.jsonl` on exit 0.
5. `record.py` invokes `append-retro.sh` (existing path) with the merged list; `append-retro.sh` grep-skips any `dedup_key` already in retro.md, appends survivors as new `ISSUE-N` blocks.

### State Management

- `.pending-issues.jsonl` lives at `$WORKFLOW_STATE_DIR/<change_id>/.pending-issues.jsonl` (active state dir, already gitignored). Append-only; deleted by driver after successful drain.
- `retro.md` is the durable output. No new state.yaml fields.
- Driver-local "manual phase advance fired" flag exists only within one loop iteration (never persisted).

### Error Handling

- `record-issue.sh` called without `CHANGE_ID`/`WORKTREE_PATH` env: prints stderr warning, exits 0. Never crashes the caller.
- Sentinel file unreadable / malformed JSON line: driver logs to stderr, skips that line, continues.
- `append-retro.sh` write failure or malformed `workflow_issues`: record.py's existing try/except swallows it; `retro_appended: 0` returned; step still records `completed`.
- Driver `rm` of sentinel after `done` failure (exit 3): skip deletion so the next attempt can re-drain.
- Dedup grep failure (retro.md missing or unreadable): treat as "not duplicate" — emit the block; over-emission is preferable to silent loss.

## Constraints

- Emission must remain strictly non-blocking — no path here may fail a step.
- No new dispatch primitives (no post-step hooks, no new orchestrator subcommand).
- Reuse `append-retro.sh` and record.py's existing best-effort plumbing; do not move detection logic into record.py.
- Sentinel file path must be inside the active state dir, not `/tmp`, so it inherits the existing state-dir lifecycle and isolation.

## Trade-offs

- **Driver loop carries detection policy.** Detection logic in `skills/orchestrate` couples the driver to specific anomaly categories. Acceptable because the categories are small, named, and listed in the contract; other drivers can adopt the same list (or none) at their own pace.
- **Sentinel file requires driver discipline.** If a driver forgets to drain, issues accumulate silently. Mitigated by: file path is documented in the contract; drain is a one-line addition; surplus entries from earlier runs just produce extra retro blocks (over-emission, not loss).
- **`dedup_key` is producer-supplied opaque string.** Open to typos / inconsistent conventions across producers. Accepted because: locking the format would require coordinated changes across producers; record.py provides a fallback hash; the contract documents conventions like `empty-usage:<phase>:<step_id>`.

## Acceptance Criteria

- AC-1: Given a workflow run where the driver observes one of the named anomalies (manual phase advance, empty agent usage on a step expecting one, retry-then-success), when the driver calls `orchestrator done` for that step, then the payload contains a non-empty `workflow_issues` list and `record.py` writes at least one `ISSUE-N` block to `spec/changes/<change_id>/retro.md`. [traces: UC-1]
- AC-2: Given an inline script invokes `config/scripts/inline/record-issue.sh --category … --severity … --detail … --dedup-key …` during a workflow step, when the helper is called with both env vars and without them, then with env vars a JSON line is present in `.pending-issues.jsonl` and without env vars only a stderr warning is printed; in both cases the helper exits 0. [traces: UC-2, UC-E3]
- AC-3: Given an agent's COMPLETION block includes `workflow_issues: [{...}]`, when the driver maps it into the `orchestrator done` payload, then the corresponding `ISSUE-N` block appears in `retro.md` with the agent's supplied `category`, `severity`, `detail`, and `dedup_key`. [traces: UC-3]
- AC-4: A `config/steps/contracts/workflow-issues.md` file exists and documents the `workflow_issues` block schema (fields, types, allowed values for `category` and `severity`, `dedup_key` conventions) and the retro.md H2 block layout; `done-payload.md` references it from the `workflow_issues` optional field row. [traces: UC-4]
- AC-5: Given the same anomaly fires across multiple retry attempts of a step (same `dedup_key`), when each attempt's `done` payload includes the anomaly, then exactly one `ISSUE-N` block appears in `retro.md` (not one per attempt). [traces: UC-E1]
- AC-6: Given a real workflow run (bugfix, autopilot iteration, or this feature's own implementation) that hits at least one anomalous step, when the workflow completes, then `spec/changes/<change_id>/retro.md` exists and contains at least one well-formed `ISSUE-N` block with no manual backfill. [traces: UC-5]

## Decisions

- Detection lives in the driver, not `record.py` → preserves record.py's narrow boundary role and lets the driver observe driver-only signals (manual phase advance) → `skills/orchestrate/SKILL.md` grows ~30 lines of detection + drain logic, documented inline with the named categories.
- Sentinel file `.pending-issues.jsonl` under `$WORKFLOW_STATE_DIR/<change_id>/` → simplest inline-script → driver handoff that requires no new subprocess plumbing → driver must drain and unlink each iteration.
- `dedup_key` is producer-supplied (required for driver-emitted, optional for agent-emitted with hash fallback) → keeps the contract simple while giving producers full control over the dedup boundary → contract documents conventions (`empty-usage:<phase>:<step_id>`, etc.).
- Dedup enforcement lives in `append-retro.sh` (grep retro.md for existing `dedup_key`) rather than record.py → keeps record.py untouched and dedup co-located with the write → small grep cost per append, acceptable.
- Category field stays an open string with a documented "seen so far" list in the contract → prior retro.md files used ad-hoc categories productively; premature enum lock-down would gate emission.

## Open Questions

- None blocking. Discovery's OQ-1 through OQ-5 resolved per "Decisions" above.

---

## Addendum: ORC-89.1 refactor (post-archive)

Live emission shipped but two architectural gaps surfaced when reviewing how this feature would behave under the shell driver (`scripts/run-workflow.sh` invoked via `orchestrator run <ticket>`):

1. **Driver-side detection was prose-only.** All four detection categories (retry-then-success, empty-usage, manual-phase-advance, sentinel-drain) lived in `skills/orchestrate/SKILL.md` as a checklist the LLM driver runs in its head. The shell driver doesn't read SKILL.md and therefore emitted **none** of these — `workflow_issues` silently degraded to zero under `orchestrator run`.
2. **The two issue categories were conflated.** "Agent had a problem with its work" and "the workflow loop misbehaved" are different concerns with different observers; the original design treated them as one stream emitted by one detector.

### Refactored division of labor

| Issue type | Reporter | Path to retro.md |
|------------|----------|------------------|
| **Agent/work issues** — agent flags semantic problems with its own turn (low confidence, missing input, scope creep) | Agent COMPLETION block | `workflow_issues:` field forwarded by `build-payload agent` → `record.py` → `append-retro.sh` |
| **Workflow/mechanics issues** — loop observed a mechanical anomaly (script failed, tool crashed, retry-then-success, manual phase patch) | Driver (shell or LLM) calls shared helper | `scripts/lib/detect-workflow-issues.sh` → `append-retro.sh` |

Agents cannot see workflow-loop signals (they only see their turn); the loop cannot judge whether an agent's work was anomalous. Each reports what it observes; nothing else.

### Shared executable helper (replaces SKILL.md prose)

`scripts/lib/detect-workflow-issues.sh` becomes the single source of detection logic for workflow-mechanics issues. Both drivers call it; **the helper emits a JSON array on stdout**, which the driver merges into the `workflow_issues` field of the `orchestrator done` payload. There is exactly one writer to `retro.md`: `record.py` → `append-retro.sh`, unchanged from what shipped. The helper never writes to retro.md directly.

- **Shell driver** (`run-workflow.sh`): invoked at three exit points — script-step failure, tool-invocation crash, post-agent (for retry-then-success). Captures helper stdout, merges into the done payload's `workflow_issues` field before piping to `orchestrator done`.
- **LLM driver** (`skills/orchestrate/SKILL.md`): one line replaces the ~30-line prose checklist — "Run `scripts/lib/detect-workflow-issues.sh` and merge its stdout into the done payload." The LLM driver additionally passes `--manual-phase-advance` when it patches phase outside `orchestrator done`.

This collapses the drift trap: adding a new workflow-issue category means editing one shell file, not two prose blocks. Keeping the write path single (helper → done payload → record.py → append-retro.sh) preserves the dedup, telemetry, and best-effort error handling already in place — no second writer to retro.md.

### Soft-fail exit code for inline scripts

The `.pending-issues.jsonl` sentinel protocol and `config/scripts/inline/record-issue.sh` are removed. Inline scripts that want to flag a workflow issue without aborting the run exit with a documented soft-fail code (**exit 10**, reserving 10–19 for future soft-warning variants; verified non-colliding with existing exits 1–7 across `run-workflow.sh` and `config/scripts/inline/`). The shell driver maps exit 10 to `status: completed` plus one `workflow_issues` entry derived from the script's stderr (last 5 lines as `detail`, category `script-warning`, `dedup_key: script-warning:<step_id>`). Any other non-zero exit remains a hard failure.

### Backlog sync moved into `run-learn-cycle`

`run-learn-cycle/contract.yaml` and `prompt.md` are extended so the `workflow-learner` agent — already reading state.yaml at this step — also reads `retro.md` and invokes the `backlog-manager` skill for triage of each unresolved issue. The skill owns dedup-against-existing-tickets, priority assignment, and backend selection (Linear vs Backlog.md auto-detect); workflow-learner just hands it the parsed issue data. New output: `backlog_tickets_synced` (list of ticket ids returned by the skill). No new workflow step is added; the loop closes inside the existing learn-cycle agent.

### Categories changed

| Action | Category | Reason |
|--------|----------|--------|
| **Drop** | `empty-usage` | Telemetry bug in `record.py`, not a workflow anomaly. Wrong layer. |
| **Keep** | `retry-success`, `manual-phase-advance` | Driver-observable workflow signals. |
| **Add** | `script-warning` | Inline-script soft-fail (exit 10) entries. |
| **Keep** | `script-failed`, `tool-crashed` | New driver-detected categories the helper emits at the existing exit points. |

### Files affected

- `skills/orchestrate/SKILL.md` — replace driver-detection prose with single-line helper invocation.
- `scripts/run-workflow.sh` — wire helper at three exit points; map exit 10.
- `scripts/lib/detect-workflow-issues.sh` — **new**: shared detection logic.
- `config/steps/contracts/workflow-issues.md` — rewrite producers/consumers; document exit-10 convention; drop sentinel section; drop `empty-usage`.
- `config/scripts/inline/record-issue.sh` — **delete**.
- `config/steps/run-learn-cycle/{contract.yaml,prompt.md}` — extend with retro→backlog sync.
- Tests targeting `record-issue.sh` and `.pending-issues.jsonl` — delete or rewrite.

### What's preserved

`retro.md` schema, `append-retro.sh` with `dedup_key` skip, `done-payload.md`'s optional `workflow_issues` field, agent COMPLETION passthrough, and `record.py`'s best-effort retro append all stay exactly as shipped.

<!-- Format contract: config/steps/design-and-draft-artifacts/prompt.md § Design Format Contract -->
