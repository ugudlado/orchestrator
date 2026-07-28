---
name: review
description: "Phase quality review against the project quality bar. Use when reviewing implementation quality, scoring a phase, or gating merge readiness."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#reviewer
---

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
2. Run the target repo's `verify_commands` map from its `spec/project.yaml`
   (top-level, not under quality_bar). Execute every command listed. Any
   non-zero exit is a critical correctness finding — cannot pass this round.
   If `verify_commands` is not configured: this is itself a critical finding
   in spec_compliance (missing quality gate) — cannot pass this round. Write
   phase-review.md noting the missing map and what to add, and return
   COMPLETION with `status: failed` per step 9 below.
3. Score each dimension separately on 1-10 using the same caps and rubric:
   - Dimensions: spec_compliance, correctness, security, simplicity, code_quality
   - For each dimension:
     - Critical finding in this dimension → caps dimension score at scoring.critical_cap
     - Important finding in this dimension → caps dimension score at scoring.important_cap
     - All green for this dimension → dimension score = scoring.green_base
   - Compute overall = minimum of all dimension scores.
   - Award overall +1 (max 10) ONLY if ALL of:
     a. Every artifact exceeds minimum requirements (not just meets them)
     b. No TODO, FIXME, or placeholder text remains in outputs
     c. No retries were used this round
4. Baseline comparison (non-blocking):
   - Read archived state.yaml files: `spec/changes/archive/*/state.yaml`.
   - Filter entries matching current schema (e.g., feature) via the `schema:` field.
   - Compute average `metrics.review_score_avg` across those entries (skip entries missing this field).
   - If current overall is 2 or more points below that average: emit a warning in the report
     ("Quality regression: current score N is 2+ below historical average M for this schema/phase").
   - If no archived state.yaml files exist or no matching entries: skip silently.
5. Quarantine review (implement phase only):
   - If current phase is not implement: skip this step.
   - Read state.yaml for `quarantine_events` (may be absent or empty).
   - For each entry, treat as a **critical finding** in the correctness dimension
     — quarantined tasks are by definition unresolved regressions or test
     failures that autopilot could not self-heal within max_retries.
   - Include in the review report under "Quarantined tasks":
     `T-<N> (reason: <category>, attempts: <K>): <last_detail>`
   - Caps correctness dimension score at scoring.critical_cap until each
     quarantined task either:
     a. Has a fix task appended to tasks.yaml with `status: pending`, OR
     b. Is explicitly accepted by the user (state.yaml contains
     `quarantine_accepted: ["T-<N>", ...]`); for `autopilot` schema,
     quarantined tasks are treated as accepted automatically — no human gate.
6. AC verification with evidence (implement phase only):
   - If current phase is not implement: skip this step.
   - Read design.md for acceptance criteria, using the format contract owned by
     design (§ Design Format Contract).
   - **Patch schema:** when `design.md` is absent, read acceptance criteria from
     `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
     (`spec/changes/<slug>/ticket-context.md`) instead.
     The ticket AC section is the contract — verify each checkbox item with evidence.
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
7. Write the full human-readable report to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/phase-review.md.
8. If overall >= quality_bar.min_phase_review_score and no critical findings: PASS.
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
9. If FAIL:
   a. Generate fix tasks: one fix task per finding, each with Finding, Scope, and Approach.
   Do NOT suggest refactoring or unrelated improvements.
   b. Append fix tasks to tasks.yaml:
   - Read $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml.
   - Find the current last task id (e.g., T-3 or fix-2) for depends_on.
   - Append new entries with ids like fix-1, fix-2, ... (sequential,
     based on existing fix-N entries) with depends_on pointing to the
     current last task id.
   - Each new task MUST have `status: pending`.
   - Write tasks.yaml back to disk.
     c. Return COMPLETION with `status: failed` — the engine routes back via the
     workflow's `on_failure` edge. Do NOT implement retry counting here; the
     engine enforces `max_retries` on the node.
   ```
   COMPLETION:
     status: failed
     outputs:
       phase_review_report: {verdict: needs_work}
     review_score:
       overall: <N>
       dimensions: {spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N>}
     artifacts: [phase-review.md]
   ```

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
- Artifact structural compliance with format contracts (owned by each producer step's prompt.md) is a review criterion.
- When a finding requires a new requirement, the fix MUST update design.md (AC + design) and tasks.yaml atomically — partial updates that sync only one artifact leave the feature in an inconsistent state and will fail re-review.
- For tasks that spec describes as a rewrite, projection, or byte-compatible replacement of an existing producer, AC verification MUST include a value/shape parity check against at least one real payload from the prior implementation — key-presence alone is insufficient. Reviewer must run both the old producer and the new one on a real archived fixture and diff the top-level output keys; any key reduction is an important finding.
- Before scoring the phase, read tasks.yaml and check if any tasks still have `status: pending`. If any pending tasks exist and are not explicitly quarantined in state.yaml, write phase-review.md with verdict incomplete_phase listing the pending task IDs — return COMPLETION with `status: failed` and outputs.phase_review_report: {verdict: incomplete_phase}, and do NOT include review_score. `status: failed` is required so the `on_failure` edge fires back to implement — without it the dispatcher treats the step as completed and silently advances past the implement phase with tasks still pending. This guards against dispatcher bugs or manual advances that reach review before all tasks are complete. <!-- updated: 2026-05-25, source: orc-76, cycle: 1, repo: orchestrator -->
- Spot audit: pick one `evidence.verified` entry from a completed `implement` step in step_history and re-run its `check` command. A mismatch with the recorded `result` is a critical correctness finding (fabricated evidence) — this is the only enforcement on self-reported evidence, so treat a mismatch as severe.

## Verify

- Phase review report written to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/phase-review.md
- When phase_review_report.verdict is pass or needs_work: review_score recorded in step_history as {step_id: review, phase: <current>, status: completed, review_score: { overall: <N>, dimensions: { spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N> } }}
- When phase_review_report.verdict is incomplete_phase: review_score is omitted from step_history (nothing to score)
- All critical findings have either a fix task or are resolved
- phase-signoff will BLOCK if this step's entry is missing from step_history — this step is not optional
