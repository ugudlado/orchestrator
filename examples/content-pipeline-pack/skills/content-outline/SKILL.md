---
name: content-outline
description: "Turn a content brief into a section-by-section outline with an angle and evidence gaps named. Use when planning an article before drafting."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#operator
---

# Content Outline

**Intent:** Commit to an angle and a structure before any prose is written, so the draft step has one thing to execute rather than a topic to wander through.

## Inputs

- `brief.md` in the artifact directory — audience, format, length, topic.

## Outputs

- `outline.md` in the artifact directory.
- `outline_result` — `drafted` or `blocked`.

## Instructions

1. Read `brief.md` from `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/$ORCHESTRATOR_CHANGE_ID/`. Do not assume a worktree — that variable already names the right directory whether or not one exists.

2. Name the **angle** in one sentence: the specific claim this piece makes, not the topic it covers. "What teams get wrong about X" is an angle; "an overview of X" is a topic.

3. Write `outline.md` beside the brief with:
   - the angle sentence,
   - the intended audience and length, carried from the brief,
   - each section as a heading plus one sentence saying what that section establishes,
   - an **Evidence gaps** list: every claim in the outline that needs a source, number, or example the brief does not supply.

4. If the brief is a placeholder or contradicts itself, say so in the outline's first line and outline the piece anyway under a stated assumption. Do not silently invent a specification the brief did not give.

5. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       outline_result: drafted
   ```

### Rules (constraints on how)

- One angle, stated explicitly. An outline with no angle is a table of contents and fails this step.
- Every section earns its place — if you cannot say what a section establishes, cut it.
- Name evidence gaps rather than filling them with plausible-sounding specifics. A fabricated statistic in an outline becomes a fabricated statistic in the draft.
- Do not write prose sections here. The deliverable is structure, not the article.

## Verify

- `outline.md` exists in the artifact directory.
- It opens with a single-sentence angle.
- Every section heading is followed by its purpose sentence.
- An Evidence gaps section is present (it may state "none" if the brief is fully self-sufficient).
