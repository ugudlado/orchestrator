## Phase Review: ORC-76 — Step-as-directory + typed file I/O

| Dimension | Score | Key Findings |
|-----------|-------|--------------|
| Spec Compliance | 9/10 | All 11 ACs verified. Mixed-list in design-and-draft-artifacts (legacy + typed entry) is intentional back-compat, not a defect. |
| Correctness | 9/10 | Parser, dispatch, record.py typed I/O logic correct. `<slug>` substitution, file existence checks, optional flag all implemented per design. 3 pre-existing failures (symlink parity + estimate-cost baseline) are unrelated to ORC-76. |
| Security | 10/10 | No new attack surface. File paths joined via `os.path.join` (no string concatenation exploits). No user-controlled input reaches exec without prior validation. |
| Simplicity | 9/10 | Directory layout migration is mechanical and clean. Typed I/O adds ~100 LOC to parser/dispatch/record; commensurate with scope. Back-compat branch is well-bounded and documented. |
| Code Quality | 9/10 | Clear separation of concerns (parser owns schema load, dispatch owns pre-check, record owns post-check). TDD followed: every implementation task preceded by a failing test. New helper `typed_input_paths` on StepContract is appropriately minimal. |
| **Overall** | **9/10** | |

---

### Verification

**Type-check:** N/A — project uses pytest, no separate type-check command in project.yaml.

**Tests:** 646 passed, 3 pre-existing failures (unrelated to ORC-76):
- `test_complete_workflow_contract.py::test_repo_and_home_step_dirs_are_the_same_tree` — worktree symlink parity, fails because `$ORCHESTRATOR_HOME` points to main repo, not worktree.
- `test_flags_reshape.py::test_repo_and_home_flags_are_the_same_file` — same symlink parity issue.
- `test_estimate_cost_sh.py::test_rewrite_output_matches_baseline_shape` — baseline mismatch, pre-existing.

**ORC-76 new tests:** 69 tests, all pass (parser_directory_layout, parser_typed_io, dispatch_typed_inputs, record_typed_outputs, generate_plan_directory_layout, orchestrator_run_path, migrated_steps_endtoend, endtoend_migrated_workflow, record_append_retro_path).

**Build:** No build step — Python scripts, no compile step.

---

### Acceptance Criteria Verification

| AC | Criterion | Status | Evidence |
|----|-----------|--------|----------|
| AC-1 | `_load_contract` returns `kind == "agent"`, `instruction == prompt.md contents` for directory form | PASS | `test_parser_directory_layout.py` 26 tests pass; `config/steps/explore/contract.yaml` has `kind: agent`, `prompt.md` exists and non-empty |
| AC-2 | Script step: `action["run"]` resolves to `contract_dir/script.sh`, file is executable | PASS | `test_orchestrator_run_path.py` passes; `config/steps/expand-plan/contract.yaml` has `kind: script, run: script.sh`; `config/scripts/inline/expand-plan.sh` removed |
| AC-3 | Missing typed input → exit 2 with path in diagnostic | PASS | `test_dispatch_typed_inputs.py`, `test_endtoend_migrated_workflow.py::TestPreStepMissingTypedInput` pass |
| AC-4 | Typed output file absent → record exits 3, `reason: missing_outputs` | PASS | `test_record_typed_outputs.py`, `test_endtoend_migrated_workflow.py::TestPostStepMissingTypedOutput` pass |
| AC-5 | Learn cycle writes rules to `contract.yaml`, not `prompt.md` | PASS | Rules in all `contract.yaml` files; `prompt.md` carries instruction prose only |
| AC-6 | Missing payload raises appropriate error | PASS | `test_parser_directory_layout.py` covers both `kind: agent` missing prompt.md and `kind: script` missing script.sh |
| AC-7 | Flat-file back-compat: legacy `<id>.yaml` loads, `kind` synthesized | PASS | Back-compat path in `parser._load_contract` tested by `test_parser_directory_layout.py` |
| AC-8 | Migrated scripts in `config/steps/<id>/script.sh`; no stale copies in `inline/` | PASS | All 8 script steps verified; all stale inline copies confirmed absent |
| AC-9 | `step_context.inputs.discovery` carries resolved abs path | PASS | `test_dispatch_typed_inputs.py` verifies name→abs_path in returned dict |
| AC-10 | `artifact-formats.md` deleted; format contracts in producer prompts + reviewer prompt | PASS | `! test -e config/steps/contracts/artifact-formats.md` confirmed; grep checks on all 4 producer prompts pass |
| AC-11 | `append-retro.sh` path uses `config/scripts/inline/` prefix | PASS | `test_record_append_retro_path.py` 2/2 pass; record.py line 1695 uses correct path |

---

### Task Node Completeness

All 25 task nodes (task-T-1 through task-T-25) have `status: completed` in `state.yaml`. No pending task-nodes.

---

### Findings

**No critical or blocking issues found.**

**Minor observations (non-blocking):**

1. `design-and-draft-artifacts/contract.yaml` has a mixed inputs list — one legacy string `discovery_result` and one typed `{name: discovery, path: spec/changes/<slug>/discovery.md}`. This is intentional for backward compat but means the same artifact is effectively declared twice. The design explicitly documents this mixed-list pattern as valid, and dispatch correctly handles it via `typed_names` set exclusion for the legacy walk. Not a defect; worth cleaning up in the follow-up cycle when flat-file back-compat is removed.

2. The `run-phase-review` contract marks `design.md` and `tasks.yaml` typed inputs as `optional: true`. This is appropriate — phase review can run even if those files don't yet exist (e.g., first phase). The current run demonstrates this correctly.

3. Three pre-existing test failures (`test_repo_and_home_step_dirs_are_the_same_tree`, `test_repo_and_home_flags_are_the_same_file`, `test_rewrite_output_matches_baseline_shape`) are all worktree-context or baseline issues predating this feature. They do not regress any ORC-76 functionality.

---

### Verdict: PASS

Score 9/10 meets the `min_phase_review_score: 9` threshold. All 25 task nodes completed. All 11 ACs verified. 646 tests pass (3 pre-existing failures unrelated to this feature). Implementation follows design intent throughout.
