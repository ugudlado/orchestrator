---
name: content-edit
description: "Edit a draft to publish-ready and gate on unresolved sources and brief compliance. Use as the final quality gate before publishing."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#operator
---

# Content Edit

**Intent:** Produce the publish-ready file and decide, on evidence, whether it is publishable. This step is a gate — it may send the piece back.

## Inputs

- `draft.md`, `outline.md`, `brief.md` in the artifact directory.

## Outputs

- `final.md` in the artifact directory.
- `edit_result` — `approved` or `rejected`.

## Instructions

1. Read `draft.md`, `outline.md`, and `brief.md` from `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/$ORCHESTRATOR_CHANGE_ID/`.

2. Edit for clarity, not for volume: cut hedges and filler, break run-on sentences, make every heading say what its section delivers. Preserve the author's claims — you are editing, not rewriting the argument.

3. Check the draft against the brief: audience, format, length, and the outline's angle. Record each as met or missed.

4. **Gate.** Return `rejected` when any of these hold:
   - an unresolved `[NEEDS SOURCE: …]` marker remains,
   - a section the outline requires is missing,
   - the piece contradicts the brief's audience or format.

   Otherwise write `final.md` and return `approved`. When rejecting, still write `final.md` with your edits applied and list the blocking reasons at the top under "Blocking" — the next draft attempt reads them.

5. Return COMPLETION with the decision:
   ```
   COMPLETION:
     status: completed
     outputs:
       edit_result: approved
   ```

### Rules (constraints on how)

- Reject on evidence, not on taste. "I would have written it differently" is not a blocking reason; a missing source is.
- Do not resolve a `[NEEDS SOURCE]` marker by supplying a source you did not verify. Rejecting is the correct move.
- Do not pad. If the edit makes the piece shorter, that is a good outcome as long as the brief's length constraint still holds.
- State what you checked and what you did not. Do not imply you verified a fact you only read.

## Verify

- `final.md` exists.
- Every brief constraint is recorded as met or missed.
- `edit_result` is `approved` only when no `[NEEDS SOURCE]` marker survives and every outline section is present.
- On `rejected`, the blocking reasons are specific enough for the draft step to act on.
