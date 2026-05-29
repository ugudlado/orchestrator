---
feature-id: orc-90
linear-ticket: ORC-90
---

# Discovery Brief: Render retro.md in complete-workflow final report

## Feature Summary

When a workflow completes, the driver prints a one-line "feature complete" cost rollup to stderr (via `_emit_feature_rollup` in `scripts/run-workflow.sh`, which shells out to `scripts/cost-report.sh --tail`). Meanwhile, `retro.md` — populated live by `config/scripts/inline/append-retro.sh` whenever a step's done-payload carries `workflow_issues: [...]` — is silently written to `spec/changes/<change_id>/retro.md` and archived. An interactive user gets zero signal that anything went sideways unless they remember to `cat retro.md` post-run. This ticket adds a small markdown "Issues this run" section that prints right after the cost line, summarising retro.md as a table (Severity / Category / Detail / Fix direction). Empty or missing retro.md ⇒ section omitted silently. Under `--auto`, render-and-proceed (no pause, no prompt).

## Personas & Actors

- **Interactive developer** running `orchestrator complete <ticket>` (or `orchestrator run`) and watching stderr.
- **Autopilot driver** running under `--auto`; should see the same output without any prompt.
- **Inline driver** (record.py + append-retro.sh) — already the producer of retro.md; unchanged.
- **Workflow-improver agent** — downstream reader of retro.md during `run-learn-cycle`; unaffected.

## Use Cases

### Happy Path

UC-1: Issues surfaced this run — Interactive developer wants to see a compact "Issues this run" table beneath the cost line so that they can decide whether to investigate retro.md or run `/learn` without first having to know retro.md exists.
UC-2: Clean run — Interactive developer wants no extra output when no workflow_issues were recorded so that successful runs stay quiet and signal-rich (no "no issues" noise).
UC-3: Autopilot loop — Autopilot driver wants the same retro table emitted without any blocking prompt so that the iter completes and the next iter starts.

### Error & Edge Cases

UC-E1: retro.md missing — When no step emitted workflow_issues, retro.md does not exist; the section must be omitted silently (no header, no "(empty)" placeholder, no error).
UC-E2: retro.md exists but contains only the template banner (no ISSUE-N blocks) — section must be omitted silently. Detection: count of `^## ISSUE-` matches is zero.
UC-E3: retro.md exists but archive already moved it — by the time `_emit_feature_rollup` runs, `complete-workflow` has already `mv`'d `spec/changes/<cid>/` → `spec/changes/archive/<cid>/`. The renderer must resolve retro.md from the archived path, not the active path, or it will silently render nothing on every successful run. (See Key Decisions / OQ-1.)
UC-E4: Malformed issue block (missing severity or category) — render with `—` placeholder for the missing field; never crash the report.
UC-E5: Very long detail/fix_direction text — truncate to a single line in the table (e.g. first ~120 chars + "…"); full text remains in retro.md.

## Scope

### In Scope

- A renderer that reads `retro.md`, parses `## ISSUE-N — title` blocks plus the bullet metadata (category, severity, detail, fix_direction), and emits a markdown table with columns `Severity | Category | Detail | Fix direction`.
- Wiring the renderer into the same code path that emits the cost line (`_emit_feature_rollup` in `scripts/run-workflow.sh`), so cost + issues appear together at the end of every workflow run.
- Silent omission when retro.md is missing, empty, or contains no `## ISSUE-` blocks.
- Non-blocking output: no prompt, no pause, no `--auto` branch — single code path for both modes.
- Resolution logic that finds retro.md at the archived location (`spec/changes/archive/<cid>/retro.md` inside the worktree when `worktree=true`, else in repo_root) since archive happens before rollup.
- One bats or shell test that asserts: (a) populated retro.md ⇒ table rendered, (b) missing retro.md ⇒ no output, (c) empty retro.md ⇒ no output.

### Out of Scope

- Changing the *content* or *format* of retro.md itself — that's owned by `append-retro.sh` (already shipped via ORC-31 family). Rationale: this ticket is "pure visibility win, independent of backlog sync" per the description.
- Live emission of workflow_issues during the run — already implemented; this ticket only reads what's there.
- Backlog sync / Linear ticket creation from issues — separate follow-up ticket (workflow-learner backlog sync).
- Filtering or grouping issues by severity / category — render in document order; sorting is downstream concern.
- Any change to `complete-workflow`'s archive behavior. Rationale: archive is unconditional and load-bearing; the renderer adapts to it.
- Interactive "press any key" or pager behavior. Rationale: AC #3 demands non-blocking under --auto, and forking interactive/non-interactive paths is over-engineering.
- A new step in the workflow plan. Rationale: rollup is already a post-archive driver concern (lives in run-workflow.sh, not in a step contract); adding a step would re-tangle archive with rendering.

