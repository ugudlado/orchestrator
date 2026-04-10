# Tasks: {title}

- [ ] T-1 Write tests: {component} (RED — tests must fail)
  - **Why**: {which spec requirement this satisfies}
  - **Verify**: Tests run and FAIL (red) for the right reason
- [ ] T-2 Implement: {component} (GREEN — make tests pass) (depends: T-1)
  - **Why**: {which spec requirement this satisfies}
  - **Verify**: All T-1 tests pass (green), type-check clean
- [ ] T-3 Refactor: {component} (REFACTOR — clean up) (depends: T-2)
  - **Why**: Code quality — simplify without changing behavior
  - **Verify**: All tests still pass, no new warnings
- [ ] T-4 Review checkpoint (phase gate)
  - **Verify**: type-check + test (coverage >= 90%) + build all pass

- [ ] T-5 Write tests: {component} (RED) (depends: T-2)
  - **Why**: {requirement}
  - **Verify**: Tests fail (red)
- [ ] T-6 Implement: {component} (GREEN) (depends: T-5)
  - **Why**: {requirement}
  - **Verify**: Tests pass (green), type-check clean
- [ ] T-7 Review checkpoint (phase gate)
  - **Verify**: type-check + test (coverage >= 90%) + build all pass

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- Coverage target: >= 90% at each phase gate -->

<!-- VERIFICATION BUGS: If verification reveals new issues, add them as tasks -->
<!-- before proceeding. Do NOT skip ahead. -->
<!-- Example: -->
<!-- - [ ] T-6b Fix: {bug found during T-6 verification} (depends: T-6) -->
<!--   - **Why**: Found during verification — {description} -->
<!--   - **Verify**: Original test + new regression test pass -->
