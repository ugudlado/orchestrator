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
