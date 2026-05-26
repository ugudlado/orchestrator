---
feature-id: orc-89
linear-ticket: null
---

# Discovery Brief: Emit workflow_issues live to retro.md during workflow runs

## Feature Summary

Today the orchestrator workflow has a *consumption* side for `workflow_issues` — `record.py` accepts a `workflow_issues` block in the done payload and appends each entry to `spec/changes/<change_id>/retro.md` via `config/scripts/inline/append-retro.sh`. What it lacks is an *emission* side: no driver, inline script, or agent surfaces issues consistently during a run, so retro.md is empty unless a human backfills it after the fact (as happened in the `fix-inline-scripts-tmpdir` archive). This ticket closes the loop by wiring detection points — driver-side anomaly detection, an inline-script helper, and a documented format contract — so a real workflow run produces a non-empty retro.md without manual intervention, feeding downstream `/learn` and backlog-sync work.

## Personas & Actors

- **Dispatch driver** (the `/orchestrate` skill loop): detects out-of-band anomalies it observes between `orchestrator next` and `orchestrator done` (manual phase advance, empty agent usage, sandbox blocks, retry-then-succeed) and includes a `workflow_issues` block when calling `orchestrator done`.
- **Inline script author** (any script under `config/scripts/inline/`): can call a new `record-issue.sh` helper to surface a non-fatal anomaly (e.g., a never-fail script with a non-zero internal branch) without crashing its step.
- **Agent** (developer / reviewer / discoverer / architect): may include `workflow_issues` in its COMPLETION block when it observes a workflow-level problem during its own work (e.g., contract ambiguity, missing input, contradictory rules).
- **`record.py`** (existing consumer): validates the payload, dedups by `dedup_key`, appends to retro.md, returns `retro_appended` count.
- **`/learn` (workflow-learner)** (downstream consumer): not modified here, but its input becomes non-empty as a result.

## Use Cases

### Happy Path

UC-1: Driver-detected anomaly — `/orchestrate` driver notices that a step's `agent_task_result` returned an `agentId:` line but the resulting JSONL has zero usage tokens; the driver attaches a `workflow_issues: [{category: telemetry, severity: workaround-applied, dedup_key: empty-usage-<step_id>, ...}]` block to the next `orchestrator done` payload so the issue lands in retro.md without failing the step.

UC-2: Inline-script self-reported anomaly — `validate-tasks-yaml.sh` (a never-fail script) hits a malformed verify command it can tolerate by skipping; instead of silently exiting 0, it calls `scripts/inline/record-issue.sh` with a JSON blob and the helper writes a sentinel file the driver picks up at done-time, or shells out directly to record by extending the current step's payload via a known marker.

UC-3: Agent-surfaced anomaly — a developer agent finds the step contract's `inputs:` lists an output that no upstream step produces; it returns `workflow_issues: [{category: contract-drift, ...}]` inside its COMPLETION block, the driver maps it into the done payload, and retro.md gets an `ISSUE-N` heading.

UC-4: Format contract documented — `config/steps/contracts/workflow-issues.md` exists and specifies the `workflow_issues` block schema and the `retro.md` H2 layout, so both producers (drivers/agents/scripts) and consumers (`/learn`, complete-workflow renderer) agree on a single shape.

UC-5: Real-run validation — a feature/bugfix/autopilot run that hits at least one anomalous step produces a non-empty `spec/changes/<change_id>/retro.md` with one well-formed `ISSUE-N` block, with no human intervention.

### Error & Edge Cases

UC-E1: Duplicate detection across attempts — the same anomaly fires on retry (e.g., an empty-usage step retried 3×); `dedup_key` ensures one retro.md entry, not three. `append-retro.sh` (or a wrapper) skips appends whose `dedup_key` already exists in the file.

UC-E2: Malformed workflow_issues payload — driver sends `workflow_issues: "not-a-list"` or an entry missing required keys; record.py logs to stderr and continues (the existing best-effort guard is preserved — issue emission must NEVER block a step).

UC-E3: Inline helper called outside a workflow context — `record-issue.sh` invoked with no `CHANGE_ID` / `WORKTREE_PATH` env; exits 0 with a stderr warning, never crashes the caller.

UC-E4: retro.md write fails (disk full, permission, race) — record.py's existing try/except already swallows this, returning `retro_appended: 0`; the step still records `completed`.

## Scope

### In Scope

