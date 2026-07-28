---
name: content-draft
description: "Write a full draft from an approved outline, flagging unsourced claims instead of inventing sources. Use after content-outline, before editing."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#operator
---

# Content Draft

**Intent:** Execute the outline into complete prose. Structural decisions were made in the previous step; this step does not relitigate them.

## Inputs

- `outline.md` and `brief.md` in the artifact directory.

## Outputs

- `draft.md` in the artifact directory.
- `draft_result` — `drafted` or `blocked`.

## Instructions

1. Read `outline.md` and `brief.md` from `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/$ORCHESTRATOR_CHANGE_ID/`.

2. Write `draft.md`: every section the outline names, in the order it names them, as finished prose. Not notes, not an expanded outline.

3. Where the outline flagged an evidence gap, write the sentence the claim belongs in and mark it inline as `[NEEDS SOURCE: <what would settle it>]`. Never substitute an invented number, date, quote, or citation for a missing one.

4. Match the length and format the brief asked for. If the brief left length unspecified, choose one, state your choice at the top of the draft, and keep to it.

5. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       draft_result: drafted
   ```

### Rules (constraints on how)

- Follow the outline's structure. If a section turns out to be wrong, say so at the end of the draft under "Outline deviations" and explain — do not silently drop or reorder it.
- Fabrication is the failure mode this step exists to avoid. An unsourced claim marked `[NEEDS SOURCE]` is a success; a confident invented citation is a failure even if the prose is better.
- Write the whole piece. A partial draft with "…and so on" is not a deliverable.
- No meta-commentary about being an AI or about the drafting process inside the article body.

## Verify

- `draft.md` exists and covers every section from `outline.md`.
- Each outline evidence gap appears as a `[NEEDS SOURCE: …]` marker or is resolved from the brief.
- No invented citations, statistics, or quotes.
- Length and format match the brief, or a stated assumption at the top of the draft.
