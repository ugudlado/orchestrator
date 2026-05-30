# Run Phase Review

**Intent:** Run phase quality checks and decide pass/retry.

## Inputs

- `task_execution_result`
- `design.md` (optional, at `spec/changes/<slug>/design.md`)
- `tasks.yaml` (optional, at `spec/changes/<slug>/tasks.yaml`)

## Outputs

- `phase_review_report`
- Artifact: `phase-review.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/phase-review.md`.

## Instructions

1. Load scoring config from project.yaml quality_bar.scoring. Apply defaults
   for any missing field:
   - critical_cap: 5 (default)
   - important_cap: 7 (default)
   - green_base: 9 (default)
2. Run the phase's verify.commands (from schema) if present:
   Execute each command. All must exit 0.
3. Check the phase's verify.assertions (from schema):
   Evaluate each assertion. All must be true.
4. Check the phase's verify.metrics (from schema):
   Compare actual values against thresholds.
   Apply `when:` conditions on metrics (the `when:` mechanism filters any metric
   whose gating flag is set in state.yaml).
5. Score each dimension separately on 1-10 using the same caps and rubric:
   - Dimensions: spec_compliance, correctness, security, simplicity, code_quality
   - For each dimension:
     - Critical finding in this dimension → caps dimension score at scoring.critical_cap
     - Important finding in this dimension → caps dimension score at scoring.important_cap
     - All green for this dimension → dimension score = scoring.green_base
   - Compute overall = minimum of all dimension scores.
   - Award overall +1 (max 10) ONLY if ALL of:
     a. Every artifact exceeds minimum requirements (not just meets them)
     b. No TODO, FIXME, or placeholder text remains in outputs
     c. All verify assertions passed on first attempt (no retries used this round)
5b. Baseline comparison (non-blocking):
   - Read archived state.yaml files: `spec/changes/archive/*/state.yaml`.
   - Filter entries matching current schema (e.g., feature) via the `schema:` field.
   - Compute average `metrics.review_score_avg` across those entries (skip entries missing this field).
   - If current overall is 2 or more points below that average: emit a warning in the report
     ("Quality regression: current score N is 2+ below historical average M for this schema/phase").
   - If no archived state.yaml files exist or no matching entries: skip silently.
5bb. Quarantine review (implement phase only):
   - If current phase is not implement: skip this step.
   - Read state.yaml for `quarantine_events` (may be absent or empty).
   - For each entry, treat as a **critical finding** in the correctness dimension
     — quarantined tasks are by definition unresolved regressions or test
     failures that autopilot could not self-heal within max_retries.
   - Include in the review report under "Quarantined tasks":
     `T-<N> (reason: <category>, attempts: <K>): <last_detail>`
   - Caps correctness dimension score at scoring.critical_cap until each
     quarantined task either:
       a. Has a fix task appended to tasks.yaml (and injected via expand-plan), OR
       b. Is explicitly accepted by the user (state.yaml contains
          `quarantine_accepted: ["T-<N>", ...]` — an interactive signoff).

5c. AC verification with evidence (implement phase only):
   - If current phase is not implement: skip this step.
   - Read design.md (feature) or fix-plan.md (bugfix) for acceptance criteria,
     using the format defined in the Format Contract Reference section below
     (§ Design Format Contract or § Fix Plan Format Contract respectively).
   - For each acceptance criterion:
     a. Run the verification check (test, manual check, build gate, or file inspection).
     b. Record pass/fail with evidence (command output, file check result, etc.).
     c. If a criterion uses ALL/EVERY/EACH:
        - Define scope: what "all" means for this criterion (e.g., "all .ts files in src/").
        - Count programmatically: use grep/find/ast to get the total N.
        - Verify each target with evidence.
        - Report: "Verified N/N <target type>" (e.g., "Verified 47/47 API routes").
        - If N differs from any earlier count in spec: note the discrepancy and
          use the fresh count as authoritative.
     d. If a criterion contains FIXED/RESOLVED/COMPLETE claims:
        - Re-run the original search against the ENTIRE source tree from scratch.
        - Do not trust earlier phase counts.
        - Record fresh search result as evidence.
   - If any AC fails: treat as a critical finding in spec_compliance dimension.
5a. Write the full human-readable report to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/phase-review.md.
6. If overall >= phase verify.metrics.review_score.min and no critical findings: PASS.
   Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       phase_review_report: {verdict: pass}
     review_score:
       overall: <N>
       dimensions: {spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N>}
     artifacts: [phase-review.md]
   ```
7. If FAIL:
   a. Generate fix tasks: one fix task per finding, each with Finding, Scope, and Approach.
      Do NOT suggest refactoring or unrelated improvements.
   b. Append fix tasks to tasks.yaml:
      - Read $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml.
      - Find the current last task id (e.g., T-3 or fix-2) for depends_on.
      - Append new entries with ids like fix-1, fix-2, ... (sequential,
        based on existing fix-N entries) with depends_on pointing to the
        current last task-node id (NOT prefixed with task-).
      - Write tasks.yaml back to disk.
   c. Invoke expand-plan to inject the fix task-nodes into the workflow plan:
      Run: `orchestrator expand-plan $STATE_YAML_PATH`
      This appends task-fix-N nodes to workflow_plan and rewires
      run-phase-review.depends_on to the last fix task-node.
   d. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       phase_review_report: {verdict: needs_work}
     review_score:
       overall: <N>
       dimensions: {spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N>}
     artifacts: [phase-review.md]
     state_patch:
       retries: <incremented value>
   ```
   e. If retries >= phase verify.max_retries (default 3): set status paused, surface
      failure summary to user. Otherwise: fix task-nodes are in the DAG; dispatcher
      schedules them before re-running this review step.

