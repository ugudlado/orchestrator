---
feature-id: orc-91
linear-ticket: ORC-91
---

# Design: workflow-learner auto-syncs retro.md issues into backlog with dedup + recurrence

## Context

The `workflow-learner` agent runs at the end of every `/learn` invocation. Today it routes findings into step contracts and `spec/project.yaml` learnings but stops short of the user-facing backlog. Retro entries that describe real, file-able issues — with `fix_direction` and sometimes explicit `backlog_entry` slugs — never become tickets unless someone files them by hand. When the same issue recurs across N features we either get N near-duplicate tickets or zero. ORC-91 closes that loop with a deterministic sync that runs on every `/learn` invocation.

The agent prompt itself is markdown and cannot host failing tests. The deterministic work — parsing retro.md, resolving dedup keys, searching the backlog, classifying matches, deciding `create | append | regression`, and calling `backlog` — is extracted into a shell helper. The agent's only change is a thin "after §4, run this helper" instruction.

## Goals / Non-Goals

### Goals

- Make backlog sync a non-skippable step of every `/learn` run (no flag, no opt-in).
- Dedup against ALL backlog statuses (To Do, In Progress, Done) before filing.
- Append `Recurred in feature <ID> on <DATE>` notes to open matches instead of duplicating.
- File a HIGH-priority `Regression:` ticket when a `Done` ticket recurs, linking back to the original ID; also annotate the closed ticket.
- Emit a per-issue audit line (`matched | created | bumped | regression`) so a human can replay the decisions.
- Exit cleanly (no error to `/learn`) when retro.md is missing, prose-only, or when ticketing != backlog.

### Non-Goals

- Retroactive sweep of archived retros (one-time backfill is a follow-up).
- Linear backend support (`ticketing: linear` skips with a single log line).
- Heuristic extraction from prose-only retros (warn-and-skip; format fix lives elsewhere).
- Structured `recurrence_count` field on ticket frontmatter (counter is derived from notes).
- Triggering ideator re-prioritization automatically (ideator reads notes on its own next run).
- Modifying retro.md generation or the `complete-feature` / `run-learn-cycle` steps.

## Approaches Considered

### Approach 1: Embed sync logic in agent prompt (markdown only)

Add ~80 lines of prose to `agents/workflow-learner.md` describing the search/match/create flow. Agent shells out to `backlog` directly.

- Pros: zero new files; everything in one place; matches the existing pattern of §1–§5.
- Cons: cannot be tested — `flags.tdd_required=true` would force tests against a prompt; non-deterministic LLM judgement on every classification (dedup key normalization, tie-break, priority mapping) drifts run-to-run; impossible to dry-run without invoking the full agent.

### Approach 2: Shell helper invoked by the agent (SELECTED)

New `config/scripts/inline/backlog-sync-from-retro.sh` does the deterministic work; `workflow-learner.md` gains a 3-line §4b that calls the helper with the retro path and feature id, then incorporates its stdout into the §5 report.

- Pros: deterministic; testable with bats (UC-1/2/3/E1/E4/E6 all become smoke tests); reusable in dry-run; the agent stays focused on classification.
- Cons: introduces a second artifact (a script alongside the prompt edit); shell parsing of structured Markdown is mildly fragile but the retro block format is small and well-defined.

### Approach 3: Python helper

Same as Approach 2 but in Python with `pyyaml` and richer parsing.

- Pros: cleaner parsing; easier to add structured logging.
- Cons: heavier dependency footprint; inconsistent with the existing inline-script convention (bash); no parsing complexity here that bash + grep/awk can't handle cleanly.

### Selected Approach

**Approach 2.** TDD + agent-prompt-only changes are incompatible; pulling deterministic work into a shell helper resolves that and matches the existing `config/scripts/inline/` pattern (`capture-test-baseline.sh`, `validate-tasks-yaml.sh`, etc.). Approach 1 was ruled out by `tdd_required`; Approach 3 was ruled out by the inline-script convention.

## High-Level Design

### Architecture Overview

```
/learn
  └─ workflow-learner agent
      ├─ §1 Find Context
      ├─ §2 Gather Inputs
      ├─ §3 Run Workflow Evaluation
      ├─ §4 Route Findings  (existing)
      ├─ §4b Backlog Sync   (NEW — calls helper)
      │     └─ backlog-sync-from-retro.sh <retro_path> <feature_id>
      │            ├─ early-exit if ticketing != backlog OR retro missing
      │            ├─ parse ## ISSUE-N blocks → list of issues
      │            ├─ for each issue: resolve dedup_key, search backlog, decide, act
      │            └─ emit per-issue audit line + summary counts to stdout
      └─ §5 Report  (existing — incorporates sync summary)
```

