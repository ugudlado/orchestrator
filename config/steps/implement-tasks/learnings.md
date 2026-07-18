# Learnings

- In Bun TDD workflows, RED tasks (failing tests for a later GREEN task) must use `test.todo()` — never `test.skip()` or plain `test()` — so the suite stays green until the GREEN task flips them to `test()`. Write the assertion bodies out inside the todo so GREEN is a mechanical flip. <!-- learned: 2026-07-18, source: BKG-423/BKG-549, cycle: 0 -->
- After authoring or modifying tests, always run the suite and report its actual output before marking the task complete — a claimed-green suite without a run is unverified. <!-- learned: 2026-07-18, source: BKG-423, cycle: 0 -->
