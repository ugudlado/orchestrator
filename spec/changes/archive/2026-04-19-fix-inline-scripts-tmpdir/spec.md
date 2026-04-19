---
feature-id: fix-inline-scripts-tmpdir
linear-ticket: none
---

# Specification: Fix inline scripts to honor TMPDIR for sandbox compatibility

## Motivation

Inline helper scripts under `scripts/inline/` call bare `mktemp`, which creates
temp files under `/var/folders/...` on macOS. Claude Code's default sandbox
denies writes there; only `$TMPDIR`, `/tmp/claude`, and a short whitelist are
permitted. The concrete symptom: `preview-route.sh` silently fails on every
workflow run under the default sandbox and always returns
`estimate_unavailable`. `capture-test-baseline.sh` has the same bug and
silently skips with `reason: unparseable` because its `$TMPOUT` write fails.

## What Changes

Every `mktemp` call in `scripts/inline/*.sh` is rewritten to an explicit
template rooted at `${TMPDIR:-/tmp}/<name>.XXXXXX`, so temp files land in a
sandbox-allowed directory while preserving non-sandbox behavior.

## Requirements

### Functional

1. **FR-1**: Every `mktemp` invocation under `scripts/inline/*.sh` MUST pass an
   explicit template of the form `${TMPDIR:-/tmp}/<descriptive-name>.XXXXXX`.
2. **FR-2**: When run inside Claude Code's default sandbox (writes restricted
   to `$TMPDIR`, `/tmp/claude`, etc.), `preview-route.sh` and
   `capture-test-baseline.sh` MUST complete without a write-permission failure
   and produce their normal JSON output.
3. **FR-3**: Outside the sandbox, behavior MUST remain functionally identical
   (same cleanup via `rm -f`, same JSON shape).

### Non-Functional

1. **NFR-1**: Fix is minimal — no helper functions, no new files, no shared
   sourced module. Straight in-place replacement in each script.

## Architecture

Inventory of affected files (grep for `mktemp` in `scripts/inline/`):

| File | Line(s) | Current | Replacement |
|------|---------|---------|-------------|
| `scripts/inline/capture-test-baseline.sh` | 41 | `TMPOUT=$(mktemp)` | `TMPOUT=$(mktemp "${TMPDIR:-/tmp}/capture-test-baseline.XXXXXX")` |
| `scripts/inline/preview-route.sh` | 24 | `TMPOUT=$(mktemp)` | `TMPOUT=$(mktemp "${TMPDIR:-/tmp}/preview-route.XXXXXX")` |
| `scripts/inline/preview-route.sh` | 25 | `TMPERR=$(mktemp)` | `TMPERR=$(mktemp "${TMPDIR:-/tmp}/preview-route-err.XXXXXX")` |

Total: **2 files, 3 call sites**. No other `mktemp` usage in `scripts/inline/`.

## Test Strategy

### Test File Paths

N/A — no unit tests. Verification is a manual smoke test of each script.

### Coverage Targets

N/A — shell-only bug fix, no code under test framework.

### Key Test Scenarios

- Run `scripts/inline/preview-route.sh` inside the default sandbox — exits 0
  and the last stdout line is a valid `{"route_preview": ...}` JSON object
  whose `status` is NOT a sandbox-caused `estimate_unavailable` error (it may
  still be `estimate_unavailable` for other reasons like missing estimator).
- Run `scripts/inline/capture-test-baseline.sh` — exits 0 and emits a
  `{"baseline": ...}` JSON object on its last stdout line.

## Acceptance Criteria

- AC-1: Given the default Claude Code sandbox, when `preview-route.sh` runs,
  then its temp-file writes succeed and it emits a valid `{"route_preview":
  ...}` JSON line. [traces: UC-1]
- AC-2: Given the default Claude Code sandbox, when
  `capture-test-baseline.sh` runs, then its temp-file writes succeed and it
  emits a valid `{"baseline": ...}` JSON line. [traces: UC-1]
- AC-3: Given a grep over `scripts/inline/*.sh` for `mktemp`, when results
  are inspected, then every call passes an explicit `${TMPDIR:-/tmp}/...`
  template — zero bare `mktemp` calls remain. [traces: UC-1]
- AC-4: Given a run outside the sandbox, when either script runs, then it
  behaves identically to before the change (same JSON shape, same cleanup).
  [traces: UC-E1]

## Alternatives Considered

**Alternative 1: Shared helper (`_mktemp_safe`) sourced by each inline script**
Rejected. Inline scripts are deliberately self-contained so the orchestrator
can execute them without sourcing project-local files. Introducing a shared
dependency inverts that contract for a 3-site fix.

**Alternative 2: Set `TMPDIR` globally in the orchestrator wrapper**
Rejected. Sandbox restrictions are a Claude Code runtime concern; fixing it
at the caller hides the bug from any other invocation path (CI, manual
runs, future callers) and makes the script implicitly dependent on caller
setup.

## Impact

No breaking changes. Pure in-place string replacement. Cleanup (`rm -f`)
already covers the new template paths without modification.

## Decisions

- Use explicit templates per call site rather than a shared helper:
  minimal-fix principle — 3 sites, no reason to add indirection.
- Name templates after the script/purpose (e.g. `preview-route.XXXXXX`)
  rather than a generic name: eases debugging of leaked temp files.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
