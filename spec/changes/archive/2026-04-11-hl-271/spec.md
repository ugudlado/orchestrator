---
feature-id: auto-disable-ux-steps-for-backend-features
linear-ticket: HL-271
---

# Chore: Auto-disable UX steps for backend features

## What

Add auto-detection logic to the `load-project-context` step so that `ux_design`
is set to `false` when the project has no frontend technologies. This removes
the need to pass `--no-ux` manually for backend-only projects.

The detection lives in `config/steps/load-project-context.yaml`, step 4 (compute
runtime feature profile). After merging schema defaults with state flags, an
additional rule checks whether the project's `tech_stack` contains any frontend
technology. If it does not, `ux_design` is forced to `false` -- unless the user
explicitly passed `--no-ux: false` (i.e., opted in).

## Why

Every backend-only feature currently requires `--no-ux` to skip the `ux-design`
and `run-ux-critique` steps. This is friction with no value -- a project whose
tech stack is `[bash, zsh, yaml]` will never need UX design steps. Auto-detection
eliminates the flag for the common case while preserving the explicit override
for mixed or frontend projects.

## Acceptance Criteria

- [ ] AC-1: When `tech_stack` contains no frontend technologies, the resolved
  feature profile sets `ux_design: false` without any CLI flag.
- [ ] AC-2: When `tech_stack` contains a frontend technology (e.g., `react`,
  `nextjs`, `vue`, `svelte`, `html`, `css`, `tailwind`), `ux_design` retains
  its schema default (`true`).
- [ ] AC-3: An explicit `--no-ux` flag still works and takes precedence
  (no regression).
- [ ] AC-4: The `workflow_plan` written to `state.yaml` reflects the auto-resolved
  `ux_design` value -- `ux-design` and `run-ux-critique` appear in `filtered`
  (not `active`) for backend-only projects.
- [ ] AC-5: The auto-detection logic is documented inline in the step contract
  so future maintainers understand the heuristic.
