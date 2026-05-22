# Tasks — Unify phase-opening artifact: discovery.md for both explore and diagnose

- [x] T-1: Add regression test for the feature-schema dispatch pre-check
  Why: AC-1, AC-6 — codify the bug as an automated test that fails on HEAD (exit 2) and passes after the fix (exit 0). Bugfix rule: regression test before the fix.
  Files: config/scripts/orchestrator_next/tests/test_orc78_discovery_input_unification.py (new)
  Change: Turn the runnable reproduction in diagnose.md into a pytest. The test builds a temp step-contracts dir via ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE with `explore` emitting `discovery_result` and `design-and-draft-artifacts` declaring `inputs: [discovery_result]` (the post-fix contract), constructs a feature-schema state where `explore` is completed with `evidence.outputs.discovery_result`, calls `dispatch()`, and asserts exit code 0. On HEAD the real `config/steps/design-and-draft-artifacts.yaml` still says `diagnosis_result`; the test must read the real contract (no override for design-and-draft-artifacts) so it FAILS with exit 2 on HEAD and PASSES after T-2. Mirror the existing pattern in test_record_agent_field.py for state/contract setup.
  Test scenarios:
    - Feature schema: explore completed → discovery_result resolvable → dispatch returns exit 0
    - On HEAD (design-and-draft-artifacts still declares diagnosis_result): dispatch returns exit 2
    - Bugfix schema: diagnose completed → discovery_result resolvable → dispatch returns exit 0

- [x] T-2: Rename the phase-opening artifact contract to discovery_result / discovery.md (atomic)
  Why: AC-2, AC-3, AC-4, AC-6 — the contract triple must move together or one schema breaks mid-change.
  Files: config/steps/diagnose.yaml, config/steps/design-and-draft-artifacts.yaml, config/templates/bugfix/diagnosis.md → config/templates/bugfix/discovery.md
  Change: In config/steps/diagnose.yaml — rename `outputs: [diagnosis_result]` to `[discovery_result]` (line 72), and replace `diagnose.md` with `discovery.md` and `diagnosis_result` with `discovery_result` in instruction/verify/COMPLETION text (lines 51, 61, 65). In config/steps/design-and-draft-artifacts.yaml — change `inputs: [diagnosis_result]` to `[discovery_result]` (line 12). `git mv config/templates/bugfix/diagnosis.md config/templates/bugfix/discovery.md` (content unchanged — the `# Diagnosis: {title}` heading is structural). Do NOT modify dispatch.py. Confirms T-1 now passes.
  Test scenarios:
    - T-1 regression test now passes (exit 0 for feature schema)
    - diagnose.yaml contains no `diagnosis_result` / `diagnose.md` tokens
    - config/templates/bugfix/discovery.md exists; diagnosis.md does not
  depends: T-1

- [x] T-3: Update documentation and prose callsites to discovery.md
  Why: AC-5 — remove all stale `diagnose.md` / `diagnosis.md` filename references in docs, contracts, skills, and the agent prompt.
  Files: config/steps/contracts/artifact-formats.md, config/steps/CONVENTIONS.md, config/steps/execute-next-task.yaml, config/templates/bugfix/fix-plan.md, config/templates/bugfix/tasks.md, skills/systematic-debugging/SKILL.md, skills/linear/SKILL.md, agents/discoverer.md
  Change: artifact-formats.md lines 269, 363, 396, 405 — `diagnosis.md` → `discovery.md` (leave the section heading "Diagnosis Format Contract" and the in-fence template heading on line 276 untouched). CONVENTIONS.md lines 264, 275 — `diagnose.md` → `discovery.md`. execute-next-task.yaml line 30 — `diagnosis.md` → `discovery.md`. templates/bugfix/fix-plan.md line 6 and templates/bugfix/tasks.md line 9 — `diagnosis.md` → `discovery.md` (filename token only; do not restructure the task list). systematic-debugging/SKILL.md lines 16, 72, 81 — `diagnosis.md` → `discovery.md`. linear/SKILL.md line 69 — `diagnose.md` → `discovery.md`. discoverer.md lines 121–131 — `diagnose.md` → `discovery.md`, `diagnosis_result` → `discovery_result`.
  Test scenarios:
    - No functional `diagnosis.md` / `diagnose.md` filename reference remains in the listed files
    - agents/discoverer.md COMPLETION block declares `discovery_result: {path: "discovery.md"}`
  depends: T-2

