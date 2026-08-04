# UX review feedback

Same convention as design review
(`design-reviewer/reference/feedback-format.md`): feedback lives in the UX
design artifact, not a separate `*-review.md` or `*_result` output.

## Where it lives

Prefer structured fields on `ux-artifacts.yaml` (the UX design artifact):

```yaml
review:
  verdict: pass # pass | needs_work | skipped
  overall: 8 # null when skipped
  reviewed: "2026-08-03"
  scores:
    accessibility: 9
    hierarchy: 8
    consistency: 8
    friction: 7
  findings:
    - id: F1
      title: "Missing accessible name on delete"
      severity: critical
      where: "components/DeleteButton.tsx"
      problem: "…"
      fix: "…"
  guidance: "Ship."
```

When authoring narrative notes, mirror
`ux-reviewer/templates/feedback.md` as a `## Review` markdown section.

## Dimensions

| Dimension     | Focus                                       |
| ------------- | ------------------------------------------- |
| accessibility | names, contrast, keyboard traps             |
| hierarchy     | primary job first, progressive disclosure   |
| consistency   | design-system reuse                         |
| friction      | frequency × pain; destructive-action guards |

## Routing

- `pass` / `skipped` → COMPLETION `status: completed`
- `needs_work` after max fix retries → `status: failed` (escalate)
- Do not emit `critique_score` as the primary contract — keep optional
  `review_score` for metrics; verdict lives under `review.verdict`.
