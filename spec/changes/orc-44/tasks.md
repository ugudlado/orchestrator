# Tasks — ORC-44: Standardize tool_calls key naming

- [x] T-1: Write regression test that fails with `tools:` key and passes with `tool_calls:`
  Verify: `python -m pytest config/scripts/tests/test_tool_calls_key_conventions.py -v` → FAILED (test exists, upsert returns 0 rows for `tools:` key)

- [x] T-2: Rename `tools:` → `tool_calls:` in CONVENTIONS.md and state-with-tools.yaml fixture
  Verify: `grep -n 'tools:' config/steps/CONVENTIONS.md | grep -v 'tool_calls\|allowed_tools'` → zero matches; `grep -n '      tools:' config/scripts/test-fixtures/state-with-tools.yaml` → zero matches
  depends: T-1

- [x] T-3: Confirm regression test passes and full suite is green
  Verify: `python -m pytest config/scripts/tests/test_tool_calls_key_conventions.py -v` → PASSED; `python -m pytest config/scripts/ -q` → 0 failures
  depends: T-2
