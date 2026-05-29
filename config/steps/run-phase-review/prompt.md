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
   Apply `when:` conditions on metrics (e.g., test_coverage only when tdd_required).
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
   Return COMPLETION per contracts/done-payload.md with:
     outputs.phase_review_report: {verdict: pass}
     review_score: { overall: <N>, dimensions: { spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N> } }
     artifacts: [phase-review.md]
7. If FAIL:
   a. Generate fix tasks per Fix Task Protocol (contracts/error-recovery.md):
      one fix task per finding, each with Finding, Scope, and Approach.
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
   d. Return COMPLETION per contracts/done-payload.md with:
      outputs.phase_review_report: {verdict: needs_work}
      review_score: { overall: <N>, dimensions: {...} }
      artifacts: [phase-review.md]
   e. Follow Error Recovery Contract (contracts/error-recovery.md):
      - If retries >= phase verify.max_retries (default 3):
        execute on_max_retries action per § Escalation Protocol.
      - Otherwise: fix task-nodes are now in the DAG; the dispatcher
        schedules them before re-running this review step.

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
