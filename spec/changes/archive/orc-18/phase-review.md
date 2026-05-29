# Phase Review — orc-18 (implement / main)

**Schema:** feature
**Phase:** main (implement)
**Reviewer:** reviewer
**Attempt:** 1
**Verdict:** needs_work

## Summary

Doctor implementation is largely complete: the `orchestrator_next.doctor` module exposes 12 checks, a `/doctor` slash command, `make doctor`, and dispatch hardening (`ContractDispatchError` + agent-definition guard + state.yaml write-after-verify). The full pytest suite passes (679 passed, 2 skipped, 1 xfailed) and the surface-wiring shell test passes. However two structural defects must be resolved before this phase can close.

1. **Producer artifacts are missing.** `design-and-draft-artifacts` step_history records `outputs.design.md = spec/changes/orc-18/design.md` and `outputs.tasks.yaml = spec/changes/orc-18/tasks.yaml`, but neither file exists on disk in this worktree (only `discovery.md` and `state.yaml` are present). The implement-phase tasks T-1..T-6 ran without a written design or task contract; the run-phase-review step has no design.md to verify ACs against, and `orchestrator expand-plan` cannot inject fix tasks because `tasks.yaml` is absent (`expand_plan.py:124` errors on missing path).
2. **Two ACs from the ticket are not satisfied by the implementation.** AC-1 (explicit existence rows for `spec/project.yaml`, `install.sh`, `agents/`, `skills/`) and AC-4 (orphaned state.yaml whose worktree dir is missing) are not represented in `run_all()`.

Because the standard fix-task-injection path (`tasks.yaml` → `expand-plan`) is blocked by finding 1, this review returns `needs_work` and flags the blocker for user / architect attention. Fix tasks below are recorded for tracking; they cannot be injected into the DAG until `tasks.yaml` is restored.

## Verify commands

| Command | Exit | Result |
|---|---|---|
| `python3 -m pytest config/scripts/orchestrator_next/tests/test_doctor_graph.py config/scripts/orchestrator_next/tests/test_dispatch_guards.py config/scripts/orchestrator_next/tests/test_doctor.py -q` | 0 | 38 passed |
| `python3 -m pytest config/scripts/orchestrator_next/tests/ -q` | 0 | 679 passed, 2 skipped, 1 xfailed |
| `bash tests/test_doctor_surface.sh` | 0 | 6/6 surface checks pass |
| `python3 -m orchestrator_next.doctor` (ORCHESTRATOR_HOME=repo_root) | 2 | 12 rows produced; FAILs reflect host state, not orc-18 work |

## Acceptance criteria verification

Source: `/Users/spidey/.config/backlog/workspaces/orchestrator/tasks/orc-18 - Deep-Doctor-Health-Check.md` (no design.md available; ticket ACs are the contract).

| # | AC | Result | Evidence |
|---|---|---|---|
| 1 | Existence checks for `spec/project.yaml`, `install.sh`, `config/workflows`, `config/steps`, `agents/`, `skills/` | **FAIL (partial)** | `doctor.py` exposes no row for `spec/project.yaml`, `install.sh`, `agents/`, or `skills/`. `config/steps` is implicit via `check_contracts`/`check_inline_scripts`; `config/workflows` is implicit via `check_schema_step_graph`. Only 2 of 6 listed targets are covered, and never as discrete `[OK]` rows the user can read. |
| 2 | Symlinks valid (catches make-setup-from-worktree) | PASS | `check_symlinks` walks both repo_root and ORCHESTRATOR_HOME, reports broken links. |
| 3 | ORCHESTRATOR_HOME matches install symlink | PASS | `check_orchestrator_home` resolves `~/.config/orchestrator` and FAILs on mismatch. |
| 4 | No orphaned state.yaml (active changes whose worktree dir is missing) | **FAIL** | `check_active_vs_archive` checks active-id substring vs archive names — that is a *different* check. There is no check that opens each active `~/.workflows/*/state.yaml`, reads `worktree_path`, and FAILs/WARNs if that dir does not exist. (Makefile `stale` target does a stat-based age check, not the orphan check, and is not invoked by `doctor`.) |
| 5 | Schema → step graph: every step ID in workflows resolves to a contract | PASS | `check_schema_step_graph`, override-aware via `_resolve_artifact("steps", …)`. |
| 6 | Agent graph: every contract's `agent:` resolves to an agent `.md` | PASS (downgraded to WARN) | `check_agent_files` searches `$ORCHESTRATOR_HOME/agents/` and `~/.claude/agents/`. The ticket says "resolves" — WARN is acceptable given `check_orchestrator_home` would FAIL upstream on the canonical break-cause. |
| 7 | Flag graph: every `flags_read` entry exists in flags registry | PASS | `check_contract_flag_graph` loads `config/flags.yaml` `gates`+`behavioral` keys and reports unknown names. |
| 8 | Template graph: every template path exists on disk | PASS | `check_contract_template_graph` resolves entries from `template_paths`. |
| 9 | Dispatch hardening: file-not-found guards before reads; state.yaml write-after-verify | PASS | `dispatch.py:30` defines `ContractDispatchError`; `_load_step_contract` wraps `FileNotFoundError`; `_resolve_allowed_tools` guards on `_agent_definition_path`. `record.py:1485-1500` verifies YAML pre/post-write and restores bytes on parse failure. |
| 10 | `/doctor` slash command + consolidated report with per-category status | PASS | `skills/doctor/SKILL.md` invokes the same entry point as `make doctor`; `_format_table` renders one consolidated 3-column table. |
| 11 | Non-zero on FAIL, zero on WARN-only | PASS | `run_all` returns `2` if any `FAIL`, else `0`. Aligned with `check_state_valid`/etc. statuses. |

