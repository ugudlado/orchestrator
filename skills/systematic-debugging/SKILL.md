---
name: systematic-debugging
description: "Systematic debugging — reproduce, trace, hypothesize, verify. This skill should be used when the user says 'debug this', 'why is this failing', 'trace the error', 'find the root cause', or encounters a bug, test failure, or unexpected behavior that needs methodical investigation."
user-invocable: true
args:
  - name: issue
    description: Description of the bug or failing test (optional)
    required: false
---

## Execution

1. Parse `$ARGUMENTS` for bug description or failing test
2. Use the debugger agent for systematic root cause analysis
3. Present root cause analysis and suggested fix to user
