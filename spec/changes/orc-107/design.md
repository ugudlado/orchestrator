---
feature-id: orc-107
linear-ticket: ORC-107
---

# Design: Fix `_WORKTREE_ROOT` path resolution in tests/

## Context

42 of 77 tests in `tests/` fail because each file independently computes `_WORKTREE_ROOT` as three directory levels above `tests/`. This was correct when tests lived at `config/scripts/orchestrator_next/tests/` (3 levels deep from repo root), but ORC-106 moved them to `tests/` (1 level deep). The stale depth resolves to `/Users/spidey/code` instead of the repo root, causing `bin/orchestrator` lookups to fail with a path like `/Users/spidey/bin/orchestrator`.

There are 11 Python files in `tests/` each independently encoding the wrong depth — any future directory migration would repeat this failure.

## Goals / Non-Goals

### Goals

- Eliminate all 42 path-resolution failures with a single canonical fix.
- Ensure `bin/orchestrator` resolves correctly from `tests/` in both direct and worktree invocations.
- Leave no per-file `_WORKTREE_ROOT` definitions that could silently override the fix.

### Non-Goals

- Fixing ORC-69 (5 pre-existing subprocess isolation failures — separate bug).
- Refactoring test logic, fixtures, or golden files beyond the path correction.
- Moving or reorganizing `tests/` further.
- CI configuration changes.

## Approaches Considered

### Approach 1: conftest.py with module-level constant (XS)

Add `tests/conftest.py` defining `ORCHESTRATOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`. Each of the 11 files removes its `_WORKTREE_ROOT` local definition and imports `ORCHESTRATOR_ROOT` from `conftest`.

**Pros:** Single fix point; future test files naturally import from conftest; UC-E2 shadowing risk is eliminated by removing per-file definitions; no library needed.

**Cons:** `from conftest import ORCHESTRATOR_ROOT` is slightly unusual (conftest is typically implicit), but works because pytest adds conftest to `sys.path`.

### Approach 2: Per-file depth fix (S)

Change `'..', '..', '..'` → `'..', '..'` in all 11 test files without introducing conftest.

**Pros:** No new file, no import, minimal mechanical change.

**Cons:** 11 separate diffs; any future directory move repeats this exact mistake; no central enforcement; UC-E2 risk remains if any file re-encodes the wrong depth later.

### Approach 3: Session-scoped pytest fixture (M)

Define `@pytest.fixture(scope="session") def orchestrator_root()` in conftest.py.

**Cons:** pytest fixtures are only injectable into test functions — they cannot satisfy module-level assignments like `_BIN_ORCHESTRATOR = os.path.join(_WORKTREE_ROOT, "bin", "orchestrator")` at import time. This approach cannot solve the problem without restructuring every test class. Rejected.

### Selected Approach

**Approach 1 — conftest.py with module-level constant.**

Ruled out Approach 2 (multi-file churn, no central enforcement) and Approach 3 (incompatible with module-level usage). Approach 1 is XS complexity, a single new file, and eliminates the UC-E2 shadowing risk by removing all 11 per-file definitions.

## High-Level Design

### Architecture Overview

```
tests/
  conftest.py          ← new: defines ORCHESTRATOR_ROOT (one level up from tests/)
  test_*.py (×11)      ← modified: remove _WORKTREE_ROOT; import ORCHESTRATOR_ROOT from conftest
```

pytest auto-discovers `conftest.py` and adds its directory to `sys.path`, making `from conftest import ORCHESTRATOR_ROOT` work in any sibling test file without manual `sys.path` manipulation.

### Key Abstractions

- `ORCHESTRATOR_ROOT` — a module-level constant in `conftest.py` resolving to `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`. This is the canonical repo root for all test path lookups.

## Low-Level Design

### Components

