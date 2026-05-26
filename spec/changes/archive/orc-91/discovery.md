---
feature-id: orc-91
linear-ticket: ORC-91
---

# Discovery Brief: workflow-learner auto-syncs retro.md issues into backlog with dedup + recurrence

## Feature Summary

Today `/learn` (the `workflow-learner` agent) routes findings to step contracts, agent prompts, and `spec/project.yaml` learnings, but it stops short of the user-facing backlog. Retro entries that describe real, file-able issues (with explicit `fix_direction` and sometimes `backlog_entry` slugs) are left dangling — when the same issue recurs across multiple features, we either get N near-duplicate tickets filed by hand or zero tickets at all. ORC-91 closes that loop by extending the agent with a deterministic backlog-sync branch that runs on every `/learn` invocation: it reads the just-finished feature's `retro.md`, searches the backlog for matches, and creates / appends / regression-files tickets per the rules in the ticket spec.

## Personas & Actors

- **workflow-learner agent** (`agents/workflow-learner.md`) — owner of the new routing branch; runs at end of `complete` phase.
- **Backlog.md CLI** (`backlog`) — sole interface for ticket reads/writes; the agent shells out via `Bash`.
- **retro.md** — produced earlier in the `complete` phase (or backfilled, per the ISSUE-22 retro precedent) as structured Markdown.
- **Ideator** — downstream consumer that parses the "Recurred in feature" notes to compute `effective_score` (per ticket description) when prioritizing.
- **Engineer reading the audit log** — needs to see what the sync did per issue (matched / created / bumped / regression).

## Use Cases

### Happy Path

UC-1: First-time issue — workflow-learner reads a retro issue with no matching open or closed ticket, runs `backlog search "<dedup_key>"`, finds nothing, then runs `backlog task create` with labels `recurrence-1,from-retro`, the issue's `fix_direction` as an AC, and the retro detail in the description.

UC-2: Recurrence on an open ticket — agent finds a matching `To Do` or `In Progress` ticket via `backlog search`. It appends `"Recurred in feature <FEATURE_ID> on <DATE>\n- detail: <issue.detail>"` via `backlog task edit <id> --append-notes` and creates no new ticket.

UC-3: Regression after close — agent finds a matching `Done` ticket. It appends the recurrence note to the closed ticket AND files a new HIGH-priority ticket titled `Regression: <original-title> (<original-id>) recurred after close`, linking back to the original ticket ID in the description.

UC-4: Audit / dry-run — engineer wants to verify what the last `/learn` did. The agent's output (or a sync log) shows per-issue rows: `ISSUE-N → matched <id> (status) | created <new-id> | bumped <id> recurrence to K | regression filed as <new-id>`.

UC-5: Explicit dedup key from retro — retro entry includes `backlog_entry: spec/changes/backlog/<slug>/` or a `backlog_entry:` slug. Agent uses that slug verbatim as the dedup key (skips the normalized-category fallback).

### Error & Edge Cases

UC-E1: retro.md missing or empty — agent logs `[learn] Backlog sync: no retro issues found` and proceeds without error; this is a normal state for clean features.

UC-E2: `backlog search` returns multiple matches — agent must pick deterministically; spec needs to define the tie-breaker (newest? same-status preferred? exact-slug exact match wins over fuzzy?).

UC-E3: prose-only retro (no `## ISSUE-N` blocks, e.g. `done-verb-level-aware-writes/retro.md`) — agent has no structured fields to read; spec must decide whether to skip silently, attempt heuristic extraction, or warn.

UC-E4: Two retro issues with the same dedup_key in the same run — agent must dedup against itself (file once, append the second as a recurrence on the just-created ticket).

UC-E5: `backlog` CLI exits non-zero (e.g. workspace not initialized, search index stale) — agent should not crash `/learn`; the sync sub-step must be fail-soft and log the underlying error.

UC-E6: Backlog config is `linear` (other repos) instead of `backlog` — `spec/project.yaml.ticketing` field drives this; spec must clarify whether ORC-91 also targets Linear, or strictly the `backlog: backlog` case.

## Scope

### In Scope

- New routing branch inside `agents/workflow-learner.md` that runs after §4 (Route Findings), before §5 (Report), on every invocation — no flag, no opt-in.
- Parsing of structured retro.md entries (`## ISSUE-N — <title>` blocks with `category`, `severity`, `detail`, `fix_direction`, and optional `backlog_entry`).
- Dedup-key resolution: explicit `backlog_entry:` slug if present; else normalized `<category> + <fix_direction>` (need to define normalization rules in spec).
- `backlog search "<dedup_key>" --plain` lookups inspecting **all statuses** (To Do, In Progress, Done).
- Append-only recurrence on open tickets via `backlog task edit <id> --append-notes`.
- Regression file-and-link on closed tickets: append note on the closed ticket AND `backlog task create` a new HIGH-priority ticket whose title references the original ID.
- New-ticket creation with labels `recurrence-1,from-retro`, priority derived from the retro's `severity`, and the issue's `fix_direction` as an AC.
- Per-issue audit log line emitted by the agent showing `matched|created|bumped|regression` decisions.
- §5 report line summarizing sync counts (e.g. `Backlog sync: 1 created, 2 bumped, 0 regressions`).

