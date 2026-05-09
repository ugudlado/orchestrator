---
id: ORC-47
title: 'Fix repro.sh exit-code inversion: non-zero on success, zero on bug-confirmed'
status: Backlog
assignee: []
created_date: '2026-05-08 12:52'
labels:
  - bugfix
  - hl-303
dependencies: []
ordinal: 44000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
repro.sh (spec/changes/hl-303/repro.sh) exits 1 when the fix is working (prints 'OK') and exits 0 when the bug is confirmed. This is counter-intuitive and breaks callers using 'set -e' — a working fix would be treated as a failure by shell scripts that rely on conventional exit codes (0=success, non-zero=failure). The script should exit 0 when the post-fix behavior is correct and exit 1 when the bug is still present.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 repro.sh exits 0 when 'OK: predicate correctly detected unchecked tasks' is the result
- [ ] #2 repro.sh exits 1 (non-zero) when the bug is confirmed (regression state)
- [ ] #3 The fix is validated by running repro.sh in the fixed repo and confirming exit code is 0
<!-- AC:END -->
