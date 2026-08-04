# Review feedback format

Review feedback lives **in the artifact under review**, not in a separate
`*-review.md` or a `*_result` completion output. The same shape is the
convention for design review, UX review, and code/phase review.

## Where it lives

| Review              | Artifact updated                                                                         | Section replaced                                                    |
| ------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Design review       | `design.md`                                                                              | `## Review`                                                         |
| UX review           | `ux-artifacts.yaml`                                                                      | `review:` mapping (same fields); optional `## Review` markdown twin |
| Code / phase review | Prefer `## Review` on `design.md` when present; else the change's primary notes artifact | `## Review`                                                         |

Only the `## Review` section may be edited by the reviewer. The rest of the
artifact is owned by the authoring step.

## Template

Copy from `design-reviewer/templates/feedback.md` (or the skill-local twin).
Replace an existing `## Review` section in full, or append if missing.

Required fields:

| Field        | Required | Notes                                       |
| ------------ | -------- | ------------------------------------------- |
| Verdict      | Yes      | `pass` or `needs_work` only                 |
| Overall      | Yes      | Integer 1–10; min of dimension scores       |
| Reviewed     | Yes      | ISO date                                    |
| Scores table | Yes      | One row per dimension for this review type  |
| Findings     | Yes      | Explicit "None — pass." when empty          |
| Guidance     | Yes      | Next action for the author; "Ship." on pass |

## Routing

- Verdict `pass` → COMPLETION `status: completed`
- Verdict `needs_work` → COMPLETION `status: failed` (+ `refresh_artifacts: true` when the authoring step must re-read the artifact)

Do **not** emit `design_review_result`, `code_review_report`, or similar
result handles — the verdict in `## Review` plus COMPLETION `status` is enough.
