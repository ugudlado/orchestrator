---
name: design-review
description: "Review design.md and tasks.yaml for completeness and quality. Use when reviewing a design before implementation."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#reviewer
---

# Design Review

**Intent:** Automated critique of `design.md` and `tasks.yaml` before implementation
begins. On pass, implementation proceeds. On fail, resets back to
`design` so the architect can address the findings.

## Inputs

- `design.md` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design.md`
- `tasks.yaml` at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/tasks.yaml`

## Outputs

- `design_review_result` — `pass` or `needs_work`
- Artifact `design-review.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/design-review.md`

## Instructions

### 0. Run the deterministic gate

Run:

```
bash skills/design-review/eval.sh <design.md> <tasks.yaml>
```

Non-zero exit → skip scoring, return `status: failed` / `design_review_result: needs_work`
immediately with the script's stderr as the findings.

### 1. Read artifacts

Read `design.md` and `tasks.yaml` in full before evaluating anything.

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

### 4a. On pass

Write `design-review.md` with scores and a brief summary. Return:

```
COMPLETION:
  status: completed
  outputs:
    design_review_result: pass
  review_score:
    overall: <N>
    dimensions: {completeness: <N>, ac_coverage: <N>, task_quality: <N>, feasibility: <N>, scope_control: <N>}
  artifacts: [design-review.md]
```

### 4b. On needs_work

Write `design-review.md` with scores, each finding, and specific guidance for the architect.
Set `refresh_artifacts: true` so the architect re-reads the findings on next run.

Return:

```
COMPLETION:
  status: failed
  outputs:
    design_review_result: needs_work
  review_score:
    overall: <N>
    dimensions: {completeness: <N>, ac_coverage: <N>, task_quality: <N>, feasibility: <N>, scope_control: <N>}
  artifacts: [design-review.md]
  state_patch:
    refresh_artifacts: true
```

The engine routes `failed` via the workflow's `on_failure` edge — the architect step
is re-queued automatically. Do NOT call `orchestrator reset-step` manually.

## Rules

- Do not edit `design.md` or `tasks.yaml` — findings only, no fixes.
- Emit `status: failed` (not `status: completed`) when verdict is `needs_work` — the engine handles rerouting.
- The retry cap is enforced by the engine (`max_retries` on the workflow node). Do not implement retry counting here.
- Findings must be specific and actionable: name the AC, task id, or section at fault.
- Do not flag style preferences or subjective improvements — only structural gaps that
  would cause implementation to fail or miss acceptance criteria.

## Verify

- `design-review.md` written with scores and findings
- `status: failed` returned when verdict is `needs_work`, `status: completed` when pass