- Driver-side detection in the `/orchestrate` skill (and `developer` skill where it spawns sub-loops) for a small, named set of anomalies: manual phase advance, empty agent usage when one was expected, retry-then-success, never-fail script exit ≠ 0 surfaced via marker.
- New inline helper `config/scripts/inline/record-issue.sh` that scripts call to surface a non-fatal issue; mechanism for the driver to pick it up at done time (sentinel file under the active state dir).
- Extend the `done-payload.md` contract to document the `workflow_issues` optional field (today the field works but is undocumented in the contract).
- New `config/steps/contracts/workflow-issues.md` contract defining: the `workflow_issues` block schema (fields: `id`, `category`, `severity`, `surfaced_at`, `detail`, `workaround`, `fix_direction`, `dedup_key`), the retro.md H2 block layout already produced by `append-retro.sh`, the enum values for `category` and `severity` (drawn from existing retro.md samples), and the dedup-key semantics.
- Dedup-by-`dedup_key` enhancement in `append-retro.sh` (or record.py prior to invocation) so retried steps don't append the same issue twice.
- Real-run validation: produce a non-empty retro.md from at least one workflow run (bugfix, autopilot iteration, or the orc-89 feature itself).

### Out of Scope

- `/learn` consumption of retro.md — separate ticket already filed (workflow-learner backlog-sync, per session memory 21809). This ticket only writes retro.md; reading it is owned elsewhere.
- Final-report rendering of retro.md in the complete-workflow output — separate ticket (per session memory 21808). Out of scope so this stays focused on the emit side.
- Refactoring `append-retro.sh` to drop the embedded python3 — works today; "while I'm here" change.
- Building a generic issue-tagging UI / dashboard view of retro.md — beyond the scope of "emit live to a file".
- Modifying the `workflow_issues` field's existing best-effort semantics in record.py — keep emission strictly non-blocking; do not turn it into a hard validation gate.

## UI Direction

N/A — no UI components. This is a driver/script/contract change that produces a markdown file.

## Key Decisions

- **Selected design direction: Driver-detects + sentinel-file inline + agent passthrough (Approach A, complexity M).** Rationale: matches the discovery recommendations on OQ-1 (sentinel), OQ-2 (driver-side detection, not record.py), and OQ-3 (producer-supplied dedup_key). Highest module reuse (append-retro.sh + record.py + orchestrate skill all reused). Alternatives considered: (B) record.py-centralized detection — rejected because it expands an already-heavy module and can't see driver-only signals like manual phase advance; (C) post-step hook script — rejected because it introduces a new dispatch concept (post-step hooks) for a small problem, complexity L.
- Detection points are *named and small* (not "instrument everything"). The list of anomaly categories must be enumerable in the contract — open-ended instrumentation invites noise. Categories drawn from observed prior issues in archived retro.md files: `driver-bug`, `driver-contract-ambiguity`, `telemetry-helper-drift`, `metrics-accuracy`, `workflow-gate-too-strict`, `contract-drift`, `tooling-bug`, `other`.
- `dedup_key` is producer-supplied, opaque string. Stable across retries (e.g., `empty-usage:<phase>:<step_id>`) so a single anomaly across N attempts produces one retro.md entry.
- Emission is **best-effort, never-blocking**. A failure in `append-retro.sh`, a malformed `workflow_issues` list, or a missing helper script must never fail the step itself. This preserves the existing record.py invariant.
- Inline-script → driver handoff uses a **sentinel file** under the active state dir (e.g., `$WORKFLOW_STATE_DIR/<change_id>/.pending-issues.jsonl`) which the driver drains and includes in the next `orchestrator done` payload, then deletes. Avoids fragile stdout parsing of script output.

## Open Questions

- OQ-1: Should `record-issue.sh` write the sentinel file *or* call `orchestrator done` directly with a synthetic patch? Sentinel is simpler (driver owns the boundary) but requires the driver to remember to drain it. Recommend: sentinel; the driver already runs between every step and a one-line drain is cheap.
- OQ-2: Where should the driver detect "retry-then-success"? The cleanest signal is in `step_history` (two entries for same `step_id` with `attempt` 1 failed, attempt 2 completed). Is this detected at `orchestrator done` time (by record.py inspecting prior history) or by the dispatch driver inspecting `state.yaml.step_history` before sending its next payload? Recommend: driver-side, to keep record.py free of detection policy.
- OQ-3: Should `dedup_key` be enforced as required, or default to a hash of `(category, surfaced_at, detail)` when omitted? Recommend: required for driver-emitted issues, optional for agent-emitted (agents may not know the right key); record.py fills the default when missing.
- OQ-4: Is there a category enum we should fix now in the contract, or leave open string with a documented "seen so far" list? Recommend: open string with documented examples — the prior retro.md files used ad-hoc categories productively; locking the enum prematurely will gate emission.
- OQ-5: AC-2 mentions the inline helper "without crashing the step" — does the script need to also surface the issue to its caller's stdout/stderr for human-readable logs, or only to the sentinel file? Recommend: sentinel only; logs already accumulate enough noise.
