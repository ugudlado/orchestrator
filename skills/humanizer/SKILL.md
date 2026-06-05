---
name: humanizer
description: "Remove AI writing patterns from text. This skill should be used when the user says 'humanize', 'make this sound human', 'remove AI patterns', 'rewrite naturally', or asks to edit text to sound less AI-generated."
user-invocable: true
args:
  - name: target
    description: Text to humanize, file path, or "clipboard" (optional — reads from context)
    required: false
  - name: --voice
    description: File path to a writing sample for voice matching
    type: option
---

## Execution

1. Parse `$ARGUMENTS` for target text or file path
2. If `--voice` provided, include the writing sample for voice calibration
3. Process the target text through the humanizer agent
4. Present the scored output to the user
