---
feature-id: orc-107
linear-ticket: ORC-107
---

# Discovery Brief: Fix `_WORKTREE_ROOT` path resolution in tests/

## Feature Summary

42 of 77 tests in `tests/` fail because each test file independently computes `_WORKTREE_ROOT` as three directory levels above `tests/`, which resolves to `/Users/spidey/code` instead of the repo root `/Users/spidey/code/feature_worktrees/orc-107`. This broken path was written when tests lived at `config/scripts/orchestrator_next/tests/` (3 levels deep); ORC-106 moved them to `tests/` (1 level deep) but left the path depth unchanged. The fix introduces a single `conftest.py` that exports a canonical `ORCHESTRATOR_ROOT` constant, eliminating the per-file layout assumption across 11 affected files.

## Personas & Actors

- **Developer running `pytest tests/`** — expects the test suite to be a reliable signal; currently 42/77 tests are false failures caused purely by path resolution.
- **CI pipeline** — runs `pytest tests/` on every commit; currently produces noisy, untrustworthy results.
- **Future contributor** — adds a new test file; must not accidentally re-encode the wrong path depth.

## Use Cases

### Happy Path

UC-1: Full suite pass — a developer runs `pytest tests/` from the repo root and all tests that are not pre-existing failures pass, with `bin/orchestrator` resolved correctly to the repo root.
UC-2: Worktree invocation — a developer runs `pytest tests/` from inside a git worktree at `~/code/feature_worktrees/<slug>/` and the root resolves to the worktree root, not the canonical repo root, which is also correct for that invocation context.

### Error & Edge Cases

UC-E1: Missing `bin/orchestrator` — if the repo is in an unexpected location, the conftest-exported root still correctly points one level above `tests/`; the test fails with a clear "file not found" error rather than a cryptic wrong-path error pointing at `/Users/spidey/bin/orchestrator`.
UC-E2: Stale per-file `_WORKTREE_ROOT` override — if a test file defines its own `_WORKTREE_ROOT` after the fix is applied, it silently wins over the conftest fixture. The fix must either (a) remove all per-file definitions or (b) have conftest define a module-level constant imported by each file, not a pytest fixture, to avoid this shadowing risk.

## Scope

### In Scope

- Add `tests/conftest.py` with a canonical `ORCHESTRATOR_ROOT` constant pointing one level above `tests/`.
- Update all 11 test files that define `_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))` to use the canonical constant instead.
- Verify: `pytest tests/` passes with zero path-resolution failures.

### Out of Scope

- ORC-69: 5 subprocess isolation failures are a separate bug and must not be conflated with this fix.
- Refactoring test logic, fixtures, or golden files beyond the path change.
- Moving or reorganizing `tests/` further; this fix accepts the current layout.
- CI configuration changes (the fix is entirely in the test code).

## UI Direction

N/A — no UI components.

## Key Decisions

- **conftest.py over per-file fix**: A single `conftest.py` is the canonical pytest mechanism for shared test infrastructure. Updating 11 files individually creates 11 new places that can drift in future migrations. Selected: conftest.py with module-level constant.
- **Module-level constant, not pytest fixture**: `_WORKTREE_ROOT` is used at module scope (lines 5–8 in each file, before any test class). A pytest fixture is only available inside test functions and cannot satisfy module-level assignments. The conftest defines `ORCHESTRATOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` — one level above `tests/` — and all 11 files import it directly, eliminating their per-file definitions.
- **Per-file definitions removed (not shadowed)**: Each test file's `_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..', '..'))` line is deleted entirely. Leaving it in place would silently override the conftest constant (UC-E2 risk).
- **Build or reuse**: This is a fix to existing test infrastructure; no new module is being built. The conftest pattern is idiomatic pytest — no third-party library is needed.

## Open Questions

- OQ-1: Should the conftest expose `ORCHESTRATOR_ROOT` as a module-level constant (importable) or as a session-scoped pytest fixture? The former is simpler given the usage pattern (module-level assignments), but the latter is more pytest-idiomatic for test setup. The ticket favors a fixture — but module-level `_WORKTREE_ROOT` bindings will not see a fixture value. This needs a concrete decision before implementation.
- OQ-2: Are there any bats/shell tests in `tests/bats/` or `tests/__tests__/` that encode the same wrong path depth? A quick grep is needed to confirm scope is limited to the 11 Python files.