## Findings

### Critical

- **C1 — Missing producer artifacts (design.md, tasks.yaml).**
  - Scope: `spec/changes/orc-18/`. `state.yaml.step_history[design-and-draft-artifacts].evidence.outputs` references both files; neither exists.
  - Impact: blocks AC traceability (no UC→AC mapping), blocks fix-task injection via `expand-plan` (requires `tasks.yaml`), violates the Design Format Contract and Tasks YAML Format Contract.
  - This is upstream of the implement phase but discovered here because no design.md was available for AC verification. Escalate to architect / user — reviewer should not regenerate the design retroactively.

### Important

- **F1 — AC-1: doctor lacks discrete existence rows for `spec/project.yaml`, `install.sh`, `agents/`, `skills/`.**
  - Scope: `config/scripts/orchestrator_next/doctor.py` `run_all`.
  - Approach: add `check_required_paths(orch_home)` that probes `spec/project.yaml`, `install.sh`, `config/workflows` (dir), `config/steps` (dir), `agents/` (dir), `skills/` (dir) and emits one row per target with FAIL if missing. Insert at the top of `run_all` so the table reads top-down per the prototype in the ticket.

- **F2 — AC-4: no orphan check for active state.yaml files whose worktree dir is missing.**
  - Scope: `config/scripts/orchestrator_next/doctor.py` `run_all`.
  - Approach: add `check_orphan_states()` that iterates `~/.workflows/*/state.yaml`, loads each via `_parser.load_state`, reads `state.worktree_path` (or falls back to the active change dir), and WARNs/FAILs for each entry where the worktree dir does not exist. Separate from `check_active_vs_archive`, which keeps its substring semantics.

## Quarantined tasks

None. `state.yaml` has no `quarantine_events`.

## Baseline comparison

Skipped — archived `state.yaml` files in `spec/changes/archive/` predominantly lack a `metrics.review_score_avg` entry, so no representative historical average is available.

## Score

| Dimension | Score | Notes |
|---|---|---|
| spec_compliance | 5 | Critical finding C1 (missing producer artifacts) caps the dimension. |
| correctness | 9 | Implementation passes all tests; dispatch and doctor work end-to-end. |
| security | 9 | No new attack surface; YAML loading uses `safe_load`. |
| simplicity | 9 | Each check is a single function; `run_all` is a flat list. |
| code_quality | 8 | Two stylistic notes (non-blocking): (a) module-level `import` statement appears below another `import` block (`dispatch.py:50` `from orchestrator_next import resolver` follows the new class definition) — harmless but unusual; (b) `_doctor_main` exit 3 for unset env is undocumented in the SKILL invocation table (SKILL says 0/2/3 — actually documented). |

**Overall: 5** (= min of dimensions; the +1 first-pass bonus is not applicable since C1 blocks the phase).

## Fix tasks (for tracking; cannot be injected until tasks.yaml is restored)

```yaml
- id: fix-1
  title: "Restore design.md and tasks.yaml for orc-18 (architect)"
  files:
    - spec/changes/orc-18/design.md
    - spec/changes/orc-18/tasks.yaml
  depends_on: [task-T-6]
  verify:
    - test -s spec/changes/orc-18/design.md
    - test -s spec/changes/orc-18/tasks.yaml

- id: fix-2
  title: "Add discrete existence-check rows in doctor.run_all (AC-1)"
  files:
    - config/scripts/orchestrator_next/doctor.py
    - config/scripts/orchestrator_next/tests/test_doctor.py
  depends_on: [fix-1]
  verify:
    - python3 -m pytest config/scripts/orchestrator_next/tests/test_doctor.py -q

- id: fix-3
  title: "Add orphan-state check for missing worktrees (AC-4)"
  files:
    - config/scripts/orchestrator_next/doctor.py
    - config/scripts/orchestrator_next/tests/test_doctor.py
  depends_on: [fix-2]
  verify:
    - python3 -m pytest config/scripts/orchestrator_next/tests/test_doctor.py -q
```

## Verdict

`needs_work`. Two ACs unsatisfied (AC-1, AC-4); producer artifacts missing (C1).
