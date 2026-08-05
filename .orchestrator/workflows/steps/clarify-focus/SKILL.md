---
name: clarify-focus
description: "Capture the user's research focus/angle from their latest message and record it to focus.md. Use after intake-consult in a consult workflow."
user-invocable: true
---

# Clarify Focus

**Intent:** Turn the user's direction (the "User direction" appended to this
prompt) into a crisp research focus statement saved to `focus.md`.

## Inputs

- `spec/changes/<slug>/topic.md` — the normalized topic from intake.
- The **User direction** line at the end of this prompt (the client's
  continuation message).

## Outputs

- `spec/changes/<slug>/focus.md` — one short paragraph capturing the angle:
  what aspect the user wants emphasized, constrained, or compared.

## Instructions

1. Read `topic.md`.
2. Read the User direction. If it's empty or generic ("go ahead", "continue"),
   default to the full topic.
3. Write `focus.md` containing:
   - **Topic:** the topic
   - **Focus:** a 1–2 sentence statement of the user's angle
   - **Constraints:** any constraints the user mentioned (scope, time, format)

## Verify

- `focus.md` exists under the change dir.
- The Focus line reflects the user's direction, not a generic restatement.

Return a COMPLETION block on stdout with `{"status": "completed", "outputs": {"focus_file": "<abs path>"}}`.