**`tests/conftest.py` (new)**
- Responsibility: define `ORCHESTRATOR_ROOT` as a module-level constant pointing one level above `tests/`.
- Inputs: `__file__` (the path of conftest.py itself).
- Outputs: `ORCHESTRATOR_ROOT` importable by all sibling test files.
- Dependencies: `os` (stdlib only).

**`tests/test_*.py` (×11, modified)**
- Remove lines defining `_HERE` and `_WORKTREE_ROOT` (or rename the local constant to use `ORCHESTRATOR_ROOT`).
- Import `from conftest import ORCHESTRATOR_ROOT`.
- Rename all `_WORKTREE_ROOT` usages to `ORCHESTRATOR_ROOT` (or keep `_WORKTREE_ROOT = ORCHESTRATOR_ROOT` as a one-line alias if the file uses the name extensively).

### Data Flow

1. pytest discovers `tests/conftest.py` at collection time and adds `tests/` to `sys.path`.
2. Each test file's `from conftest import ORCHESTRATOR_ROOT` resolves to `tests/conftest.py`.
3. `ORCHESTRATOR_ROOT` evaluates to the directory containing `tests/` — i.e., the repo root.
4. Downstream path constants (`_BIN_ORCHESTRATOR`, `PYTHONPATH`, etc.) resolve correctly.

### State Management

No mutable state. `ORCHESTRATOR_ROOT` is a module-level constant evaluated once at import time.

### Error Handling

- If the repo is in an unexpected location: `ORCHESTRATOR_ROOT` still correctly points one level above `tests/`; tests fail with a clear "file not found" error referencing the correct path (not a cryptic `/Users/spidey/bin/orchestrator` path). Satisfies UC-E1.
- The per-file `_WORKTREE_ROOT` definitions are fully removed (not left as overrides), eliminating the UC-E2 silent-shadowing risk.

## Constraints

- Must work in both direct invocation (`pytest tests/`) and worktree invocations (`~/code/feature_worktrees/<slug>/`).
- Cannot use third-party libraries — `os.path` only.
- Must not alter test logic, golden files, or fixture data.

## Trade-offs

- `from conftest import ORCHESTRATOR_ROOT` is slightly non-idiomatic (conftest symbols are usually consumed implicitly via fixture injection). The trade-off is acceptable: the import is explicit, works reliably, and is the simplest mechanism that solves a module-level constant problem in pytest.

## Acceptance Criteria

- AC-1: Given the repo is at any path on disk, when a developer runs `pytest tests/` from the repo root, then zero tests fail due to `bin/orchestrator` not found at `/Users/spidey/bin/orchestrator` or equivalent wrong-root path. [traces: UC-1]
- AC-2: Given a git worktree at `~/code/feature_worktrees/<slug>/`, when a developer runs `pytest tests/` from inside the worktree, then `ORCHESTRATOR_ROOT` resolves to the worktree root (one level above `tests/`), not the canonical repo root. [traces: UC-2]
- AC-3: Given the repo is in an unexpected location, when `bin/orchestrator` does not exist, then tests fail with a clear "file not found" error referencing the actual resolved path (not a stale hardcoded `/Users/spidey/` path). [traces: UC-E1]
- AC-4: Given `tests/conftest.py` defines `ORCHESTRATOR_ROOT`, when any of the 11 test files is examined, then no per-file `_WORKTREE_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..', '..'))` definition exists. [traces: UC-E2]
- AC-5: Given the fix is applied, when `pytest tests/` is run, then the 33 tests that currently pass continue to pass (no regression). [traces: UC-1]

## Decisions

- Use `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` over `pathlib.Path(__file__).parent.parent` → both are correct, but `os.path` is already the idiom used throughout all 11 test files; consistency beats stylistic preference here → no `pathlib` import required.
- Remove per-file definitions entirely rather than aliasing → eliminates UC-E2 risk with zero ambiguity.

## Open Questions

- None. OQ-1 (fixture vs. constant) resolved: module-level constant. OQ-2 (bats/shell scope): discovery confirmed scope is limited to 11 Python files.