## UI Direction

N/A — terminal/stderr output only. The output is plain markdown printed to stderr alongside the existing "feature complete: …" line. Format suggestion (table rendered via plain markdown pipes, the same convention `cost-report.sh` already emits):

```
[2026-05-26T18:30:12Z] feature complete: orc-90: $0.47 · 3m · 28 steps · 1.2x median

## Issues this run (3)
| Severity            | Category              | Detail                                       | Fix direction                       |
|---------------------|-----------------------|----------------------------------------------|-------------------------------------|
| blocker             | driver-bug            | Stale active state.yaml resumed wrong slug…  | orchestrator doctor preflight check |
| workaround-applied  | driver-contract-amb…  | Dispatcher signals complete_workflow mid-pl… | introduce advance_phase action      |
| cosmetic            | telemetry-drift       | read-sub-state-metrics.sh uses outdated pa…  | update to .state/<slug> layout      |
```

## Key Decisions

- **Render location: `_emit_feature_rollup` in `scripts/run-workflow.sh`, not a new step.** Rationale: archive already happens before rollup (state.yaml is gone by the time we're here), and the cost line is already emitted from this function — co-locating keeps cost + issues physically adjacent without a new step contract or workflow_plan edit. AC #4 ("alongside the existing cost summary") essentially names this location.
- **Source: archived `retro.md` only.** When `_emit_feature_rollup` runs, `complete-workflow → archive-completed-change` has already moved `spec/changes/<cid>/` → `spec/changes/archive/<cid>/`. Resolve retro.md from the archive (worktree-aware), mirroring how `cost-report.sh` is already invoked. Avoids a TOCTOU race and matches what the user could `cat` afterward.
- **Renderer language: shell + python3 heredoc (parser).** The producer (`append-retro.sh`) already uses this pattern; parsing the H2 + bullet structure cleanly is a one-screen python script. A separate `scripts/render-retro.sh` keeps it testable in isolation and reusable.
- **Truncation: per-cell, ~120 chars with `…`.** Keeps the table readable at typical terminal widths without forcing the user to scroll. Full text remains in archived retro.md.
- **Silence on empty:** check both `[ -f retro.md ]` AND `grep -cE '^## ISSUE-' retro.md > 0`. The producer writes a header banner even when zero issues land, so file existence alone is not enough.
- **Selected design direction (design-and-draft-artifacts, auto-select):** **Approach 2 — standalone `scripts/render-retro.sh` invoked by `_emit_feature_rollup`**. Complexity S (2). Heuristic: lowest numeric complexity among the three approaches considered (S=2 vs M=3 for the inline-bash and Python-module alternatives). Module-reuse tiebreak not exercised. Full approach analysis lives in `design.md` § Approaches Considered.

## Open Questions

- OQ-1: Should the renderer also fire on workflow *failure* (exit ≠ 1 from the driver loop), so issues recorded during a partial run are still surfaced? Today `_emit_feature_rollup` is only called on the success path (exit 1 = workflow-complete in the driver's exit-code convention). Argument for yes: a failed run is exactly when issues matter most. Argument for no: scope creep — failure paths don't archive, so retro.md is at the *active* path, requiring a second resolution branch. Recommendation: deliver success-path-only in v1, file follow-up if needed.
- OQ-2: Column order — does the ticket's `Severity | Category | Detail | Fix direction` (AC #1) match user preference, or should detail come first as the most-scanned column? AC #1 names the exact order; design-and-draft-artifacts can confirm with mockup but default is to honor AC verbatim.
- OQ-3: Should `## Issues this run` use the count `(N)` suffix (as in the mockup), and should it be styled as a heading or a plain bold line? Minor; design step picks.
- OQ-4: Where does `orchestrator complete` (the wrapper at `scripts/orchestrator-complete.sh`) sit in this — does it also need a rollup call, or is delegating to `run-workflow.sh` sufficient? Reading shows orchestrator-complete.sh execs run-workflow.sh and inherits its rollup. No change needed there — confirm in design.
