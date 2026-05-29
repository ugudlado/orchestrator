# UX Design

**Intent:** Design and validate UI/UX through playground prototyping and critique.

## Inputs

- `discovery_result`
- `discovery.md` at `spec/changes/<slug>/discovery.md`.

## Outputs

- `ux_direction`
- Artifacts: `ux-prototype.html` and `ux-artifacts.yaml` (in `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`).

## Instructions

1. Read the discovery brief's "UI Direction" section for context.
2. Generate 3 design options via /playground skill.
   - If /playground fails: escalate to user with error. Do not proceed silently.
3. Present options to user for selection.
   - If no selection (timeout or skip): use the first option as stable default.
4. Polish the chosen direction with /frontend-design skill.
   - If /frontend-design fails: escalate to user.
5. Validate with /critique skill — apply fixes autonomously.
   - If /critique finds autonomously fixable issues (CSS, accessibility): apply and re-run /critique.
   - If /critique finds issues requiring user input: escalate to user.
   - Max 2 /critique retry loops. After that, proceed with current state.
6. Record final UI direction in the discovery brief's "UI Direction" section.
7. Persist UX artifacts per CONVENTIONS.md § UX Artifact Contract:
   a. Save the final polished prototype HTML to
      $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ux-prototype.html
   b. Write $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ux-artifacts.yaml with:
      - prototype.file: ux-prototype.html
      - prototype.description: one-line summary of the design direction
      - prototype.options_considered: number of options generated (typically 3)
      - prototype.selected_option: which option was chosen
      - prototype.critique_status: passed|passed-with-fixes|skipped
      - prototype.critique_rounds: number of /critique iterations run
8. Return COMPLETION per contracts/done-payload.md (driver calls orchestrator done).

### Rules (constraints on how)

- Use playground for rapid prototyping, frontend-design for polish, critique for validation.

## Verify

Before returning COMPLETION, confirm:

- UI Direction section updated in discovery brief
- At least 3 options were generated and one selected
- ux-prototype.html exists in $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/
- ux-artifacts.yaml exists and follows § UX Artifact Contract format
