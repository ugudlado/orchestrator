# Deep Doctor Health Check

## Idea
Expand `make doctor` from its current 6-line existence check into a comprehensive health validator. Currently it only checks if directories exist. A real doctor command should verify: (1) symlinks point to valid targets (not stale after a worktree switch -- the gotcha in project.yaml), (2) every schema referenced in `project.yaml schemas:` has a matching workflow YAML, (3) every step referenced in schemas has a matching step contract YAML, (4) every agent referenced in step contracts has a matching agent .md, (5) ORCHESTRATOR_HOME matches the expected path, (6) no orphaned state.yaml files (active changes with no worktree). This catches the most common failure mode: running `make setup` from a worktree instead of main.

## Why Now
The gotcha documented in `project.yaml` ("install.sh uses ln -sf to re-point existing symlinks -- running make setup from the worktree sets ORCHESTRATOR_HOME to the worktree path, not the main repo") is a real, recurring problem. A smart doctor command would detect this immediately instead of letting it silently corrupt the next workflow run. The recent install.sh refactoring for config symlinks makes this the right time to add validation.

## Prototype
```
$ make doctor
Checking orchestrator health...
  [OK] spec/project.yaml
  [OK] install.sh
  [OK] config/workflows (6 schemas)
  [OK] config/steps (38 contracts)
  [OK] agents (11 definitions)
  [OK] skills (23 skills)
  [OK] ORCHESTRATOR_HOME -> /Users/spidey/code/orchestrator (matches repo root)
  [OK] All schema refs resolve (feature -> feature.yaml, etc.)
  [OK] All step refs resolve (38/38 steps have contracts)
  [OK] All agent refs resolve (8/8 agents have .md files)
  [WARN] Stale worktree state: changes/orchestrator/old-feature/state.yaml (no worktree dir)
  [OK] Symlinks valid (3 dirs, 2 files)
Done. 11 checks passed, 1 warning.
```

## Priority
- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: small
- **Score: 6.0**
