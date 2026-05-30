# Run UX Critique

**Intent:** Run UX critique on UI changes and iterate until quality_bar score is met.

## Inputs

None named. (Reads modified files in the phase and `quality_bar` from `spec/project.yaml`.)

## Outputs

- `critique_score`
- `critique_skipped`
- `critique_retries`

## Instructions

1. Read quality thresholds from project.yaml:
   - target_score = quality_bar.min_phase_review_score
   - max_retries = quality_bar.max_retry_rounds

2. Check if any files modified in this phase touch UI:
   Match: *.html, *.css, *.scss, *.tsx, *.jsx, *.svelte, *.vue, *.astro,
   or files in components/, pages/, views/, layouts/, templates/.

   If NO UI files modified → skip. Log: "[critique] No UI changes — skipping"

3. Spawn ux-reviewer agent in background with:
   - Target files (list of modified UI files)
   - vision.target_users from project.yaml
   - quality_bar.scoring thresholds
   - ux-prototype.html reference if it exists in change dir

4. When agent returns, read SCORE and STATUS from output.

5. If score >= target_score: PASS. Record critique_score in state.yaml.

6. If score < target_score:
   a. Parse PRIORITY_ISSUES from agent output into fix tasks.
   b. Apply fixes scoped to critique findings only.
   c. Run verify_commands to confirm nothing broke.
   d. Increment retry counter in state.yaml.
   e. Re-spawn ux-reviewer agent on updated files.
   f. If retries >= max_retries: STOP and escalate to user.

7. Commit UX improvements:
   ```
   style(<change-id>): UX critique improvements (score: N/10)
   ```

### Rules (constraints on how)

- Only runs when the phase includes UI-facing changes.
- Target score is quality_bar.min_phase_review_score from project.yaml.
- Retry with fixes until target score is met or max_retry_rounds exhausted.
- Spawn ux-reviewer agent directly with context — do NOT invoke /critique skill.

## Verify

- If UI files modified: critique_score >= quality_bar.min_phase_review_score
- If no UI files: step skipped (logged)
- All verify_commands pass after fixes

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
