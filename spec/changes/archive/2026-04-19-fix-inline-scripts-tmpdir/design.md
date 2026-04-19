# Design: Fix inline scripts to honor TMPDIR for sandbox compatibility

## Context

Claude Code's default sandbox on macOS denies writes to `/var/folders/...`
(where bare `mktemp` lands) and whitelists `$TMPDIR`, `/tmp/claude`, and a few
other paths. Two inline scripts — `scripts/inline/preview-route.sh` and
`scripts/inline/capture-test-baseline.sh` — currently call bare `mktemp`,
causing silent failures (the scripts are marked non-blocking so workflow runs
proceed but always see `estimate_unavailable` / `unparseable` outputs). Fix
is mechanical: anchor `mktemp` under `${TMPDIR:-/tmp}`.

## Goals / Non-Goals

### Goals

- Inline scripts produce valid JSON outputs when run in the default sandbox.
- Zero behavioral change outside the sandbox.
- Minimal textual diff — one-line edit per call site.

### Non-Goals

- Introducing a shared shell helper or library for inline scripts.
- Changing orchestrator-wrapper behavior or env setup.
- Touching non-`mktemp` code paths in the affected scripts.

## Approaches Considered

### Approach 1: Per-site explicit template (CHOSEN)

Replace each `mktemp` with `mktemp "${TMPDIR:-/tmp}/<name>.XXXXXX"` in place.

- Pros: 3-line diff, no new files, no new indirection, easy to grep and audit.
- Cons: If many more inline scripts are added later with the same pattern,
  repetition grows — but that's a problem to solve when it appears.

### Approach 2: Shared `_mktemp_safe` helper sourced by each script

Create `scripts/inline/_lib.sh` exposing `_mktemp_safe <name>` and source it
from every inline script.

- Pros: Single point of future change.
- Cons: Inline scripts are explicitly self-contained so the orchestrator can
  execute them without resolving sibling files; adding a sourced dep inverts
  that contract. Complexity S/M for a 3-site bug — net negative.

### Approach 3: Move `export TMPDIR=...` into the orchestrator wrapper

Have the orchestrator set `TMPDIR` (to a known-safe dir) before invoking
inline scripts; leave bare `mktemp` alone.

- Pros: Zero touch to the scripts.
- Cons: Scripts become implicitly dependent on a non-obvious caller contract.
  Any other caller (CI, manual, future agents) re-hits the same bug. Wrong
  layer for the fix.

### Selected Approach

**Approach 1** — per-site explicit template. The bug is in the scripts, the
fix belongs in the scripts, the diff is smaller than either alternative, and
it leaves the system no worse off. Approaches 2 and 3 both add coupling in
exchange for no concrete benefit at today's scale (3 call sites, 2 files).

## High-Level Design

### Architecture Overview

No architectural change. Inline scripts remain self-contained single-file
bash programs invoked by the orchestrator or ad-hoc callers.

### Key Abstractions

None introduced. Reuses the existing `mktemp` template syntax.

## Low-Level Design

### Components

Two files, 3 call sites:

- `scripts/inline/capture-test-baseline.sh:41`
- `scripts/inline/preview-route.sh:24`
- `scripts/inline/preview-route.sh:25`

Each line becomes `mktemp "${TMPDIR:-/tmp}/<descriptive-name>.XXXXXX"`.
Existing `rm -f "$TMPOUT" ...` cleanup already handles any path.

### Data Flow

Unchanged. Script writes to temp file, reads back, emits JSON, removes temp.

### State Management

None beyond the transient temp file, which is already cleaned up.

### Error Handling

Unchanged. If `mktemp` itself fails (disk full, etc.) the scripts already run
under `set -uo pipefail`; the subsequent write or `python3` step would
surface the failure in the JSON output's `reason` field.

## Constraints

None beyond standard project conventions.

## Trade-offs

Accepting three similar string literals instead of one shared helper. At 3
sites the duplication cost is negligible; at 10+ sites we'd revisit
Approach 2.

## Decisions

- Per-site explicit template over shared helper → preserves inline-script
  self-containment → future maintenance cost is linear with call sites (fine
  at today's size).
- Descriptive template names (`preview-route.XXXXXX`, etc.) over generic
  `tmp.XXXXXX` → makes leaked temp files traceable to their producer →
  one-time naming cost, ongoing debugging benefit.

## Open Questions

- None.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