### Key Abstractions

- **Issue record**: `{id, title, category, severity, detail, fix_direction, backlog_entry?}` parsed from one `## ISSUE-N — <title>` block.
- **Dedup key**: explicit `backlog_entry:` slug if present, else `normalize(category) + "|" + normalize(first 8 words of fix_direction)`. `normalize(s) = lowercase(s)` with non-alphanumeric runs collapsed to `-`.
- **Match decision**: one of `none | open <id> | done <id>`. Determines whether to create, append, or regression-file.
- **Sync ledger**: in-memory map of `dedup_key → newly_created_id` so that two retro issues with the same key in the same run collapse to one ticket (the second becomes a recurrence on the just-created one).

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs |
|---|---|---|---|
| `config/scripts/inline/backlog-sync-from-retro.sh` | Parse retro, search backlog, file/append/regression | `$1=retro_path $2=feature_id`; reads `spec/project.yaml` | stdout: per-issue audit lines + summary; exit 0 even on backlog CLI failure |
| `agents/workflow-learner.md` § 4b | Invoke helper, capture its stdout, include summary in §5 | helper stdout | log + report line |
| `tests/inline/test_backlog_sync_from_retro.bats` | Smoke tests for UC-1, UC-2, UC-3, UC-E1, UC-E4, UC-E6 (D-9) | fixture retros + a temp backlog | bats pass/fail |

### Data Flow

1. Helper receives `retro_path` and `feature_id`.
2. Reads `spec/project.yaml` for `ticketing:`. If not `backlog` → emit `[learn] Backlog sync: skipped — ticketing=<value>`, exit 0.
3. If `retro_path` does not exist or has zero `## ISSUE-` lines → emit `[learn] Backlog sync: no retro issues found` (D-5: prose-only is the same warn-and-skip), exit 0.
4. For each `## ISSUE-N` block: extract fields via line-prefix matches (`- **category**:`, etc.).
5. For each issue:
   a. Resolve dedup_key (D-2 / D-6).
   b. Check in-memory ledger (D-7); if already filed this run → append recurrence note to the ledger's ticket and continue.
   c. `backlog search "<dedup_key>" --plain` → parse results (one row per match).
   d. Tie-break per D-3 → produces a single match or none.
   e. If match status ∈ {To Do, In Progress}: `backlog task edit <id> --append-notes $'Recurred in feature <FEATURE_ID> on <DATE>\n- detail: <issue.detail>'`. Decision = `bumped <id>`.
   f. If match status == Done: append same note to closed ticket AND `backlog task create "Regression: <orig-title> (<orig-id>) recurred after close" --priority high --label recurrence-1,from-retro,regression --ac "<issue.fix_direction>" -d "Original ticket: <orig-id>. Issue surfaced in feature <FEATURE_ID> on <DATE>. Detail: <issue.detail>"`. Decision = `regression <new-id>`.
   g. If no match: `backlog task create "<issue.title>" --priority <severity→priority per D-4> --label recurrence-1,from-retro --ac "<issue.fix_direction>" -d "Surfaced in feature <FEATURE_ID> on <DATE>. Detail: <issue.detail>"`. Store new id in ledger. Decision = `created <new-id>`.
   h. Emit `[learn] sync: ISSUE-N → <decision>`.
6. Emit summary `[learn] Backlog sync: X created, Y bumped, Z regressions`.

### State Management

- No persisted state. Sync is purely a function of the retro file + current backlog snapshot.
- The in-memory ledger lives only for the duration of one helper invocation.

### Error Handling

- Helper is fail-soft (D-10). Every `backlog` invocation is wrapped to capture exit code; on non-zero, emit `[learn] sync: ISSUE-N → ERROR (<backlog stderr first line>)` and continue with the next issue. Final exit is always 0.
- Missing `spec/project.yaml` → treat as `ticketing` unknown → skip with the same "skipped" log line.
- Malformed issue block (missing `fix_direction` or `category`) → log `[learn] sync: ISSUE-N → skipped (missing required field <name>)` and continue.

## Constraints

- Must run from `$REPO_ROOT` (per the tasks.yaml contract); helper resolves paths relative to that cwd.
- `backlog` CLI must run from repo root (already established convention).
- Helper must not write to `state.yaml` or any artifact the dispatcher tracks.
- No new shell-script dependencies beyond `bash`, `grep`, `sed`, `awk`, `date` — already required by sibling inline scripts.

## Trade-offs

