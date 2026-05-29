---
feature-id: orc-90
linear-ticket: ORC-90
---

# Design: Render retro.md in complete-workflow final report

## Context

When a workflow completes, `_emit_feature_rollup` in `scripts/run-workflow.sh:349` prints a single cost line to stderr by shelling out to `scripts/cost-report.sh --tail`. In parallel, `config/scripts/inline/append-retro.sh` writes structured workflow_issues to `spec/changes/<change_id>/retro.md` as they are recorded by `record.py`. `complete-workflow` then archives `spec/changes/<change_id>/` to `spec/changes/archive/<change_id>/` before the rollup line is emitted. An interactive user gets no signal that issues were captured unless they remember to `cat` the archived retro.md. Recent archive naming (memory ID 21736 / observed `2026-05-26-orc-86`, `orc-85` co-existing) shows date-prefix removal is mid-rollout, so resolution must tolerate both layouts.

## Goals / Non-Goals

### Goals

- Surface workflow_issues from `retro.md` as a markdown table beside the existing cost line, with columns `Severity | Category | Detail | Fix direction` in that order.
- Stay silent when there are no issues — no header, no placeholder.
- Single non-blocking code path that works identically under interactive and `--auto`.
- Keep the renderer testable in isolation (bats), independent of a live workflow run.

### Non-Goals

- Changing the content or format of `retro.md` itself (owned by `append-retro.sh`).
- Live emission of `workflow_issues` during the run (already shipped).
- Backlog sync from issues (separate follow-up ticket).
- Failure-path rendering (active retro.md surfaces when workflow exits non-success). Tracked as follow-up per discovery OQ-1.
- A new workflow step or workflow_plan edit — render lives in the driver, not the engine.

## Approaches Considered

### Approach 1: Inline bash parser in `_emit_feature_rollup`

Add ~40 lines of bash to `_emit_feature_rollup` that grep retro.md for `## ISSUE-`, parse bullet fields, and print a table.

Pros: zero new files. Cons: bash parsing of H2 + bullet structure is fragile (especially with multi-line detail values); not testable without invoking the entire driver loop; duplicates the python3-heredoc pattern that `append-retro.sh` already establishes.

Complexity: M.

### Approach 2: Standalone `scripts/render-retro.sh` invoked by `_emit_feature_rollup`

A new script `scripts/render-retro.sh <change_id>` that:

1. Resolves retro.md by trying (in order): `$WORKTREE_ROOT/spec/changes/archive/<change_id>/retro.md`, `$WORKTREE_ROOT/spec/changes/archive/<date>-<change_id>/retro.md` (legacy), `$REPO_ROOT/spec/changes/archive/<change_id>/retro.md`, `$WORKTREE_ROOT/spec/changes/<change_id>/retro.md` (pre-archive fallback).
2. Returns silently with no output when the file is absent or contains zero `## ISSUE-` headings.
3. Otherwise parses with a python3 heredoc (mirrors `append-retro.sh`'s pattern) and prints the markdown table to stderr.

`_emit_feature_rollup` invokes it immediately after the cost line. Renderer is testable directly against a fixture retro.md.

Pros: Testable in isolation; one obvious integration point; reuses the producer's parsing idiom; trivial to extend per-cell truncation.

Cons: One additional script file.

Complexity: S.

### Approach 3: Python module under `config/scripts/orchestrator_next/`

Add `orchestrator_next.retro_render` with a CLI entrypoint; integrate with `_emit_feature_rollup`.

Pros: Type-safe; aligns with longer-term Python migration.

Cons: Heavier than the problem warrants; no other code path under `_emit_feature_rollup` uses Python imports; introduces a packaging dependency for a five-row table.

Complexity: M.

### Selected Approach

**Approach 2** — standalone `scripts/render-retro.sh`. Auto-selection picked it as the lowest complexity (S=2 vs. M=3 for both alternatives). It also keeps the renderer testable without bootstrapping the full driver and inherits the same shell-+-python3 idiom the producer already uses, which keeps the cognitive load on the next reader minimal.

## High-Level Design

### Architecture Overview

```
record.py ──(workflow_issues)──▶ append-retro.sh ─▶ retro.md (active)
                                                         │
complete-workflow ──(archive-completed-change)──────────▶│
                                                         ▼
                                              retro.md (archived)
                                                         │
run-workflow.sh::_emit_feature_rollup                    │
   ├── cost-report.sh --tail  ──▶ "feature complete: …"  │
   └── render-retro.sh <cid>   ──▶ (reads)  ─────────────┘
                                ──▶ "## Issues this run (N) | table …" (or silent)
```

The renderer is a sibling of `cost-report.sh` in the driver's rollup function — same lifecycle, same output stream (stderr), same silent-on-error contract.

### Key Abstractions

- **`render-retro.sh <change_id>`** — single-purpose CLI. Resolves the retro file, parses, prints. Exits 0 on success and on every silent-omit case; exits non-zero only on argument misuse.
- **Resolution chain** — ordered list of candidate paths tried in sequence; first existing wins. Encapsulated inside the script so callers do not need to know archive vs. active.
- **Issue parser (python3 heredoc)** — reads the file, splits on `^## ISSUE-` headings, extracts the four bullet fields (`severity`, `category`, `detail`, `fix_direction`), truncates each cell to 120 chars with `…` suffix, and prints a markdown pipe-table.

## Low-Level Design

### Components

1. **`scripts/render-retro.sh`** — new file. ~80 lines. Inputs: `$1 = change_id`. Optional env: `WORKTREE_ROOT` (default `$PWD`), `REPO_ROOT` (default `$WORKTREE_ROOT`). Outputs: markdown table on stderr or nothing.
2. **`scripts/run-workflow.sh::_emit_feature_rollup`** — modified. After the existing `echo "[$(_log_ts)] feature complete: $tail_line" >&2` block, locate `render-retro.sh` via the same `find` pattern used for `cost-report.sh` and invoke it with `$change_id`. Silent if the script is not found (graceful degradation in stripped installs).
3. **`tests/bats/render-retro.bats`** — new bats file. Three cases: populated retro.md renders table; missing file produces empty output and exit 0; file with header-only-and-no-issues produces empty output and exit 0.

### Data Flow

```
_emit_feature_rollup(change_id)
  ├─ tail_line ← cost-report.sh --tail
  ├─ echo "feature complete: $tail_line"  >&2
  └─ render-retro.sh $change_id           >&2
        ├─ resolve retro_path (chain of 4 candidates)
        ├─ [ -f $retro_path ] && grep -cE '^## ISSUE-' $retro_path > 0  ?  continue : exit 0
        └─ python3 -c "parse + truncate + print table"
```

### State Management

No persistent state. The renderer reads `retro.md` only. The active vs. archived path resolution is computed per-call; no caching.

### Error Handling

| Failure | Behavior |
|---|---|
| `change_id` arg missing | Exit 2 with usage message on stderr (script-level misuse). |
| retro.md does not exist at any candidate path | Silent. Exit 0. (UC-E1) |
| retro.md exists but contains zero `^## ISSUE-` headings | Silent. Exit 0. (UC-E2) |
| Malformed issue block (missing severity or category bullet) | Render `—` placeholder for missing field; never crash. (UC-E4) |
| `detail` or `fix_direction` > 120 chars | Truncate to 120 chars + `…`. (UC-E5) |
| python3 not on PATH | Print "render-retro: python3 missing" to stderr and exit 0 (degrade silently — never block the user from seeing the cost line). |
| `render-retro.sh` not found by `_emit_feature_rollup`'s `find` | Skip silently (mirrors current `cost-report.sh` handling at `run-workflow.sh:354`). |

## Constraints

- Must not block the driver under `--auto` (no prompts, no pagers, no interactive forks).
- Must emit on stderr (matches `cost-report.sh`'s output stream and `_emit_feature_rollup`'s convention).
- Resolution must tolerate both archive naming conventions (`<slug>` and legacy `<date>-<slug>`) observed under `spec/changes/archive/`.
- No new workflow step; no `workflow_plan` edit.

## Trade-offs

- **Truncation hard-coded at 120 chars** rather than terminal-width-aware. Rationale: terminal-width detection adds shell portability headaches (`tput cols` is unreliable when stderr is redirected, which is the autopilot loop's normal case). 120 chars is wide enough for typical detail text and narrow enough that 4 columns fit on a 200-column terminal. Full text is one `cat` away in archived retro.md.
- **Failure-path rendering deferred** to a follow-up. Rationale: failed runs do not archive, so the source path is the *active* `spec/changes/<cid>/retro.md`; that's a second resolution branch and a second test matrix. Scope creep risk outweighs the value for v1 (per discovery OQ-1).
- **Renderer in shell+python3** rather than pure Python. Rationale: matches the producer (`append-retro.sh`) which lives in the same inline-scripts neighbourhood — one idiom, one place to learn.

## Acceptance Criteria

- AC-1: `_emit_feature_rollup` reads the archived `retro.md` for the current `change_id` and prints a markdown table with header `| Severity | Category | Detail | Fix direction |` and one body row per `## ISSUE-N` block, in document order. [traces: UC-1]
- AC-2: When `retro.md` is missing at every candidate path, the renderer emits no output and exits 0; `_emit_feature_rollup` completes normally and the cost line is still printed. [traces: UC-2, UC-E1]
- AC-3: When `retro.md` exists but contains zero `^## ISSUE-` headings (header-banner-only case), the renderer emits no output and exits 0. [traces: UC-2, UC-E2]
- AC-4: The renderer runs without prompts, pauses, or pagers; identical behavior under interactive and `--auto` (verified by invoking `_emit_feature_rollup` directly in a bats test under both flag values and asserting identical stderr/stdout). [traces: UC-3]
- AC-5: The rendered "## Issues this run (N)" block appears on stderr immediately after the existing `feature complete: …` cost line for the same `change_id`. [traces: UC-1]
- AC-6: A malformed issue block (e.g. missing `severity` bullet) renders with `—` in the missing cell and does not crash the renderer; the remaining well-formed rows still render. [traces: UC-E4]
- AC-7: `detail` or `fix_direction` values longer than 120 characters are truncated to 120 chars with a trailing `…`; the full text remains in `retro.md`. [traces: UC-E5]
- AC-8: A bats test fixture-drives the renderer with three retro.md states (populated, missing, header-only) and asserts the expected output and exit codes. [traces: UC-1, UC-E1, UC-E2]

## Decisions

- Render in `_emit_feature_rollup`, not as a workflow step → archive runs before rollup, and the cost line is already there → cost + issues stay physically adjacent without re-tangling archive with a step contract.
- Source from archived `retro.md` first → `complete-workflow` has already moved `spec/changes/<cid>/` → `archive/<cid>/` by the time rollup runs → resolution chain tries archived paths before the active path.
- Column order `Severity | Category | Detail | Fix direction` → AC #1 names this exact order → honor verbatim (resolves discovery OQ-2).
- Heading uses `## Issues this run (N)` with parenthesized count → matches the mockup in discovery, gives the user the issue count at a glance, mirrors the H2 style of the producer's blocks (resolves OQ-3).
- Hard-coded 120-char truncation rather than terminal-width-aware → terminal-width detection on a redirected stderr is unreliable; full text remains in `retro.md` for follow-up reading.
- `scripts/render-retro.sh` lives next to `cost-report.sh` (not under `config/scripts/inline/`) → invoked by the driver, not by `record.py`; sibling location mirrors `cost-report.sh`'s role.
- `orchestrator-complete.sh` does not need a separate change → it execs `run-workflow.sh` and inherits the rollup (resolves OQ-4).

## Open Questions

- None blocking. Failure-path rendering (OQ-1) is deliberately deferred to a follow-up ticket per the Trade-offs section.