### Out of Scope

- Retroactive sweep of all archived retros — only the just-finished feature's retro is processed. (Rationale: keeps the sync incremental and predictable. A one-time backfill can be a follow-up ticket.)
- Linear backend support — `project.yaml.ticketing: linear` paths are deferred. (Rationale: this repo uses `backlog`; cross-backend abstraction belongs in a separate ticket.)
- Heuristic extraction from prose-only retros — only structured `## ISSUE-N` blocks are processed. (Rationale: prose parsing is brittle; the fix is to standardize the retro template, not the consumer.)
- Adding a structured `recurrence_count` field to ticket frontmatter — spec confirms the counter is **derived** from `Recurred in feature` lines in implementation notes, not stored.
- Triggering ideator re-prioritization automatically — the sync writes labels and notes; ideator reads them on its next run.
- Changing the retro.md format or generation step (that's owned by `complete-feature` / `run-learn-cycle`).

## UI Direction

N/A — no UI components. The agent is a CLI/headless step; output is the existing `[learn]` log lines plus new audit lines.

## Key Decisions

- **D-1 (Approach)**: Extract sync logic into a shell helper (`config/scripts/inline/backlog-sync-from-retro.sh`) invoked from `agents/workflow-learner.md`. Keeps the agent prompt thin, makes deterministic logic testable via bats — satisfies `flags.tdd_required=true` without writing tests against markdown.
- **D-2 (Normalization, OQ-1)**: Fallback dedup key = `lowercase(category) + "|" + lowercase(first 8 words of fix_direction)` with non-alphanumeric runs collapsed to single `-`. Deterministic, stable across features.
- **D-3 (Tie-break, OQ-2)**: Exact dedup-key substring in title wins over fuzzy; on further ties prefer status priority `In Progress > To Do > Done`; on further ties, lowest task ID (oldest).
- **D-4 (Severity → priority, OQ-3)**: `blocker → high`, `regression-trigger → high`, `workaround-applied → medium`, `cosmetic → low`, any other / missing → `medium`.
- **D-5 (Prose-only retros, OQ-4)**: Warn-and-skip. Helper emits `[learn] Backlog sync: skipped — retro.md has no ## ISSUE-N blocks` and exits 0. Standardizing the retro template is a separate concern.
- **D-6 (Legacy slugs, OQ-5)**: `backlog_entry:` slug is used verbatim as the `backlog search` query. If it matches an existing task title substring, treat as a match. If not, fall through to normalized key. No path-to-id mapping.
- **D-7 (Self-dedup, OQ-E4/OQ-6)**: File first occurrence; second occurrence with the same dedup_key is appended as a recurrence to the just-created ticket within the same run.
- **D-8 (Audit log, OQ-7)**: Stdout only for v1 (one `[learn] sync: ISSUE-N → <decision>` line per issue). Persistence under state.yaml is a follow-up.
- **D-9 (Ticketing backend, OQ-E6/OQ-8)**: Helper reads `spec/project.yaml` `ticketing:` field. If not `backlog`, exit 0 with `[learn] Backlog sync: skipped — ticketing=<value>`. Linear support is explicitly out of scope.
- **D-10 (Failure mode, UC-E5)**: Helper is fail-soft. Any `backlog` CLI non-zero exit is logged but does not propagate failure to `/learn`. Sync is best-effort.

## Open Questions

- OQ-1: Normalization rules for the fallback dedup_key. Lowercase + collapse whitespace? Strip punctuation? Keep `category` + first N words of `fix_direction`? Need a deterministic recipe so the same issue across features yields the same key.
- OQ-2: Tie-breaking when `backlog search` returns multiple matches: prefer exact dedup-key substring match over fuzzy, then prefer status priority `In Progress > To Do > Done`, then newest? Spec must lock this down.
- OQ-3: Priority mapping from retro `severity` to backlog `--priority`: `blocker → high`, `workaround-applied → medium`, `cosmetic → low`, other → medium? Confirm with the existing retro vocabulary.
- OQ-4: Prose-only retros (e.g. `done-verb-level-aware-writes/retro.md` uses `### 1.` H3 headings instead of `## ISSUE-N` blocks). Should the agent warn and skip, or should `/learn` fail loudly so retros get standardized? Recommend warn-and-skip + emit a structural-rule learning so the next retro is well-formed.
- OQ-5: How is "matching" defined when an explicit `backlog_entry:` slug points to a folder under `spec/changes/backlog/` (legacy slugs) but the backlog is now CLI-managed and slugs may not exist as tickets? Need a mapping: try slug as task title substring? Map slug → existing ticket id?
- OQ-6: Self-dedup within a single retro (UC-E4) — when two retro entries collapse to the same dedup_key, do we file one and bump it, or file two? Recommend: file the first, append the second as a recurrence on the just-created ticket.
- OQ-7: Where does the per-issue audit log go — stdout only, or also persisted under `state.yaml` (e.g. as a `backlog_sync_log` learning) for replay/debug? Stdout is the minimum; persistence is nice-to-have.
- OQ-8: Should the sync run when `state.flags.ticketing != "backlog"` (e.g. repos that switched to Linear)? Default behavior: skip with a single log line, leave the routing branch a no-op.
