---
id: ORC-12
title: Consolidate orchestrator script trees and test roots
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-consolidate-script-trees
  - feature
  - score-7.5
  - recurrence-1
dependencies:
  - ORC-17
priority: medium
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: consolidate-script-trees -->

**Original score:** 7.5 | **Recurrence:** 1

## Idea

The orchestrator's script layer has accumulated four overlapping locations and four test roots:

- `scripts/` — shell utilities and `scripts/inline/` (the real inline-step ports)
- `config/scripts/orchestrator_next/` — the Python package (`upsert.py`, `cost_report.py`, tests)
- `config/scripts/adapters/` — adapters
- `config/scripts/tests/`, `config/scripts/__tests__/`, `config/scripts/test-fixtures/` — three separate test locations

Plus `compute-swe-metrics.sh` at **736 lines of bash** — directly contradicts the `bash-fragility-prefer-python-for-new-code` learning.

## Why Now

1. Recent metrics work (HL-290, HL-291, post-OTel cleanup) already churned these files — piggyback on warm context.
2. Unblocks **HL-298** (harden inline script ports) — that work becomes trivial after layout is unified.
3. Several backlog items (`skill-stub-audit`, `error-recovery-contract-step`) assume a cleaner layout than we have.
4. The 736-line bash script is a known fragility risk and a structural root cause of debugging pain in metrics work.

## Proposed restructure

**Phase A — consolidate Python:**
- One canonical Python package (e.g. `orchestrator_py/` or keep `orchestrator_next/`).
- Move `config/scripts/adapters/` into the package as a submodule.
- Collapse `tests/`, `__tests__/`, `test-fixtures/` into one `tests/` tree inside the package.

**Phase B — port bash to Python:**
- Rewrite `compute-swe-metrics.sh` (736 LOC) as a Python module; keep a thin shell wrapper if external callers invoke it.
- Move remaining shell-shaped scripts to `scripts/shell/`.

**Phase C — update references:**
- Hooks, step contracts, `install.sh`, README paths.
- Run full orchestrate cycle end-to-end to confirm parity.

## Acceptance

- One Python package root; one `tests/` root; one `scripts/shell/` root.
- `compute-swe-metrics.sh` ported to Python with test coverage; old bash removed or reduced to a wrapper.
- All existing orchestrate/autopilot flows pass an end-to-end run.
- `install.sh` still produces a working install on a clean machine.
- No stale directory left behind.

## Out of Scope

- Rewriting business logic — pure move + rename + port.
- Changing what the scripts do.
- New features in `doctor.py`.

## Notes

Linear ticket creation blocked by workspace free-tier limit on 2026-04-19; file this in Linear when the workspace is upgraded.

---

## Dependencies / Interactions

- **Unblocks** HL-298 (harden inline script ports).
- **Touches** same surface area as `skill-stub-audit` backlog item — coordinate ordering, no hard dependency.
- Best sequenced **after** `split-cost-report-package` (trivial; sets the package-layout pattern first).

## Priority

- User value: 6/10
- Strategic fit: 9/10 (removes a load-bearing fragility)
- Technical leverage: 9/10 (unblocks multiple downstream items)
- Effort: medium
- **Score: 7.5**

## Size

Medium. Target: 6-8 tasks across the three phases. Bulk of effort is Phase B (bash → Python port) and verification.

## Labels

orchestrator, improvement
<!-- SECTION:DESCRIPTION:END -->
