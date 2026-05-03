# Spec: ORC-36 — Consolidate active workflow state under `spec/changes/<slug>/`

## Motivation

Active workflow data lives in two parallel directories today:

- `.state/<slug>/` — `state.yaml`, `plan.yaml` (machine-managed via `seed-state.sh`)
- `spec/changes/<slug>/` — `spec.md`, `design.md`, `diagnose.md`, `tasks.md` (artifacts written by agents)

This split is the structural root cause of a recurring bug class:

- **ORC-34** (shipped): canonical `state.yaml` missed `started_at` because producer and metrics consumer drifted across the two locations.
- **ORC-35** (superseded by this ticket): `_resolve_feature_metrics` raises `FileNotFoundError` because `tasks.md` is in `spec/changes/<slug>/` but the resolver looks in `.state/<slug>/`. Worked around with a manual symlink during ORC-34's run.
- **archive-completed-change.sh** silently drops `spec.md`, `design.md`, `diagnose.md`, `tasks.md` from archives (only copies from `.state/<slug>/`). Worked around manually during ORC-34.
- **compute-prediction-accuracy.py** silently reports `predicted=0 / actual=0 / accuracy=100%` because it reads `tasks.md` from the state-yaml sibling path.

Each of these has been patched as a symptom. The structural fix is to collapse to one canonical location.

## Requirements

### Functional

- **F-1.** `seed-state.sh` writes `state.yaml` and `plan.yaml` to `spec/changes/<slug>/`, not `.state/<slug>/`. The `.state/` directory is no longer created.
- **F-2.** `archive-completed-change.sh` archives by renaming `spec/changes/<slug>/` → `spec/changes/archive/<date>-<slug>/` in a single `mv` operation. All artifacts (`state.yaml`, `plan.yaml`, `spec.md`, `design.md`, `diagnose.md`, `tasks.md`, `retro.md`, `discovery.md`, `ux-*`, etc.) move atomically.
- **F-3.** `_resolve_feature_metrics_tasks_path` in `record.py` reads `tasks.md` from `spec/changes/<slug>/tasks.md`. ORC-35 reproduction passes without the symlink workaround.
- **F-4.** `compute-prediction-accuracy.py` reads `tasks.md` from the same `spec/changes/<slug>/` location and reports nonzero predicted/actual counts on a real run.
- **F-5.** `compute-swe-metrics.sh` is invoked with `spec/changes/<slug>/` as its `<state_dir>` argument and finds `state.yaml` there.
- **F-6.** `.gitignore` excludes `spec/changes/*/state.yaml` and `spec/changes/*/plan.yaml` from version control. The `spec/changes/archive/**/state.yaml` subtree (committed, historical) remains tracked.
- **F-7.** `WORKFLOW_STATE_DIR` continues to exist as the env-var seam, but its default value is `$REPO_ROOT/spec/changes` (was `$REPO_ROOT/.state`). All step contracts, skills, agents continue using `$WORKFLOW_STATE_DIR/$CHANGE_ID/` — only the default flips.
- **F-8.** All step contracts, skill files, agent files, and `CLAUDE.md` Paths table reflect the new canonical layout.
- **F-9.** End-to-end `/autopilot` run completes with no manual symlinking and no missing-artifact warnings.

### Non-Functional

- **NF-1.** **No in-flight migration.** Operators must drain (complete or abort) any active workflow before installing this change. The current `orc-36` workflow completes under `.state/orc-36/` via a one-time archive special-case (T-7); no other in-flight runs exist on the destination machine. [traces: UC-5]
- **NF-2.** Single-source-of-truth invariant: producers and consumers of any workflow file must use the same `WORKFLOW_STATE_DIR/<slug>/` path. New code introduced post-fix that adds a divergent path is a regression.
- **NF-3.** Pre-commit hook state-schema check (`.state/*/state.yaml` glob, `scripts/pre-commit.sh:31–32`) becomes vacuously inert because state files are now gitignored. The hook block is removed cleanly — no orphan dead code.

