---
name: code-reviewer
description: "Code review (implementation quality) against the project quality bar. Use when reviewing implementation quality, scoring a phase, or gating merge readiness."
user-invocable: true
---

# Run Code Review

**Intent:** Run implementation code-review checks and decide pass/retry.

## Code Reviewer

You review diffs for defects and policy violations, and you own the verdict.

### Rules

- Judge against documented project policy and public-contract definitions, not
  personal preference. Overrule prior reviews explicitly when they conflict
  with policy, and say why.
- Before blaming the diff for a failure, verify it on the base branch and in
  isolation; distinguish pre-existing flakes from regressions, and flag flakes
  for separate tracking instead of blocking or ignoring them.
- Treat speculative abstraction (unused config, one-implementation interfaces,
  layers "for later") as a real defect: request deletion to the minimum that
  ships the feature and name the concrete maintenance cost.
- Give precise verdicts with evidence. No soft "consider simplifying" when you
  mean "remove this".

## Inputs

- `tasks.yaml` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml` — per-task
  `status` from implement (source of truth for what landed this pass).
- `design.md` (optional, at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md`)

## Outputs

- `code_review_report` — COMPLETION verdict handle (`pass` / `needs_work` /
  `incomplete_phase`).
- Artifact: `code-review.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/code-review.md`.
- On needs_work: updated `tasks.yaml` (reopened tasks with `reviews[]` and/or
  new `fix-N` tasks).

## Instructions

1. Scoring config is step-owned (a repo needing a different bar vendors this
   pack in `.orchestrator/config/`):
   - critical_cap: 5
   - important_cap: 7
   - green_base: 9
