# Tasks -- Auto-disable UX steps for backend features

- [x] T-1: Add ux_design auto-detection rule to load-project-context.yaml
  In step 4 of the instruction block (compute runtime feature profile), after
  merging schema defaults with state flags, add a rule: if no CLI flag explicitly
  set `ux_design`, check `tech_stack` from project.yaml against a list of known
  frontend technologies (`react`, `nextjs`, `next`, `vue`, `svelte`, `angular`,
  `html`, `css`, `tailwind`, `scss`, `sass`, `less`, `webpack`, `vite`,
  `typescript-frontend`, `flutter`, `swift-ui`). If none match, set
  `ux_design: false`. Add a brief inline comment documenting the heuristic and
  the override path (`--no-ux` / explicit flag).
  Files: config/steps/load-project-context.yaml
  Verify: Read the updated step contract and confirm (a) the auto-detection rule
  is present in step 4, (b) it only fires when no explicit CLI flag was set for
  ux_design, (c) it checks tech_stack against a frontend technology list, and
  (d) the inline documentation explains the heuristic and override. Then confirm
  this repo's own tech_stack `[bash, zsh, yaml]` would trigger `ux_design: false`.
