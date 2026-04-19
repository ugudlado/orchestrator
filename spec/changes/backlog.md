# Backlog

<!-- Consolidated on 2026-04-20. Single source of truth for pending work.
     Two sections: Features and Bugs. Each has a Summary table and detailed
     entries as H2 blocks (keyed by slug), sorted by score descending.
     The ideate skill and ideator agent read this file.
     To add: append a new H2 under the right section; update the section's Summary table.
     To retire: move the block to spec/changes/backlog-archive.md with a shipped-at date.
 -->

## Summary

- **Features**: 20
- **Bugs**: 4

## Features — Summary

| # | Slug | Title | Score |
| ---: | --- | --- | ---: |
| 1 | [backfill-step-history-jsonl](#backfill-step-history-jsonl) | Backfill step_history Coverage from JSONL | 8.5 |
| 2 | [metrics-regression-detection](#metrics-regression-detection) | Metrics Regression Detection + Autopilot Breaker | 8.2 |
| 3 | [error-recovery-contract-step](#error-recovery-contract-step) | Explicit Error Recovery Step Contract | 8.0 |
| 4 | [per-subagent-cost-attribution](#per-subagent-cost-attribution) | Sub-Agent Cost Attribution | 8.0 |
| 5 | [step-timing-telemetry](#step-timing-telemetry) | Step Timing Telemetry | 7.7 |
| 6 | [consolidate-script-trees](#consolidate-script-trees) | Consolidate orchestrator script trees and test roots | 7.5 |
| 7 | [workflow-improve-skill-implementation](#workflow-improve-skill-implementation) | Implement /workflow-improve Skill | 7.5 |
| 8 | [orchestrate-dispatch-loop-hardening](#orchestrate-dispatch-loop-hardening) | Harden the Orchestrate Dispatch Loop | 7.0 |
| 9 | [worktree-cleanup-on-failure](#worktree-cleanup-on-failure) | Worktree Cleanup on Workflow Failure | 6.8 |
| 10 | [dry-run-mode](#dry-run-mode) | Dry Run Mode | 6.7 |
| 11 | [skill-stub-audit](#skill-stub-audit) | Audit and Prune Stub Skills | 6.3 |
| 12 | [doctor-deep-check](#doctor-deep-check) | Deep Doctor Health Check | 6.0 |
| 13 | [register-repo-changeid-fallback](#register-repo-changeid-fallback) | register-repo.sh: Fall Back to Directory Basename When change_id Missing | 6.0 |
| 14 | [step-contract-input-output-graph](#step-contract-input-output-graph) | Step Contract Input/Output Dependency Graph | 5.5 |
| 15 | [install-uninstall-cleanup](#install-uninstall-cleanup) | Install/Uninstall Cleanup and Shell Detection | 5.2 |
| 16 | [multi-repo-orchestrator-home](#multi-repo-orchestrator-home) | Multi-Repo ORCHESTRATOR_HOME Isolation | 5.0 |
| 17 | [state-yaml-schema-validation](#state-yaml-schema-validation) | State YAML Schema Validation | 3.5 |
| 18 | [conventions-lint-script](#conventions-lint-script) | CONVENTIONS.md Lint Script | 3.0 |
| 19 | [schema-validation-step](#schema-validation-step) | Schema Self-Validation on Load | 3.0 |
| 20 | [conditional-step-dependencies](#conditional-step-dependencies) | Conditional Step Dependencies | 2.8 |

## Bugs — Summary

| # | Slug | Title | Score |
| ---: | --- | --- | ---: |
| 1 | [fix-missing-step-contracts](#fix-missing-step-contracts) | Fix missing step contracts (ISSUE-18) | 7.8 |
| 2 | [self-referential-bug-bootstrap](#self-referential-bug-bootstrap) | Self-referential bugfix bootstrap (ISSUE-19) | 6.3 |
| 3 | [pricing-date-suffix-lookup](#pricing-date-suffix-lookup) | Pricing lookup tolerant of model date-suffixes (ISSUE-23) | 5.8 |

---

# Features

## backfill-step-history-jsonl

**Backfill step_history Coverage from JSONL** (score 8.5)

### Idea
Re-run JSONL enrichment across archived features to backfill missing tokens and `tools_json` on `step_history` rows, then add an invariant preventing the gap from reopening.

### Evidence
- `step_history` has 193 rows; only 39 have `total_tokens`, only 3 have `tools_json`.
- `per_agent_tool_uses` has 2 rows across 20 ingested features — per-agent-per-tool breakdown is effectively empty.
- JSONL session data exists for most archives but the initial enrichment script missed many rows (path-resolution bugs, quoted-timestamp bugs — several fixed after the bulk of archives were ingested).

### Fix
1. Re-run JSONL enrichment pass over every archived `state.yaml` with JSONL sessions available.
2. Re-run `register-repo.sh` to reingest.
3. Add invariant in register-repo: any `step_history` row with `agent != NULL AND status = completed` must have `total_tokens > 0` OR be marked `agent: inline`.

### Why Now
Unblocks per-step token/cost analysis for 80% of history. Prerequisite for regression detection (needs a full baseline).

### Priority
- User value: 8/10
- Strategic fit: 9/10
- Technical leverage: 9/10
- Effort: medium
- **Score: 8.5**

---

## metrics-regression-detection

**Metrics Regression Detection + Autopilot Breaker** (score 8.2)

### Idea
Turn the metrics stack from a passive ledger into an active guardrail. Detect feature-level and step-level regressions against rolling baselines, surface them in `/telemetry`, and stop `/autopilot` from compounding damage when the last 3 runs all regressed.

### Evidence
- `execute-next-task` averaged ~19 min across 2 samples — no rolling baseline exists to flag drift.
- The `/learn` skill references "steps taking >2× average" but nothing produces that signal.
- Prior commit 62166d6 fixed a PyYAML indent bug that had silently corrupted step_history parsing — a drift alarm would have caught it days earlier.
- The cost_usd=0 regression that motivated the observability batch also went undetected for multiple features.

### Fix
1. New step `compute-metrics-regressions.yaml`, run after `compute-swe-metrics`.
2. New table `metrics_anomalies`:
   ```
   change_id, anomaly_type, metric, observed, baseline_median, ratio, detected_at
   ```
3. Flag:
   - feature `cost_usd` > 1.5× 30-day median (same schema)
   - step `duration_ms` > 2× median for same `step_id`
   - single-agent token spike > 2× median
4. Surface top anomalies in `/telemetry`.
5. Autopilot breaker: the iterate step refuses to pick new work if the last 3 completed features each appear in `metrics_anomalies`; writes `stop_reason: regression_breaker` to checkpoint.

### Why Now
Prerequisite: fix-cost-usd-and-widen-token-split (baselines on zeros are meaningless) and backfill-step-history-jsonl (needs full history). This caps the observability arc — once it lands, future regressions self-report.

### Priority
- User value: 9/10
- Strategic fit: 8/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 8.2**

---

## error-recovery-contract-step

**Explicit Error Recovery Step Contract** (score 8.0)

### Idea
The orchestrate SKILL.md dispatch loop says "Follow Error Recovery Contract (CONVENTIONS.md) for all failures" but CONVENTIONS.md does not contain an explicit Error Recovery Contract section. The `execute-next-task.yaml` step contract has inline retry logic (steps 7a-7f), `run-phase-review.yaml` has its own retry pattern (step 7), and `phase-signoff.yaml` has a rejection-fix loop (step 5). These three retry/recovery patterns are defined independently with slightly different semantics. There should be a single `error-recovery.yaml` step contract (or a CONVENTIONS.md section) that defines the canonical retry/escalation pattern: (1) diagnose failure, (2) attempt scoped fix, (3) re-verify, (4) increment retry counter, (5) escalate at max_retries. Then the three existing steps reference it instead of each defining their own variant.

### Why Now
The orchestrator references an Error Recovery Contract that does not exist. This is a concrete gap -- any agent following the dispatch loop instructions will hit a broken reference. Additionally, inconsistent retry semantics across steps mean that `/learn`'s cross-feature retry analysis (step 2b) is comparing apples to oranges when aggregating retry data across different step types.

### Priority
- User value: 6/10
- Strategic fit: 9/10
- Technical leverage: 9/10
- Effort: medium
- **Score: 8.0**

---

## per-subagent-cost-attribution

**Sub-Agent Cost Attribution** (score 8.0)

### Idea
Today, when an agent invokes the `Agent` tool to spawn a sub-agent, the sub-agent's tokens land in the **parent agent's** bucket in `per_agent_metrics`. You cannot tell how much of an architect's 416k-token bill was the architect itself vs. a sub-agent it spawned. At Opus pricing ($15/$75 per 1M) a single sub-agent call can dwarf its parent invisibly.

### Evidence
- `per_tool_uses` shows the `Agent` tool invoked 16 times across ingested features.
- No table tracks parent→subagent token flow. `per_agent_metrics` collapses everything under the caller.

### Fix
1. Parse `Agent` tool invocations from JSONL — each call has a `subagent_type` and the child session produces its own token counts.
2. New table `per_subagent_calls`:
   ```
   parent_change_id, parent_agent, subagent_type,
   input_tokens, output_tokens, cost_usd, duration_ms
   ```
3. Decision (design phase): either subtract sub-agent tokens from parent totals, or keep both clearly labeled (`self_tokens` vs `inclusive_tokens`).

### Why Now
Reveals the single most mis-attributed cost in the stack. Enables "stop spawning Opus architect for trivial sub-tasks" decisions that can cut feature cost 30–50% on Agent-heavy features. Depends on fix-cost-usd (otherwise the sub-agent costs will also be zero).

### Priority
- User value: 8/10
- Strategic fit: 8/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 8.0**

---

## step-timing-telemetry

**Step Timing Telemetry** (score 7.7)

### Idea
Add wall-clock timing to every step execution by recording `started_at` and `completed_at` in each `step_history` entry. The grammar already declares these fields as optional in `step_record`, but nothing produces them today. With timing data, the `/telemetry` skill can show a Gantt-style phase breakdown, and `/learn` can flag duration outliers (the learn skill already references "steps taking >2x average" but has no data to work with).

### Why Now
The SWE metrics system (`swe_metrics.wall_clock_minutes`) already tracks total elapsed time, but it's a single number -- you can't tell whether the specify phase took 80% of the time or the implement phase did. The `step_record` grammar already has `started_at` and `completed_at` fields defined. The recent refactoring to consolidate state into `WORKFLOW_STATE_DIR` means there's exactly one place to read/write this data.

### Prototype
No visual prototype needed. The change is structural: update the orchestrate skill's dispatch loop (SKILL.md step 4) to emit timestamps in `step_history` entries, and update the telemetry skill to render a per-step duration breakdown.

### Priority
- User value: 8/10
- Strategic fit: 7/10
- Technical leverage: 8/10
- Effort: small
- **Score: 7.7**

---

## consolidate-script-trees

**Consolidate orchestrator script trees and test roots** (score 7.5)

### Idea

The orchestrator's script layer has accumulated four overlapping locations and four test roots:

- `scripts/` — shell utilities and `scripts/inline/` (the real inline-step ports)
- `config/scripts/orchestrator_next/` — the Python package (`upsert.py`, `cost_report.py`, tests)
- `config/scripts/adapters/` — adapters
- `config/scripts/tests/`, `config/scripts/__tests__/`, `config/scripts/test-fixtures/` — three separate test locations

Plus `compute-swe-metrics.sh` at **736 lines of bash** — directly contradicts the `bash-fragility-prefer-python-for-new-code` learning.

### Proposed restructure

**Phase A — consolidate Python:**
- One canonical Python package (e.g. `orchestrator_py/` or keep `orchestrator_next/`).
- Move `config/scripts/adapters/` into the package as a submodule.
- Collapse `tests/`, `__tests__/`, `test-fixtures/` into one `tests/` tree inside the package.

**Phase B — port bash to Python:**
- Rewrite `compute-swe-metrics.sh` (736 LOC) as a Python module; keep a thin shell wrapper if external callers invoke it.
- Move remaining shell-shaped scripts to `scripts/shell/`.

**Phase C — update references:**
- Hooks, step contracts, `install.sh`, README paths.
- Run full orchestrate cycle end-to-end to confirm parity.

### Why Now

1. Recent metrics work (HL-290, HL-291, post-OTel cleanup) already churned these files — piggyback on warm context.
2. Unblocks **HL-298** (harden inline script ports) — that work becomes trivial after layout is unified.
3. Several backlog items (`skill-stub-audit`, `error-recovery-contract-step`) assume a cleaner layout than we have.
4. The 736-line bash script is a known fragility risk and a structural root cause of debugging pain in metrics work.

### Acceptance

- One Python package root; one `tests/` root; one `scripts/shell/` root.
- `compute-swe-metrics.sh` ported to Python with test coverage; old bash removed or reduced to a wrapper.
- All existing orchestrate/autopilot flows pass an end-to-end run.
- `install.sh` still produces a working install on a clean machine.
- No stale directory left behind.

### Dependencies / Interactions

- **Unblocks** HL-298 (harden inline script ports).
- **Touches** same surface area as `skill-stub-audit` backlog item — coordinate ordering, no hard dependency.
- Best sequenced **after** `split-cost-report-package` (trivial; sets the package-layout pattern first).

### Out of Scope

- Rewriting business logic — pure move + rename + port.
- Changing what the scripts do.
- New features in `doctor.py`.

### Priority

- User value: 6/10
- Strategic fit: 9/10 (removes a load-bearing fragility)
- Technical leverage: 9/10 (unblocks multiple downstream items)
- Effort: medium
- **Score: 7.5**

### Size

Medium. Target: 6-8 tasks across the three phases. Bulk of effort is Phase B (bash → Python port) and verification.

### Labels

orchestrator, improvement

### Notes

Linear ticket creation blocked by workspace free-tier limit on 2026-04-19; file this in Linear when the workspace is upgraded.

---

## workflow-improve-skill-implementation

**Implement /workflow-improve Skill** (score 7.5)

### Idea
The `/workflow-improve` skill at `skills/workflow-improve/SKILL.md` is a 3-line stub with no real logic ("Analyze metrics and identify improvements to workflow infrastructure"). Similarly, the `/telemetry` skill is a 2-line stub. The `/workflow-improve` skill should be the user-facing command that validates the full workflow graph: checks every schema's step references resolve to actual step contract YAMLs, checks every step contract's `agent:` field resolves to an agent `.md`, checks `flags_read` references exist in schema `defaults`, and validates template references. This overlaps with the existing `doctor-deep-check` backlog item but is runtime-invocable rather than a Makefile target, and focuses on structural integrity of the workflow graph rather than symlink health.

### Why Now
The orchestrator has 38 step contracts, 6 schemas, and 11 agents. As this grows, silent reference breakage (a schema referencing a step that was renamed, a step referencing an agent that was deleted) will become a real maintenance burden. The recent refactoring wave (renaming SPEC_CHANGES_DIR, moving config paths) is exactly the kind of change that creates these breakages.

### Priority
- User value: 7/10
- Strategic fit: 9/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 7.5**

---

## orchestrate-dispatch-loop-hardening

**Harden the Orchestrate Dispatch Loop** (score 7.0)

### Idea
The orchestrate skill's dispatch loop (SKILL.md section 4) describes the core execution engine but has several fragile points: (1) The "READ step contract" instruction does not specify what to do if the YAML file is missing or malformed -- the agent will just fail mid-workflow. (2) The "READ agent definition" instruction has no fallback if the `.md` file is missing. (3) The "AFTER step completes" section writes `next_step` but does not handle the case where state.yaml is corrupted or locked. (4) There is no timeout or circuit-breaker for agent spawns that hang. Add explicit error handling clauses to the dispatch loop: file-not-found checks before each READ, agent spawn timeout guidance, and state.yaml write-after-verify pattern.

### Why Now
As the orchestrator runs longer autonomous sessions (autopilot with multiple iterations), the probability of hitting these edge cases increases. A single missing step contract YAML (perhaps due to a rename that was not propagated) can crash an entire autopilot session with no recovery path. The recent rename of SPEC_CHANGES_DIR to WORKFLOW_STATE_DIR is exactly the kind of change that could leave stale references.

### Priority
- User value: 7/10
- Strategic fit: 8/10
- Technical leverage: 7/10
- Effort: small
- **Score: 7.0**

---

## worktree-cleanup-on-failure

**Worktree Cleanup on Workflow Failure** (score 6.8)

### Idea
When a workflow fails mid-execution (agent crash, user abort, max retries exceeded), the git worktree at `~/code/feature_worktrees/$SLUG` and the branch `feature/$SLUG` are left behind. The `remove-worktree.yaml` step only runs in the `complete` phase, so any workflow that stops before completion leaks worktrees. Over time, `git worktree list` accumulates stale entries, and `~/code/feature_worktrees/` fills with abandoned directories. Add: (1) a `make clean-worktrees` target that lists stale worktrees (no matching active state.yaml) and offers to remove them, (2) a check in `create-worktree.yaml` that warns if more than 5 worktrees exist (suggesting cleanup), and (3) guidance in the `on_max_retries: escalate` handler to mention worktree cleanup.

### Why Now
The autopilot mode runs multiple iterations, each potentially creating a worktree. If any iteration fails and the next starts, worktrees accumulate. The `doctor` command does not check for orphaned worktrees. This is the kind of slow resource leak that is invisible until disk space runs low.

### Priority
- User value: 7/10
- Strategic fit: 6/10
- Technical leverage: 6/10
- Effort: small
- **Score: 6.8**

---

## dry-run-mode

**Dry Run Mode** (score 6.7)

### Idea
Add a `--dry-run` flag to all schemas that prints the resolved step plan without executing anything. Output would show: schema selected, flags resolved, each phase with its filtered steps (marking conditional steps with their condition), and which agents would be spawned. This gives users a preview of what `/develop` will do before it starts creating worktrees, spawning agents, and modifying state.

### Why Now
The orchestrate skill already does schema resolution and step filtering (SKILL.md sections 1 and 3). A dry run just stops before section 4 (dispatch loop). Users currently have to read schema YAML files manually to understand what steps will run -- the conditional `if` / `if not` filtering makes this non-trivial. This is especially useful when testing new flag combinations or debugging why a step was skipped.

### Prototype
Before/after example:

```
$ /develop "add search feature" --no-tdd --no-design --dry-run

Schema: feature (v3)
Flags: tdd_required=false, design=false, ux_design=true, auto=false, linear=true

Phase: specify
  1. create-worktree
  2. load-project-context
  3. explore                        [agent: discoverer]
  4. design-exploration              SKIPPED (design=false)
  5. ux-design                      [agent: ux-reviewer]
  6. create-or-refresh-artifacts    [agent: architect]
  7. run-phase-review               [agent: reviewer]
  8. create-linear-ticket
  9. phase-signoff

Phase: implement
  1. execute-next-task (repeat)     [agent: developer]
  2. run-simplify                   [agent: developer]
  3. run-ux-critique                [agent: ux-reviewer]
  4. run-phase-review               [agent: reviewer]
  5. final-signoff

Phase: complete
  ...
```

### Priority
- User value: 7/10
- Strategic fit: 6/10
- Technical leverage: 7/10
- Effort: small
- **Score: 6.7**

---

## skill-stub-audit

**Audit and Prune Stub Skills** (score 6.3)

### Idea
Several skills are effectively stubs with no real implementation: `/telemetry` (2 lines), `/workflow-improve` (3 lines), `/reflect` (likely minimal). Meanwhile, `/specify`, `/implement`, `/commit-group`, `/critique`, `/humanizer`, `/pal`, `/portless`, `/shadcn`, `/systematic-debugging`, and `/frontend-design` exist as skill directories. Some of these may be fully implemented, some may be stubs, and some may be dead code from earlier iterations. Audit all 22 skill directories: classify each as (a) fully implemented, (b) stub needing implementation, (c) dead code to remove, or (d) alias to another skill (like `/develop` -> `/orchestrate`). Then either implement the stubs that have clear value or remove the ones that are just noise. Having stub skills that users can invoke but that produce no useful output is worse than not having them at all.

### Why Now
The orchestrator is positioning itself as a universal workflow engine. Users discovering skills that do nothing will lose trust. Better to have 10 solid skills than 22 where half are empty.

### Priority
- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 6.3**

---

## doctor-deep-check

**Deep Doctor Health Check** (score 6.0)

### Idea
Expand `make doctor` from its current 6-line existence check into a comprehensive health validator. Currently it only checks if directories exist. A real doctor command should verify: (1) symlinks point to valid targets (not stale after a worktree switch -- the gotcha in project.yaml), (2) every schema referenced in `project.yaml schemas:` has a matching workflow YAML, (3) every step referenced in schemas has a matching step contract YAML, (4) every agent referenced in step contracts has a matching agent .md, (5) ORCHESTRATOR_HOME matches the expected path, (6) no orphaned state.yaml files (active changes with no worktree). This catches the most common failure mode: running `make setup` from a worktree instead of main.

### Why Now
The gotcha documented in `project.yaml` ("install.sh uses ln -sf to re-point existing symlinks -- running make setup from the worktree sets ORCHESTRATOR_HOME to the worktree path, not the main repo") is a real, recurring problem. A smart doctor command would detect this immediately instead of letting it silently corrupt the next workflow run. The recent install.sh refactoring for config symlinks makes this the right time to add validation.

### Prototype
```
$ make doctor
Checking orchestrator health...
  [OK] spec/project.yaml
  [OK] install.sh
  [OK] config/workflows (6 schemas)
  [OK] config/steps (38 contracts)
  [OK] agents (11 definitions)
  [OK] skills (23 skills)
  [OK] ORCHESTRATOR_HOME -> /Users/spidey/code/orchestrator (matches repo root)
  [OK] All schema refs resolve (feature -> feature.yaml, etc.)
  [OK] All step refs resolve (38/38 steps have contracts)
  [OK] All agent refs resolve (8/8 agents have .md files)
  [WARN] Stale worktree state: changes/orchestrator/old-feature/state.yaml (no worktree dir)
  [OK] Symlinks valid (3 dirs, 2 files)
Done. 11 checks passed, 1 warning.
```

### Priority
- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: small
- **Score: 6.0**

---

## register-repo-changeid-fallback

**register-repo.sh: Fall Back to Directory Basename When change_id Missing** (score 6.0)

### Idea

`register-repo.sh` currently skips `state.yaml` files that lack a `change_id`
field, logging `skip:` and continuing. This is correct per FR-6 but loses
ingestable data: 3 of 10 archives in the orchestrator repo
(`hl-253-extract-dev-workflow-system-into-standalone-repo`,
`quality-gates-phase2`, `reliability-phase1`) are fully populated state.yaml
files from before the `change_id` field was added.

These archives have `slug` and other useful fields but skip silently.

### Scope

When `change_id` is empty after `yq` extraction, fall back to:

```bash
change_id=$(basename "$(dirname "$state_file")")
```

Then proceed through the existing slug guard
(`^[a-z0-9._-]+$`) and ingest. Existing slug-validation defense remains intact.
Log: `warn: change_id absent, using dirname fallback: <slug>`.

### Out of scope

- Backfilling `change_id` into the legacy state.yaml files themselves
- Changing the schema (PK still `(repo_root, change_id)` — fallback just
  populates the value at ingest time)

### Why Now

Surfaced during implement-phase review of `cross-repo-metrics-duckdb`
(reviewer Important Finding I1). The fix is ~3 lines and recovers 30% of
this repo's archive history. Should ship before any consumer (e.g.,
`/learn` querying DuckDB) runs analytics that would notice the gap.

### Priority

- User value: 5/10
- Strategic fit: 6/10
- Technical leverage: 8/10 (3-line fix, 30% data recovery)
- Effort: extra-small (--light feature)
- **Score: 6.0**

---

## step-contract-input-output-graph

**Step Contract Input/Output Dependency Graph** (score 5.5)

### Idea
Each step contract declares `inputs:` and `outputs:`. These form an implicit dependency graph: `explore` outputs `discovery_result`, which `create-or-refresh-artifacts` consumes via `phase_context_bundle`. But this graph is never materialized or validated. Build a script or skill that: (1) parses all step contract YAMLs and extracts inputs/outputs, (2) for each schema, walks the phase step lists and verifies that every step's declared inputs are satisfied by a prior step's outputs or by initial state, (3) detects orphaned outputs (produced but never consumed) and unresolved inputs (consumed but never produced). Output as a dependency graph (text or diagram). This would catch wiring bugs like a schema that skips `explore` but still expects `discovery_result` downstream.

### Why Now
With 38 step contracts and 6 schemas, manual tracking of which step produces what and which step needs what is error-prone. The conditional step system (`if design`, `if ux_design`) makes this worse -- a step might be skipped by a flag but its output might still be expected downstream. This idea complements `schema-validation-step` (which validates at load time) by providing an offline analysis tool.

### Priority
- User value: 5/10
- Strategic fit: 7/10
- Technical leverage: 6/10
- Effort: medium
- **Score: 5.5**

---

## install-uninstall-cleanup

**Install/Uninstall Cleanup and Shell Detection** (score 5.2)

### Idea
`install.sh` has three issues: (1) It hardcodes `~/.zshrc` as the shell profile -- users on bash, fish, or nushell get no ORCHESTRATOR_HOME export. Detect `$SHELL` and write to the correct profile. (2) There is no `uninstall.sh` or `make uninstall` target -- removing the orchestrator requires manually deleting symlinks from `~/.claude/agents/`, `~/.claude/skills/`, and `~/.config/orchestrator/`, plus removing the export line from `.zshrc`. (3) The install script does not clean up stale symlinks -- if an agent `.md` file is renamed or deleted from the repo, the old symlink persists in `~/.claude/agents/`. Add a staleness check that removes symlinks pointing to non-existent targets.

### Why Now
The project learned (in `gotchas`) that "running make setup from the worktree sets ORCHESTRATOR_HOME to the worktree path, not the main repo." This is a direct consequence of install.sh not validating or warning about the source path. Hardening install.sh now prevents a class of setup errors that waste entire workflow runs.

### Priority
- User value: 6/10
- Strategic fit: 5/10
- Technical leverage: 4/10
- Effort: small
- **Score: 5.2**

---

## multi-repo-orchestrator-home

**Multi-Repo ORCHESTRATOR_HOME Isolation** (score 5.0)

### Idea
Currently, `WORKFLOW_STATE_DIR` defaults to `$ORCHESTRATOR_HOME/changes/$REPO_NAME`, which means all repos share the same `~/.config/orchestrator/changes/` parent. This works but has no isolation: a bug in one repo's state.yaml cleanup could affect another repo's active changes. More importantly, `install.sh` hardcodes `~/.zshrc` and does not support multiple orchestrator installations (e.g., a stable release and a development branch). Add: (1) per-repo override support via `.orchestrator.yaml` in repo root (setting a custom `WORKFLOW_STATE_DIR`), (2) `install.sh` support for `--profile` flag to install to a named profile instead of default, (3) documentation of the multi-repo state isolation model.

### Why Now
The vision says "universal workflow engine for LLMs -- define any process as config, run it on any tool." As adoption grows beyond a single developer's repos, the shared-state model will create conflicts. This is a foundational concern for the "portable across repos" story.

### Priority
- User value: 5/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 5.0**

---

## state-yaml-schema-validation

**State YAML Schema Validation** (score 3.5)

### Idea
Create a validation step (or pre-check in the dispatch loop) that validates `state.yaml` against the grammar defined in `grammar.yaml` before each step execution. The grammar already defines required fields (`schema`, `status`, `phase`, `step_id`, `flags`, `started_at`, `updated_at`) and valid enum values (`active|completed|paused`), but nothing enforces them at runtime. When an LLM writes a malformed state.yaml (wrong field name, missing required field, invalid enum), the error surfaces much later as a cryptic failure in a downstream step. Early validation with a clear error message ("state.yaml missing required field 'flags'") would catch corruption immediately.

### Why Now
The `state_contract` section in `project.yaml` already declares `required: [schema, flags]` and `merge_precedence`, but the orchestrate skill doesn't validate against it. The grammar file defines the full schema. The gap between "defined" and "enforced" is the problem. With the recent consolidation of state into `WORKFLOW_STATE_DIR`, there's now a single canonical path to validate.

### Prototype
No visual prototype. Implementation: add a validation check at the top of the dispatch loop (orchestrate SKILL.md section 4) that reads `state.yaml`, checks all required fields from `grammar.yaml state.required`, validates enum values, and reports specific violations before executing any step.

### Priority
- User value: 7/10
- Strategic fit: 7/10
- Technical leverage: 7/10
- Effort: medium
- **Score: 3.5**

---

## conventions-lint-script

**CONVENTIONS.md Lint Script** (score 3.0)

### Idea
Create a `scripts/lint-conventions.sh` that validates all step contracts, schemas, and templates against the format contracts defined in CONVENTIONS.md. Currently, CONVENTIONS.md defines detailed structural contracts (Task Format, Discovery Brief Format, Specification Format, Design Format, etc.) but compliance is only checked by the reviewer agent at runtime -- which means malformed artifacts waste an entire review cycle before being caught. A lint script could check: (1) every step contract has the 4 required sections (rules, instruction, verify, outputs), (2) every step intent is a single sentence, (3) `flags_read` entries have `effect` descriptions, (4) templates match their format contract sections.

### Why Now
CONVENTIONS.md is 1200+ lines and growing -- it's the richest source of structural rules in the project. The `/learn` skill actively adds rules to step contracts, and the `/workflow-improve` skill edits them. Neither has a way to verify they haven't introduced a structural violation. Adding a lint script to `make doctor` or as a pre-commit check would catch drift early.

### Prototype
```
$ bash scripts/lint-conventions.sh
Checking 38 step contracts...
  [OK] execute-next-task.yaml: 4 sections, single-sentence intent
  [OK] run-phase-review.yaml: 4 sections, single-sentence intent
  [FAIL] explore.yaml: missing outputs section (found: discovery_result)
  [WARN] diagnose.yaml: intent uses "and" — possible SRP violation
Checking 6 schemas...
  [OK] feature.yaml: all step refs resolve
Checking 9 templates...
  [OK] feature/spec.md: matches Specification Format Contract sections
Summary: 47 files checked, 1 error, 1 warning
```

### Priority
- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 3.0**

---

## schema-validation-step

**Schema Self-Validation on Load** (score 3.0)

### Idea
When the orchestrate skill loads a schema YAML (SKILL.md section 1, step 3), validate it against `grammar.yaml` before proceeding. Currently, malformed schemas (missing `phases`, invalid `step_entry` forms, typo in a flag name) are only caught when execution hits the broken part -- sometimes deep into a multi-hour workflow. A validation pass at load time would catch: (1) missing required fields per grammar, (2) step references that don't have a matching contract YAML in `config/steps/`, (3) agent references in step contracts that don't have a matching `.md` in `agents/`, (4) flag references in `if`/`if not` conditions that aren't declared in `defaults` or `flags`. This is essentially the "deep doctor" applied to a single schema at runtime.

### Why Now
The grammar file defines the full structural contract but nothing enforces it. As `/learn` and `/workflow-improve` modify schemas automatically, the risk of introducing structural errors increases. Catching them at load time (before worktree creation, agent spawning, etc.) is much cheaper than catching them mid-workflow.

### Prototype
Error output example:
```
[orchestrate] Schema validation failed for feature.yaml:
  - Step "desgin-exploration" (phase: specify) has no matching contract in config/steps/
  - Flag "tdd" referenced in step condition but not declared in defaults or flags
  - Phase "implment" referenced in requires but does not exist
Aborting workflow. Fix schema and retry.
```

### Priority
- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 3.0**

---

## conditional-step-dependencies

**Conditional Step Dependencies** (score 2.8)

### Idea
Add a `depends_on` field to step entries in schemas so steps can declare explicit dependencies on other steps' outputs. Currently, step ordering is purely positional (list order in the phase). This works but creates implicit coupling -- if someone reorders steps in a schema, they might break an input dependency (e.g., `create-or-refresh-artifacts` depends on `explore` having produced `discovery.md`). Adding `depends_on: [explore]` makes this explicit. The grammar already has `requires` at the phase level and `requires` on output artifacts -- this extends the pattern to steps.

### Why Now
As the number of schemas grows and `/learn` and `/workflow-improve` modify schemas automatically, positional ordering becomes fragile. The grammar file is the right place to formalize this, and the dispatch loop already reads step contracts sequentially -- adding a dependency check before execution is straightforward.

### Prototype
Grammar extension:
```yaml
step_entry:
  forms:
    - pattern:
        id: string
        depends_on: list<string>   # Step IDs that must have completed first
```

Schema usage:
```yaml
steps:
  - load-project-context
  - explore
  - design-exploration if design
  - id: create-or-refresh-artifacts
    depends_on: [explore]          # explicit: needs discovery.md
```

### Priority
- User value: 5/10
- Strategic fit: 6/10
- Technical leverage: 6/10
- Effort: medium
- **Score: 2.8**

---

# Bugs

## fix-missing-step-contracts

**Fix missing step contracts (ISSUE-18)** (score 7.8)

### Idea
workflow-init validates every schema-declared step against `$ORCHESTRATOR_HOME/config/steps/<id>.yaml` at workflow start. Missing contracts get pre-filtered into `workflow_plan.<phase>.filtered` with reason `"contract file missing (no config/steps/<id>.yaml)"` and a single WARNING is emitted. Never fails init. A stricter sibling: `orchestrator doctor` lists orphan schema refs as a deep-check item.

### Why Now
The bugfix schema declares `run-simplify` and `run-feature-verification` but neither contract file exists. Autopilot run 2026-04-19-003 hit this twice, once per phase. Each hit required a manual edit to state.yaml to move the missing step to the filtered list — otherwise `orchestrator next` errors out mid-dispatch. Happens on every bugfix run.

### Prototype
```
workflow-init start
  schema: bugfix
  declared steps: 13
  resolved contracts: 11
  [WARN] 2 steps declared without contracts — pre-filtered
    - run-simplify (reason: no config/steps/run-simplify.yaml)
    - run-feature-verification (reason: no config/steps/run-feature-verification.yaml)
  workflow_plan written
```

### Priority
- User value: 8/10 (every bugfix run currently needs a manual workaround)
- Strategic fit: 8/10 (infrastructure hygiene; fits the doctor/validate theme)
- Technical leverage: 7/10 (tiny change, ripple benefit across all schemas)
- Effort: small
- **Score: 7.8**

### Source
spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-18

---

## self-referential-bug-bootstrap

**Self-referential bugfix bootstrap (ISSUE-19)** (score 6.3)

### Idea
When a bugfix's change_description references files under `config/scripts/orchestrator_next/` or other dispatcher-critical paths, the dispatcher can't rely on its own current behavior during the fix. Two options:

1. **Schema hint**: bugfix schema detects self-referential changes (grep change_description for paths under `config/scripts/` or `bin/orchestrator`). fix-plan.md gains a "Bootstrap Constraint" section the driver reads, warning which workarounds to apply mid-run.

2. **Step marker**: allow `repeat_until` (or a new `bootstrap_before`) on a step to apply the fix from the working tree before running the step — i.e., re-import record.py after T-2 lands but before T-3 runs. Risky; prefer (1).

### Why Now
`live-telemetry-and-repeat-until-enforcement` fixed ISSUE-16 (dispatcher ignoring repeat_until) but had to work around ISSUE-16 during its own execution. The driver manually re-pointed `next_step.step_id = execute-next-task` between each of T-2..T-6, then demoted T-1's status from completed to in_progress so the dispatcher didn't treat it as done. Six manual state edits that should be automated or avoided.

### Prototype
```
### Bootstrap Constraint
This feature modifies `config/scripts/orchestrator_next/record.py`.
During implement phase, the dispatcher runs the PRE-fix version of record.py
until the workflow run is complete. Driver workarounds:
  - After each execute-next-task, re-point next_step.step_id = execute-next-task
    until tasks.md has no `- [ ]` lines.
  - Group dependency chains into single spawns to minimize re-point operations.
  - Do not call `orchestrator record` between tasks within a chain.
```

### Priority
- User value: 6/10 (rare but painful when hit)
- Strategic fit: 7/10 (infra self-improvement)
- Technical leverage: 6/10
- Effort: small
- **Score: 6.3**

### Source
spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-19

---

## pricing-date-suffix-lookup

**Pricing lookup tolerant of model date-suffixes (ISSUE-23)** (score 5.8)

### Idea
Anthropic returns model IDs with date suffixes in JSONLs (e.g. `claude-haiku-4-5-20251001`, future: `claude-sonnet-4-7-20260315`). `config/pricing.yaml` lists unstamped keys. Today the lookup misses, falls through to the `default` block (opus-tier), and overstates cost ~4× for haiku and ~5× for sonnet.

Two clean options:

1. **Strip date suffix in `_compute_cost_usd`** — regex `-\d{8}$` before the pricing lookup. One-line change. Covers all current and future Anthropic dated IDs.
2. **`aliases:` block in pricing.yaml** — explicit mapping `claude-haiku-4-5-20251001: claude-haiku-4-5`. More explicit but requires pricing.yaml edit every time Anthropic ships a dated alias.

Recommended: option 1. Simpler, future-proof, zero maintenance.

### Why Now
Already partially fixed for claude-haiku-4-5-20251001 via explicit alias in 190df05, but the pattern will recur for every future dated release. A 1-line regex strip prevents the next five instances of this bug.

### Prototype
```python
# in _compute_cost_usd, before the pricing.models lookup:
import re
base_model = re.sub(r'-\d{8}$', '', model_id)
price = (pricing.get("models") or {}).get(model_id) or \
        (pricing.get("models") or {}).get(base_model)
```

### Priority
- User value: 4/10
- Strategic fit: 6/10 (pricing-accuracy hygiene)
- Technical leverage: 9/10 (one line, permanent fix)
- Effort: XS
- **Score: 5.8**

### Source
spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-23

---

