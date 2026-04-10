---
name: critique
description: "UX design critique with staff-level evaluation. This skill should be used when the user says 'critique', 'review this design', 'UX review', or before shipping a UI feature."
user-invocable: true
args:
  - name: target
    description: File path, URL, or feature area to critique (optional — defaults to recent UI changes)
    required: false
---

## Execution
1. Identify target from `$ARGUMENTS` (file path, URL, or auto-detect recent UI files)
2. Perform UX review of the target
3. Present the review report to the user
