---
name: ux-design
description: "Design and validate UI/UX through prototyping and critique. Use when designing interfaces or producing UX artifacts."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#ux-designer
---

# UX Design

**Intent:** Design and validate UI/UX through playground prototyping and critique.

## Inputs

- `discovery_result`
- `discovery.md` at `spec/changes/<slug>/discovery.md`.
- Ticket body at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
  (`spec/changes/<slug>/ticket-context.md`) when present — for product/UX scope.

## Outputs

- `ux_direction`
- Artifacts: `ux-prototype.html` and `ux-artifacts.yaml` (in `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`).

## Instructions

1. Read the discovery brief's "UI Direction" section for context.
   **If the UI Direction is "N/A" or explicitly states no UI components,
   immediately abandon this step — do NOT generate prototypes or artifacts:**
   ```
   COMPLETION:
     step_id: ux-design
     status: abandoned
     outputs:
       reason: "No UI surface — discovery brief UI Direction is N/A"
   ```
2. Generate 3 design options via the playground skill (when available).
   - If playground fails: escalate to user with error. Do not proceed silently.
3. Present options to user for selection.
   - If no selection (timeout or skip): use the first option as stable default.
4. Polish the chosen direction with the frontend-design skill.
   - If frontend-design fails: escalate to user.
5. Validate with the ux-critique skill procedure — apply fixes autonomously.
   - If ux-critique finds autonomously fixable issues (CSS, accessibility): apply and re-run ux-critique.
   - If ux-critique finds issues requiring user input: escalate to user.
   - Max 2 ux-critique retry loops. After that, proceed with current state.
6. Record final UI direction in the discovery brief's "UI Direction" section.
7. Persist UX artifacts:
   a. Save the final polished prototype HTML to
   $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ux-prototype.html
   b. Write $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ux-artifacts.yaml with:
   - prototype.file: ux-prototype.html
   - prototype.description: one-line summary of the design direction
   - prototype.options_considered: number of options generated (typically 3)
   - prototype.selected_option: which option was chosen
   - prototype.critique_status: passed|passed-with-fixes|skipped
   - prototype.critique_rounds: number of ux-critique iterations run
8. Return COMPLETION (driver calls orchestrator done):
   ```
   COMPLETION:
     status: completed
     outputs:
       ux_direction: <one-line description of selected approach>
     artifacts: [ux-prototype.html, ux-artifacts.yaml]
   ```

### Rules (constraints on how)

- Use playground for rapid prototyping, frontend-design for polish, ux-critique for validation.

## Verify

Before returning COMPLETION, confirm:

- UI Direction section updated in discovery brief
- At least 3 options were generated and one selected
- ux-prototype.html exists in $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/
- ux-artifacts.yaml exists and follows § UX Artifact Contract format