### Rules (constraints on how)

- Target score is quality_bar.min_phase_review_score from project.yaml — retry until met.
- Maximum retries from quality_bar.max_retry_rounds — escalate to user if exhausted.
- Run type-check + test + build commands at every phase boundary before scoring.
- Capture concrete findings with fix direction — every finding must be actionable.
- Issues found during verification become new tasks in the current phase. Never skip ahead with unresolved findings.
- Do not advance with unresolved critical findings — these block phase completion regardless of overall score.
- When flags.bugfix is true: zero regressions tolerated. No existing tests may break.
- Fix tasks must be minimal and scoped to the specific failure — no refactoring, no improvements.
- Score of 10 is a first-pass bonus — only achievable when no retries were used this round.
- Operate at staff-level review quality — catch architectural issues, not just surface bugs.
- Artifact structural compliance with format contracts (see Format contract reference in prompt.md) is a review criterion.
- When a finding requires a new requirement, the fix MUST update design.md (AC + design) and tasks.yaml atomically — partial updates that sync only one artifact leave the feature in an inconsistent state and will fail re-review. <!-- learned: 2026-04-17, source: cross-repo-metrics-duckdb, cycle: 11, hits: 15, misses: 9, repo: orchestrator -->
- For tasks that spec describes as a rewrite, projection, or byte-compatible replacement of an existing producer, AC verification MUST include a value/shape parity check against at least one real payload from the prior implementation — key-presence alone is insufficient. Reviewer must run both the old producer and the new one on a real archived fixture and diff the top-level output keys; any key reduction is an important finding. <!-- learned: 2026-04-20, source: single-source-metrics-via-step-events, cycle: 12, hits: 14, misses: 5, repo: orchestrator -->
- Before scoring the phase, check if any task-nodes (step_id starting with 'task-') in the workflow_plan are still pending. If any task-nodes are pending and not explicitly quarantined in state.yaml, write phase-review.md with verdict incomplete_phase listing the pending task IDs — return COMPLETION with outputs.phase_review_report: {verdict: incomplete_phase} and do NOT include review_score. This guards against dispatcher bugs or manual advances that reach run-phase-review before all tasks are complete. <!-- learned: 2026-05-08, source: hl-303, cycle: 38, hits: 14, misses: 3, repo: orchestrator --> <!-- updated: 2026-05-25, source: orc-76, cycle: 1, repo: orchestrator -->

## Verify

- Phase review report written to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/phase-review.md
- When phase_review_report.verdict is pass or needs_work: review_score recorded in step_history as {step_id: run-phase-review, phase: <current>, status: completed, review_score: { overall: <N>, dimensions: { spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N> } }}
- When phase_review_report.verdict is incomplete_phase: review_score is omitted from step_history (nothing to score)
- All critical findings have either a fix task or are resolved
- phase-signoff will BLOCK if this step's entry is missing from step_history — this step is not optional

---

## Format contract reference

Artifact structural compliance is a review criterion. Use the format contracts
below when verifying producer artifacts.

### Discovery Brief Format Contract

Full contract in `config/steps/explore/prompt.md` § Discovery Brief Format Contract.

**Required sections:** Frontmatter (feature-id, linear-ticket), Feature Summary,
Personas & Actors, Use Cases (min 2 happy path UC-N, min 1 error UC-EN), Scope
(In Scope + Out of Scope), UI Direction, Open Questions (OQ-N format).

### Diagnosis Format Contract

Full contract in `config/steps/diagnose/prompt.md` § Diagnosis Format Contract.

**Required sections:** Symptoms, Reproduction Steps (numbered, runnable), Expected
vs Actual, Evidence Gathered, Data Flow Trace, Root Cause (with file:line), Severity
(critical/high/medium/low), Affected Areas, Since When, Linear Ticket.

### Design Format Contract

Full contract in `config/steps/design-and-draft-artifacts/prompt.md` § Design Format Contract.

**Required sections:** Frontmatter (feature-id, linear-ticket), Context, Goals,
Non-Goals, Approaches Considered (min 2 with pros/cons), Selected Approach,
Architecture Overview, Key Abstractions, Constraints, Trade-offs, Acceptance
Criteria (each with `[traces: UC-N]`), Open Questions.

**Traceability:** Every AC must reference a UC-N from discovery.md; every UC must
be traced by at least one AC.

### Tasks YAML Format Contract

Full contract in `config/steps/design-and-draft-artifacts/prompt.md` § Tasks YAML Format Contract.

**Required fields per task:** `id` (T-N or fix-N, unique), `title` (imperative verb),
`files` (list), `verify` (list of commands). `version: 1` required at top level.
`depends_on` references must resolve; no cycles.

### Fix Plan Format Contract

The `fix-plan.md` file is a structural contract between `create-or-refresh-artifacts`
(producer and task consumer) and `run-phase-review` (consumer). Only produced in
the bugfix schema.

**Required sections:**

| Section | Required | Format |
|---------|----------|--------|
| Fix Strategy | Yes | Prose referencing discovery.md Root Cause |
| Affected Files | Yes | Bulleted list: `` `file_path:line_number` — description `` |
| Regression Test | Yes | Test file, Test name, Asserts, fail-before/pass-after |
| Could This Break Other Things? | Yes | Prose analysis or "No — isolated change" |
| Rollback Plan | Yes | Concrete revert steps or "git revert <commit>" |
| Out of Scope | Yes | Bulleted list or "None — fix is self-contained" |