- [x] T-4: Update test fixtures to discovery_result / discovery.md
  Why: AC-7 — keep the existing test suite green after the rename.
  Files: config/scripts/orchestrator_next/tests/test_record_agent_field.py, config/tests/test-archive-merges-worktree-artifacts.sh
  Change: test_record_agent_field.py lines 136, 172, 225, 264 — `"outputs": {"diagnosis_result": "diagnose.md"}` → `{"discovery_result": "discovery.md"}`. test-archive-merges-worktree-artifacts.sh lines 25, 44 — create and check `discovery.md` instead of `diagnose.md`.
  Test scenarios:
    - test_record_agent_field.py passes with the updated fixtures
    - test-archive-merges-worktree-artifacts.sh passes with discovery.md
  depends: T-2

- [x] T-5: Verify full suite green and zero stale references
  Why: AC-5, AC-7 — confirm the rename is complete and nothing regressed.
  Files: (verification only — no edits)
  Change: Run the orchestrator_next pytest suite and the shell tests under config/tests/; assert zero new failures. Run `grep -rn "diagnosis_result\|diagnose\.md" config/ skills/ agents/` and confirm zero functional matches (only the artifact-formats.md "Diagnosis Format Contract" heading and in-fence template heading may remain). If verification surfaces a missed callsite, add a follow-up task before proceeding.
  Test scenarios:
    - Full pytest + shell test suite passes, zero new failures
    - grep for `diagnosis_result` / `diagnose.md` returns no functional matches
  depends: T-3, T-4

- [x] T-6: Remove the out-of-scope engine change from the bugfix branch
  Files: config/scripts/orchestrator_next/dispatch.py, config/scripts/orchestrator_next/readiness.py, config/scripts/orchestrator_next/tests/test_readiness.py, config/scripts/orchestrator_next/tests/test_orc36_path_consolidation.py
  Problem: Commit c494287 ("fix: legacy active-plan readiness and repeat_until redispatch") bundles an independent engine change onto bugfix/orc-78 — it adds `_uses_legacy_active_plan`, `_step_completed_in_history`, `_effective_node_status`, and `repeat_until_redispatch` to readiness.py, rewires dispatch.py, and adds 2 readiness tests plus a git-init removal in test_orc36_path_consolidation.py. None of this traces to a task (T-1..T-5) or an AC. design.md Non-Goals explicitly says "No engine change — dispatch.py / _check_required_inputs is not modified." Git history confirms it was not needed: baseline 28dd8a0 had only the ORC-78 regression test failing, and the core fix 2154d68 alone takes the suite to 0 failures.
  Why: A bugfix branch must carry only its diagnosed fix. Bundling unrelated engine logic violates the design's Non-Goals and project.yaml rule `minimal-diffs`, makes the bugfix's blast radius non-auditable, and lands an engine change with no design or AC trace.
  Improve: Pick ONE — (A) revert c494287 from bugfix/orc-78 (`git revert c494287` or rebase it out) and re-land the legacy active-plan readiness work under its own ticket; OR (B) if the engine change is intentionally in scope, amend design.md to add "legacy active-plan readiness + repeat_until redispatch" as an explicit in-scope item with rationale, add a corresponding task/AC, and re-run review. Do not refactor; this is scope correction only.
  Verify: Option A — `git diff 28dd8a0..HEAD -- config/scripts/orchestrator_next/dispatch.py config/scripts/orchestrator_next/readiness.py` is empty, and `git log --oneline 28dd8a0..HEAD` shows only ORC-78-scoped commits; pytest still 0 failures. Option B — design.md Non-Goals updated, new AC present, `pytest config/scripts/orchestrator_next/tests/ -q` green.

<!-- Status markers: [ ] pending, [x] done -->
