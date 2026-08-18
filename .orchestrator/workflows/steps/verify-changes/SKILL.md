---
name: verify-changes
description: "Verify the implemented changes satisfy the requirement. Use after implementation to confirm every acceptance criterion is actually in place on the run branch before the workflow advances."
user-invocable: true
---

# Verify Changes

**Intent:** Confirm the work exists and matches the requirement — not who did
it or how it arrived. Transport concerns (message signatures, agent identity)
are handled before this step ever runs; you judge the changes only.

## Verifier

You check the run branch against the requirement and own the verdict.

### Rules

- Verify against the requirement artifacts, not the conversation: the ticket
  context (`spec/changes/<change_id>/ticket-context.md` when present) and the
  design's acceptance criteria (`spec/changes/<change_id>/design.md`).
- Evidence over claims: for every acceptance criterion, point at the concrete
  code or test on the branch that satisfies it. "The reply said it's done" is
  not evidence.
- Run the project's own checks when they exist (test suite, lint, build) and
  report real output, never assumed results.
- A partially met requirement is `failed`, with the unmet items listed — do
  not average a verdict.

## Inputs

- `BRANCH` — the run branch. Fetch it and diff against the base branch
  (`git fetch origin {BRANCH}` then `git diff origin/main...origin/{BRANCH}`,
  or the local branch when no remote is configured).
- `spec/changes/<change_id>/design.md` — acceptance criteria (frontmatter or
  an `## Acceptance criteria` section). If absent, derive the checklist from
  `tasks.yaml` and the ticket context.
- `tasks.yaml` — every task the design declared should be `completed`.

## Steps

1. Fetch the branch; if it does not exist or has no commits beyond base,
   record `failed` (reason: no changes delivered).
2. Build the checklist: acceptance criteria from design.md, else tasks from
   tasks.yaml, else the ticket's stated outcome.
3. For each item, locate the satisfying change in the diff (file + what it
   does) or the test that proves it. Note items with no evidence.
4. Run the project's verification commands when present (e.g. the test
   suite). Capture pass/fail and the failing output when any.
5. Verdict: `completed` only when every checklist item has evidence and the
   project checks pass; otherwise `failed` with the unmet items and failing
   output as the reason.

## Verify

- Every acceptance criterion maps to a concrete diff hunk or test, cited.
- Project checks were actually executed (output quoted), or their absence
  stated.
- The verdict lists unmet items explicitly when failing.
