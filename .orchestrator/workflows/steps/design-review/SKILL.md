---
name: design-reviewer
description: "Review design.md and tasks.yaml for completeness and quality. Use when reviewing a design before implementation."
user-invocable: true
---

# Design Review

**Intent:** Automated critique of `design.md` and `tasks.yaml` before implementation
begins. Feedback and verdict are written into `design.md` itself (a `## Review`
section). On pass, implementation proceeds. On fail, resets back to `design`
so the architect can address the findings.

## Design Reviewer

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

- `design.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md`
- `tasks.yaml` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml`
- Feedback template: `design-reviewer/templates/feedback.md`
- Feedback format contract: `design-reviewer/reference/feedback-format.md`

## Outputs

- Updated `design.md` — replace or append the `## Review` section using the
  feedback template (verdict, scores, findings, guidance). No separate
  `design-review.md` and no `*_result` completion output.
- COMPLETION `status: completed` on pass, `status: failed` on needs_work.

## Instructions

### 0. Run the deterministic gate

Run:

```
bash design-reviewer/eval.sh <design.md> <tasks.yaml>
```

Non-zero exit → skip rubric scoring. Write `## Review` with
`Verdict: needs_work`, put the script's stderr under Findings/Guidance, then
return `status: failed` immediately.

Environmental gate failures (missing validator path / install layout) are not
design defects: run the validator at its real location, note the environment
cause under Findings, and continue scoring if the artifacts themselves are
sound.

### 1. Read artifacts

Read `design.md` and `tasks.yaml` in full before evaluating anything.
Also Read `design-reviewer/reference/feedback-format.md` before writing feedback.

### 2. Score each dimension (1–10)

| Dimension         | What to check                                                                                                                                                                                                                                                                                                                                                                 |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **completeness**  | Goals, Non-Goals, Approaches Considered, Selected Approach, AC section all present and non-empty                                                                                                                                                                                                                                                                              |
| **ac_coverage**   | Every AC in design.md has at least one task in tasks.yaml; every task has a `why` tracing to an AC                                                                                                                                                                                                                                                                            |
| **task_quality**  | Tasks are small and independently verifiable; every task has `verify` commands; no task touches unrelated files. **Critical**: if any task uses TDD-style RED tests (verify expected to fail until a later task), those tests must use `@pytest.mark.xfail(strict=False)` so the verify command exits 0 at commit time — flag missing xfail annotations as a critical finding |
| **feasibility**   | Selected approach is consistent with constraints in discovery.md; no obvious missing dependencies or unresolved open questions                                                                                                                                                                                                                                                |
| **scope_control** | Non-Goals are explicit; no task implements something outside the stated Goals                                                                                                                                                                                                                                                                                                 |

- Critical finding in any dimension → caps that dimension at 4
- Important finding → caps at 7
- Overall = minimum of all dimension scores

### 3. Decide verdict

- Overall >= 8 (min_design_review_score, step-owned) and no critical findings → **pass**
- Otherwise → **needs_work**

### 4. Write feedback into `design.md`

1. Read `design-reviewer/templates/feedback.md`.
2. Fill Verdict, Overall, Reviewed (UTC date), Scores (all five dimensions),
   Findings (or "None — pass."), and Guidance.
3. In `design.md`, **replace** the existing `## Review` section in full (from
   the `## Review` heading through EOF, or through the next peer `##` if one
   ever follows). If missing, **append** the filled section at the end of the
   file.
4. Do not edit any other section of `design.md`. Do not edit `tasks.yaml`.

### 5. Return COMPLETION

On pass:

```
COMPLETION:
  status: completed
  review_score:
    overall: <N>
    dimensions: {completeness: <N>, ac_coverage: <N>, task_quality: <N>, feasibility: <N>, scope_control: <N>}
  artifacts: [design.md]
  outputs:
    reason: "design review pass — overall <N>"
```

On needs_work — set `refresh_artifacts: true` so the architect re-reads
`design.md` (including `## Review`) on the next run:

```
COMPLETION:
  status: failed
  review_score:
    overall: <N>
    dimensions: {completeness: <N>, ac_coverage: <N>, task_quality: <N>, feasibility: <N>, scope_control: <N>}
  artifacts: [design.md]
  outputs:
    reason: "design review needs_work — overall <N>; see ## Review"
  state_patch:
    refresh_artifacts: true
```

The engine routes `failed` via the workflow's `on_failure` edge — the architect
step is re-queued automatically. Do NOT call `orchestrator reset-step` manually.
COMPLETION status is only `completed` or `failed`.

## Rules

- Only the `## Review` section of `design.md` may be edited — no fixes to the
  design body or `tasks.yaml`.
- Emit `status: failed` (not `status: completed`) when verdict is `needs_work`.
- Do not emit `design_review_result` or write `design-review.md`.
- The retry cap is enforced by the engine (`max_retries` on the workflow node).
- Findings must be specific and actionable: name the AC, task id, or section.
- Do not flag style preferences — only structural gaps that would cause
  implementation to fail or miss acceptance criteria.

## Verify

- `design.md` contains a filled `## Review` section matching the feedback template
- Verdict in that section matches COMPLETION status (`pass`↔completed,
  `needs_work`↔failed)