## Acceptance Criteria

- **AC-1.** `seed-state.sh` writes `state.yaml` and `plan.yaml` into `spec/changes/<slug>/`. No `.state/<slug>/` directory is created during a fresh init. [traces: UC-1, AC backlog #1]
- **AC-2.** `archive-completed-change.sh` archives via `mv "spec/changes/<slug>" "spec/changes/archive/<date>-<slug>"`. Archive contains `state.yaml`, `plan.yaml`, `spec.md`, `design.md`, `tasks.md`, `diagnose.md` (when present). [traces: UC-2, AC backlog #2]
- **AC-3.** Running the ORC-35 reproduction (write `tasks.md` to `spec/changes/<slug>/`, call `_resolve_feature_metrics`) succeeds without raising `FileNotFoundError`, no symlink required. [traces: UC-3, AC backlog #3]
- **AC-4.** Running `compute-prediction-accuracy.py` against a real run with non-empty `tasks.md` returns `predicted_tasks > 0`, `actual_tasks > 0`, and a non-trivial `task_accuracy_pct`. [traces: UC-3, AC backlog #4]
- **AC-5.** `compute-swe-metrics.sh "$WORKFLOW_STATE_DIR/$CHANGE_ID"` resolves `state.yaml` and exits 0 against a feature whose state lives in `spec/changes/<slug>/`. [traces: UC-3, AC backlog #5]
- **AC-6.** `.gitignore` contains rules excluding `spec/changes/*/state.yaml` and `spec/changes/*/plan.yaml`. `git check-ignore -v spec/changes/orc-36/state.yaml` reports a match (after the file moves there). `git check-ignore -v spec/changes/archive/2026-05-03-orc-34/state.yaml` reports NO match (still tracked). [traces: UC-4, AC backlog #6]
- **AC-7.** `grep -rn 'WORKFLOW_STATE_DIR.*\.state' config/ skills/ agents/ scripts/` returns zero hits across step contracts, SKILL.md files, and agent files. The `.state` token survives only inside intentional historical/migration commentary or this ticket's own diagnose.md. [traces: UC-1, AC backlog #7]
- **AC-8.** `CLAUDE.md` Paths table row "Active workflow state" reads `$REPO_ROOT/spec/changes/<slug>/state.yaml`. [traces: UC-4, AC backlog #8]
- **AC-9.** A full `/autopilot` (or end-to-end equivalent) run on a follow-up backlog ticket completes through `archive-completed-change` with no manual symlinking, no `tasks.md not found` errors, and a complete archive directory. [traces: UC-1..UC-3, AC backlog #9]
- **AC-10.** Regression test (`spec/changes/orc-36/tests/` or equivalent) reproduces the four failure modes from `diagnose.md` against the pre-fix code (must FAIL on HEAD before T-2 lands) and passes against the post-fix code. [traces: bugfix-rule: regression test before fix]

## Use Cases

- **UC-1.** Operator runs `/autopilot ORC-N` on a fresh backlog ticket. `seed-state.sh` writes `state.yaml`/`plan.yaml` directly into `spec/changes/orc-n/`. Architect agent writes `spec.md`/`design.md`/`tasks.md` to the same directory. No `.state/` directory exists at any point.
- **UC-2.** Workflow reaches `archive-completed-change`. Script renames `spec/changes/orc-n/` → `spec/changes/archive/<date>-orc-n/` in one `mv`. Archive directory contains every artifact written during the run with no extra copy step.
- **UC-3.** During `mark-change-completed` and `compute-prediction-accuracy`, the resolvers read `tasks.md` from `spec/changes/<slug>/`. Metrics report real predicted/actual task counts. No `FileNotFoundError`, no silent-zero divergence.
- **UC-4.** A new contributor reads `CLAUDE.md` Paths table and finds one canonical workflow-state path. They open `spec/changes/<slug>/` and see state + artifacts side-by-side.
- **UC-5.** Operator with no active in-flight workflow installs this change. The current `orc-36` run completes under `.state/orc-36/`; its archive step uses a one-time dual-source archive (state from `.state/`, artifacts from `spec/changes/`) — see T-7. No other workflow exists, so no migration.

## In Scope

- `skills/orchestrate/scripts/seed-state.sh` — writer path change
- `scripts/inline/archive-completed-change.sh` — single-op rename
- `config/scripts/orchestrator_next/record.py` — `_resolve_feature_metrics_tasks_path` (line 798)
- `scripts/inline/compute-prediction-accuracy.py` — tasks.md path
- `scripts/inline/compute-swe-metrics.sh` — invocation path (passes through, no internal change needed; verify caller)
- 9 step contracts under `config/steps/*.yaml` — text references
- 4 skill files (`skills/orchestrate`, `skills/learn`, `skills/telemetry`, `skills/linear`) — `WORKFLOW_STATE_DIR` defaults + path text
- `agents/workflow-init.md` — instruction text
- `CLAUDE.md` — Paths table
- `.gitignore` — two new patterns
- `scripts/pre-commit.sh` — remove now-inert `.state/*/state.yaml` schema check (lines 31–32 block)
- One-time orc-36 self-archival: T-7 special-cases this run only
- Regression test that exercises all four failure modes from diagnose.md

## Out of Scope

- ORC-32 (`read-sub-state-metrics.sh`) — separate ticket, separate drift family.
- Renaming or retiring the `WORKFLOW_STATE_DIR` env var. Default flips; var stays as the override seam.
- General-purpose migration script for `.state/<slug>/` → `spec/changes/<slug>/` for arbitrary in-flight workflows. Operators drain instead; only orc-36's own run is special-cased.
- Refactoring `_resolve_tasks_md` (lines 868–903 in `record.py`) to reuse `_resolve_feature_metrics_tasks_path`. The duplicate fallback logic stays in place — minimal fix only.
- Tooling changes for `metrics.duckdb` schema, telemetry pipelines, or DuckDB views.
- Backwards-compat fallback that reads from `.state/<slug>/` when `spec/changes/<slug>/` is absent. Would re-introduce the dual-path drift; explicitly rejected.

## Alternatives Considered

1. **Dual-path lookup in consumers** — every consumer falls back to the alternate directory. Rejected: keeps the seam open, just defers drift. Diagnose.md flagged `_resolve_tasks_md` as already-divergent evidence of this anti-pattern.
2. **Move artifacts into `.state/<slug>/`** (option C from ORC-35) — rejected because artifacts (`spec.md`, `design.md`) are human-reviewable and belong on the tracked tree, not in a machine-state directory that would also need `.gitignore` carve-outs.
3. **One-shot migration script for in-flight workflows** — rejected (NF-1). Solo-developer scope; only one in-flight run (this one); cost of writing + testing migration > cost of draining. Documented operator requirement instead.
4. **Retire `WORKFLOW_STATE_DIR` entirely; hardcode `spec/changes/`** — rejected. The env var is a useful test/CI seam (lets specs run with synthetic dirs). Re-pointing the default is a single-line change; deletion is invasive.

## Open Questions

None. Diagnose.md questions 1–5 resolved as:

1. Re-point `WORKFLOW_STATE_DIR` default; do not retire.
2. Option C: drain instead of migrate; orc-36 self-archive special-cased in T-7.
3. Pre-commit hook block retired (becomes inert; cleaner to delete than leave dead).
4. ORC-32 sequenced independently; this fix does not touch `read-sub-state-metrics.sh`.
5. `.gitignore` patterns scoped to `spec/changes/*/state.yaml` and `spec/changes/*/plan.yaml` — single-segment glob, does not match `spec/changes/archive/<date>-<slug>/state.yaml` (two extra segments).
