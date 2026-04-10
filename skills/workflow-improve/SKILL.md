---
name: workflow-improve
description: "Validate and improve workflow infrastructure. This skill should be used when the user says 'improve workflow', 'validate hooks', 'check step contracts', or after modifying hooks, step contracts, or schemas."
user-invocable: true
args:
  - name: target
    description: Specific schema, step, or hook to validate (optional — defaults to all)
    required: false
---

## Execution
1. Parse `$ARGUMENTS` for specific target or default to full validation
2. Analyze metrics and identify improvements to workflow infrastructure
3. Present findings and applied changes to user
