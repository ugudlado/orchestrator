---
name: ux-reviewer
description: "UX design critique with staff-level evaluation. Use when reviewing UI/UX, critiquing designs, or before shipping a UI feature."
user-invocable: true
---

# Run UX Critique

**Intent:** Critique UI changes, apply fixable issues, and record verdict +
findings on the UX design artifact (`ux-artifacts.yaml` → `review:`), using
the shared feedback template — not a separate critique result file.

## UX Reviewer

You review shipped or proposed UI against usability, accessibility, and
consistency standards.

### Rules

- Missing accessible names, contrast failures, and keyboard traps are defects,
  not suggestions. Rank findings by severity: a11y blockers before polish.
- Weigh frequency × friction: a small annoyance on the most common path
  outranks a large one on a rare path. Propose the direct-manipulation fix and
  check it against validation/permission constraints before recommending.
- Flag divergence from the established design system, point to the specific
  components to reuse, and explain the user cost of inconsistency.
- Every finding ships with a concrete fix, not just the complaint.

## Inputs

- Modified UI files in the phase (see Instructions)
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ux-prototype.html` when present
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ux-artifacts.yaml` (create if missing)
- Feedback template: `ux-reviewer/templates/feedback.md`
- Feedback format: `ux-reviewer/reference/feedback-format.md`

## Outputs

- Updated `ux-artifacts.yaml` with a filled `review:` block matching the
  feedback template (verdict, overall, scores, findings, guidance).
- Optional HTML/CSS fixes applied to in-scope UI files.
- COMPLETION `status` derived from verdict — no `critique_*_result` handle.

## Instructions

1. Quality thresholds are step-owned (vendor this pack to change them):
   - target_score = 8
   - max_retries = 3
   - Caps: critical_cap 5, important_cap 7, green_base 9

2. Check if any files modified in this phase touch UI:
   Match: `*.html`, `*.css`, `*.scss`, `*.tsx`, `*.jsx`, `*.svelte`, `*.vue`,
   `*.astro`, or paths under `components/`, `pages/`, `views/`, `layouts/`,
   `templates/`. Also treat an existing `ux-prototype.html` as UI surface.

   If NO UI surface → write `review:` with `verdict: skipped`,
   `guidance: "No UI changes — skipped."`, log once, return completed.

3. Read `ux-reviewer/reference/feedback-format.md` and the feedback template.
   Critique the target UI (modified files + prototype) for the four
   dimensions: accessibility, hierarchy, consistency, friction.

4. Score each dimension 1–10 with the same severity caps as design-review
   (critical → critical_cap, important → important_cap). Overall = min of
   dimensions.

5. If overall >= target_score and no critical findings: PASS.
   Write/replace `review:` on `ux-artifacts.yaml` (Verdict pass, scores,
   Findings "None — pass.", Guidance "Ship."). Return completed.

6. If overall < target_score or critical findings:
   a. Parse findings into fix tasks; apply autonomously fixable issues only
   (CSS, accessible names, obvious consistency) scoped to those findings.
   b. Run the repo's verify commands (from CLAUDE.md / AGENTS.md / README /
   manifests) after edits.
   c. Increment an in-memory retry counter; re-critique.
   d. If retries >= max_retries: write `review:` with `verdict: needs_work`,
   full findings + guidance for a human, return `status: failed`.
   e. Otherwise continue the fix loop.

7. On the way out after a successful fix loop: write `review:` with
   `verdict: pass`, final scores, and guidance noting fixes applied.
   Commit UX improvements:

   ```
   style(<change-id>): UX critique improvements (score: N/10)
   ```

8. Keep `ux-artifacts.yaml` prototype metadata (file, description, options)
   intact — only replace the `review:` mapping.

### Rules (constraints on how)

- Only runs when the phase includes UI-facing changes (else skipped).
- Do not invent a separate `ux-critique.md` / `critique_result` output.
- Target score is 8 (step-owned).

## Verify

- `ux-artifacts.yaml` has a complete `review:` block per the feedback format
- Verdict matches COMPLETION status (`pass`/`skipped`→completed,
  `needs_work`→failed)
- Discoverable verify commands pass after any applied fixes

## Return COMPLETION

On pass or skip:

```
COMPLETION:
  status: completed
  review_score:
    overall: <N or null if skipped>
  artifacts: [ux-artifacts.yaml]
  outputs:
    reason: "ux critique pass (or skipped) — overall <N or null>"
```

On needs_work after max retries:

```
COMPLETION:
  status: failed
  review_score:
    overall: <N>
  artifacts: [ux-artifacts.yaml]
  outputs:
    reason: "ux critique needs_work after max retries — overall <N>"
```

COMPLETION status is only `completed` or `failed`.
