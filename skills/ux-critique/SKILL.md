---
name: ux-critique
description: "UX design critique with staff-level evaluation. Use when reviewing UI/UX, critiquing designs, or before shipping a UI feature."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#ux-reviewer
---

# Run UX Critique

**Intent:** Run UX critique on UI changes and iterate until the target score is met.

## Inputs

None named. (Reads modified files in the phase.)

## Outputs

- `critique_score`
- `critique_skipped`
- `critique_retries`

## Instructions

1. Quality thresholds are step-owned (vendor this pack to change them):
   - target_score = 8
   - max_retries = 3

2. Check if any files modified in this phase touch UI:
   Match: _.html, _.css, _.scss, _.tsx, _.jsx, _.svelte, _.vue, _.astro,
   or files in components/, pages/, views/, layouts/, templates/.

   If NO UI files modified → skip. Log: "[critique] No UI changes — skipping"

3. Perform UX critique directly on:
   - Target files (list of modified UI files)
   - target users, as stated in the repo's own docs (README / CLAUDE.md), if any
   - the step-owned scoring thresholds (critical_cap 5, important_cap 7, green_base 9)
   - ux-prototype.html reference if it exists in change dir

4. Read SCORE and STATUS from your critique output.

5. If score >= target_score: PASS. Record critique_score in state.yaml.

6. If score < target_score:
   a. Parse PRIORITY_ISSUES from agent output into fix tasks.
   b. Apply fixes scoped to critique findings only.
   c. Run the repo's verify commands (as discovered from CLAUDE.md / AGENTS.md /
   README / manifest conventions) to confirm nothing broke.
   d. Increment retry counter in state.yaml.
   e. Re-run critique on updated files.
   f. If retries >= max_retries: STOP and escalate to user.

7. Commit UX improvements:
   ```
   style(<change-id>): UX critique improvements (score: N/10)
   ```

### Rules (constraints on how)

- Only runs when the phase includes UI-facing changes.
- Target score is 8 (step-owned).
- Retry with fixes until target score is met or max_retry_rounds exhausted.
- Perform critique using this skill procedure.

## Verify

- If UI files modified: critique_score >= target_score (8)
- If no UI files: step skipped (logged)
- All discovered verify commands pass after fixes

## Return COMPLETION

After verify passes (or on skip), return:

```
COMPLETION:
  status: completed
  outputs:
    critique_score: <N or null if skipped>
    critique_skipped: <true if no UI files, false otherwise>
    critique_retries: <N>
```
