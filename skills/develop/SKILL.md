---
name: develop
description: "Alias for orchestrate. This skill should be used when the user says 'develop', 'start developing', or 'dev'. Delegates to the orchestrate skill for backward compatibility."
user-invocable: true
args:
  - name: description
    description: Feature description, Linear ticket ID, or feature ID to resume
    required: false
---

## Execution

Delegate to the orchestrate skill with the same arguments passed to this skill.