- **Bash parsing over Python**: simpler integration with existing inline-script conventions; loses richer error reporting. Acceptable because the retro block format is small and well-defined.
- **Fail-soft errors**: a backlog CLI bug could silently drop ticket creation. Trade-off accepted because `/learn` must not fail on a non-essential sub-step; audit lines give the human a way to spot drops.
- **Stdout-only audit log (D-8)**: no replay/debug persistence. Acceptable for v1; persistence is a 1-line follow-up if it becomes painful.
- **No Linear support**: every other repo using this `workflow-learner` will get a single-line skip notice. Trade-off accepted; cross-backend abstraction is its own ticket.

## Acceptance Criteria

- AC-1: A retro with a previously-unseen issue results in exactly one new backlog ticket whose labels include `recurrence-1` and `from-retro` and whose ACs include the issue's `fix_direction`. [traces: UC-1]
- AC-2: A retro whose dedup_key matches an existing `To Do` or `In Progress` ticket appends a `Recurred in feature <FEATURE_ID> on <DATE>` note to that ticket and creates **no** new ticket. [traces: UC-2]
- AC-3: A retro whose dedup_key matches an existing `Done` ticket (a) appends a recurrence note to the closed ticket AND (b) creates a new HIGH-priority ticket whose title starts with `Regression:` and references the original ticket ID. [traces: UC-3]
- AC-4: The helper emits one audit line per issue in the form `[learn] sync: ISSUE-N → <decision>` plus a summary `[learn] Backlog sync: X created, Y bumped, Z regressions`, captured by the agent and surfaced in §5 of the `/learn` report. [traces: UC-4]
- AC-5: An explicit `backlog_entry:` slug in a retro entry is used verbatim as the `backlog search` query (no normalization), and matches an existing ticket whose title contains that slug. [traces: UC-5]
- AC-6: When `spec/project.yaml.ticketing != "backlog"`, the helper exits 0 immediately with `[learn] Backlog sync: skipped — ticketing=<value>` and creates no tickets. [traces: UC-E6]
- AC-7: When retro.md is missing or contains zero `## ISSUE-N` blocks (including prose-only retros), the helper exits 0 with `[learn] Backlog sync: no retro issues found` and creates no tickets. [traces: UC-E1, UC-E3]
- AC-8: Two retro issues in the same run that resolve to the same dedup_key produce exactly one new ticket; the second occurrence is appended as a recurrence note to the just-created ticket. [traces: UC-E4]
- AC-9: When a `backlog` CLI invocation exits non-zero, the helper logs `[learn] sync: ISSUE-N → ERROR (<reason>)`, continues processing remaining issues, and exits 0 overall. [traces: UC-E5]
- AC-10: `agents/workflow-learner.md` gains a §4b "Backlog sync" branch that runs unconditionally after §4 and before §5, invokes the helper with the active retro path and feature id, and incorporates the helper's summary into the §5 report. [traces: UC-1, UC-2, UC-3, UC-4]

## Decisions

- **D-1 → Shell helper over inline prose**: keeps logic deterministic and testable under `tdd_required`. Consequence: a new script file plus a small agent edit, rather than a single agent edit.
- **D-2 → Lowercased category + first-8-words-of-fix-direction**: deterministic, stable across features. Consequence: very long `fix_direction` strings get truncated for dedup purposes but the full text still lands in the ticket description.
- **D-3 → Exact-substring-then-status-priority tie-break**: predictable behavior under multi-match. Consequence: if two open tickets both exactly match, the `In Progress` one wins, then the oldest.
- **D-4 → Severity → priority mapping is fixed**: avoids per-call inference. Consequence: edge-case severities collapse to `medium`; rare but acceptable.
- **D-5 → Warn-and-skip on prose-only retros**: avoids brittle heuristic parsing. Consequence: prose-only retros never sync until standardized; tracked separately as a structural-rule learning.
- **D-7 → File-first self-dedup**: keeps the new ticket as the canonical one for the rest of the run. Consequence: ordering of issues in retro.md affects which one becomes the title — acceptable since both have the same dedup_key.
- **D-9 → Strict `ticketing == backlog` gate**: feature is single-backend by design. Consequence: Linear repos see a one-line skip; cross-backend support is a separate ticket.
- **D-10 → Fail-soft on every backlog CLI call**: `/learn` must never fail on this sub-step. Consequence: silent drops are possible; audit lines are the human safety net.

## Open Questions

- None that block implementation. The remaining unknowns (persistence of audit log, Linear support, retroactive sweep) are explicit follow-ups recorded as Non-Goals.