2. Discover the target repo's verify commands from the repo itself, in order:
   CLAUDE.md / AGENTS.md (stated test/build/lint commands), README, CI config,
   then lockfile/manifest conventions (pyproject → `pytest -q`,
   package.json scripts, etc.). Execute every discovered command. Any
   non-zero exit is a critical correctness finding — cannot pass this round.
   If no verify command is discoverable at all: this is itself a critical
   finding in spec_compliance (missing quality gate) — cannot pass this
   round. Write code-review.md noting what was searched and what the repo
   should document, and return COMPLETION with `status: failed` per step 9
   below.
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
     a. Has been reopened (`status: pending` with a `reviews` entry) or has a
     pending `fix-N` task covering it, OR
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
7. Write the full human-readable report to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/code-review.md.
8. If overall >= 8 (min_code_review_score, step-owned) and no critical findings: PASS.
   Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       reason: "code review pass — overall <N>"
       code_review_report: {verdict: pass}
     review_score:
       overall: <N>
       dimensions: {spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N>}
     artifacts: [code-review.md]
   ```
9. If FAIL:
   a. For each finding, record it against `tasks.yaml` (keep `code_review_report`
   and `code-review.md` as the full report — tasks carry the actionable queue):
   1. **Prefer reopen** when an existing task owns the finding:
      - Owner = task whose `files` cover the finding's primary path, or whose
        `id` is named in the finding. Prefer the most specific completed task.
      - Set that task's `status: pending`.
      - Append to its `reviews` list (create the list if absent):
        `{at: "<ISO-8601 UTC now>", comment: "<actionable finding text>"}`.
      - Optionally tighten `change` / `verify` when the finding requires it;
        do not wipe prior fields.
   2. **Else create** a new `fix-N` task (sequential after existing `fix-*`):
      - `status: pending`, `depends_on` → current last task id.
      - `title`, `files`, `verify`, `why: code-review finding`, `change` scoped
        to the finding (Finding / Scope / Approach in `change` or `comment`).
      - Seed `reviews` with the same `{at, comment}` entry.
   3. Do NOT suggest refactoring or unrelated improvements. One finding →
      one reopen or one new `fix-N` (never both for the same finding).
      b. Write tasks.yaml back to disk.
      c. Return COMPLETION with `status: failed` — the engine routes back via the
      workflow's `on_failure` edge. Do NOT implement retry counting here; the
      engine enforces `max_retries` on the node. COMPLETION status is only
      `completed` or `failed`.
   ```
   COMPLETION:
     status: failed
     outputs:
       reason: "code review needs_work — overall <N>; findings queued on tasks"
       code_review_report: {verdict: needs_work}
     review_score:
       overall: <N>
       dimensions: {spec_compliance: <N>, correctness: <N>, security: <N>, simplicity: <N>, code_quality: <N>}
     artifacts: [code-review.md, tasks.yaml]
   ```

### Rules (constraints on how)

- Target score is 8 (min_code_review_score, step-owned) — retry until met.
- Maximum 3 retry rounds (max_retry_rounds, step-owned) — escalate to user if exhausted.
- Run type-check + test + build commands at every phase boundary before scoring.
- Capture concrete findings with fix direction — every finding must be actionable.
- Issues found during verification become reopen/`reviews` entries or new
  `fix-N` tasks in the current phase. Never skip ahead with unresolved findings.
- Do not advance with unresolved critical findings — these block phase completion regardless of overall score.
- When flags.bugfix is true: zero regressions tolerated. No existing tests may break.
- Fix work must be minimal and scoped to the specific failure — no refactoring, no improvements.
  Prefer reopen + `reviews[]` over new `fix-N` when a task already owns the files.
- Score of 10 is a first-pass bonus — only achievable when no retries were used this round.
- Operate at staff-level review quality — catch architectural issues, not just surface bugs.
- Artifact structural compliance with format contracts (owned by each producer step's prompt.md) is a review criterion.
- When a finding requires a new requirement, the fix MUST update design.md (AC + design) and tasks.yaml atomically — partial updates that sync only one artifact leave the feature in an inconsistent state and will fail re-review.
- For tasks that spec describes as a rewrite, projection, or byte-compatible replacement of an existing producer, AC verification MUST include a value/shape parity check against at least one real payload from the prior implementation — key-presence alone is insufficient. Reviewer must run both the old producer and the new one on a real archived fixture and diff the top-level output keys; any key reduction is an important finding.
- Before scoring the phase, read tasks.yaml and check if any tasks still have `status: pending`. If any pending tasks exist and are not explicitly quarantined in state.yaml, write code-review.md with verdict incomplete_phase listing the pending task IDs — return COMPLETION with `status: failed` and outputs.code_review_report: {verdict: incomplete_phase}, and do NOT include review_score. `status: failed` is required so the `on_failure` edge fires back to implement — without it the dispatcher treats the step as completed and silently advances past the implement phase with tasks still pending. This guards against dispatcher bugs or manual advances that reach code-review before all tasks are complete. <!-- updated: 2026-05-25, source: orc-76, cycle: 1, repo: orchestrator -->
- Spot audit: pick one `evidence.verified` entry from a completed `implement` step in step_history and re-run its `check` command. A mismatch with the recorded `result` is a critical correctness finding (fabricated evidence) — this is the only enforcement on self-reported evidence, so treat a mismatch as severe.

## Verify

- Code review report written to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/code-review.md
- When code_review_report.verdict is pass: review_score recorded in step_history with status: completed. When verdict is needs_work: same review_score shape but status MUST be failed (see step 9c — completed with a needs_work verdict disarms the on_failure edge and the workflow advances past a failing code-review; this happened live on BKG-575). <!-- updated: 2026-07-28, source: bkg-575 gate bypass -->
- Verdict→status mapping is exact: pass → completed; needs_work or incomplete_phase → failed. No other combination is valid.
- When code_review_report.verdict is incomplete_phase: review_score is omitted from step_history (nothing to score)
- All critical findings have either a reopened task with a new `reviews` entry, a new `fix-N` task, or are resolved
- On needs_work, `tasks.yaml` is updated and listed in COMPLETION artifacts alongside `code-review.md`
- phase-signoff will BLOCK if this step's entry is missing from step_history — this step is not optional
