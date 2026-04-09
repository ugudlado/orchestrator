# Tasks — Extract Dev Workflow System into Standalone Repo

## Phase 1: Create Directory Structure

- [x] T-1: Create orchestrator directory skeleton [P]
  Files: ~/code/orchestrator/agents/, ~/code/orchestrator/skills/, ~/code/orchestrator/config/workflows/, ~/code/orchestrator/config/steps/, ~/code/orchestrator/config/templates/, ~/code/orchestrator/config/scripts/
  Verify: `ls -d ~/code/orchestrator/{agents,skills,config/workflows,config/steps,config/templates,config/scripts}` — all 6 directories exist

## Phase 2: Copy Files from Shell Repo

- [x] T-2: Copy 11 agent files [P]
  Files: ~/code/orchestrator/agents/*.md
  Verify: `ls ~/code/orchestrator/agents/*.md | wc -l` returns 11

- [x] T-3: Copy 21 skill directories (excluding linear) [P]
  Files: ~/code/orchestrator/skills/*/
  Verify: `ls -d ~/code/orchestrator/skills/*/ | wc -l` returns 21

- [x] T-4: Copy 35 step contract YAMLs + CONVENTIONS.md [P]
  Files: ~/code/orchestrator/config/steps/
  Verify: `ls ~/code/orchestrator/config/steps/ | wc -l` returns 36

- [x] T-5: Copy 5 schema YAMLs to config/workflows/ [P]
  Files: ~/code/orchestrator/config/workflows/*.yaml
  Verify: `ls ~/code/orchestrator/config/workflows/*.yaml | wc -l` returns 5

- [x] T-6: Copy 5 template directories [P]
  Files: ~/code/orchestrator/config/templates/
  Verify: `ls -d ~/code/orchestrator/config/templates/*/ | wc -l` returns 5

- [x] T-7: Copy compute-swe-metrics.sh to config/scripts/ [P]
  Files: ~/code/orchestrator/config/scripts/compute-swe-metrics.sh
  Verify: `test -f ~/code/orchestrator/config/scripts/compute-swe-metrics.sh && echo ok`

- [x] T-8: Copy grammar.yaml to config/ [P]
  Files: ~/code/orchestrator/config/grammar.yaml
  Verify: `test -f ~/code/orchestrator/config/grammar.yaml && echo ok`

## Phase 3: Rename SPEC_HOME and Update Paths

- [x] T-9: Rename SPEC_HOME to WORKFLOW_HOME in all copied files
  Files: All files under ~/code/orchestrator/{agents,skills,config}
  Verify: `grep -r 'SPEC_HOME' ~/code/orchestrator/ --include='*.md' --include='*.yaml' | grep -v '.git/' | grep -v 'SPEC_CHANGES_DIR' | wc -l` returns 0

- [x] T-10: Update schema path references (schemas/ to config/workflows/, steps/ to config/steps/, templates/ to config/templates/)
  Files: ~/code/orchestrator/config/workflows/*.yaml, ~/code/orchestrator/config/grammar.yaml, affected step contracts and skill files
  Verify: `grep -r 'WORKFLOW_HOME/schemas\|WORKFLOW_HOME/steps\|WORKFLOW_HOME/templates' ~/code/orchestrator/ --include='*.yaml' --include='*.md' | grep -v 'config/' | wc -l` returns 0. Also verify `grep -r 'WORKFLOW_HOME/config/workflows' ~/code/orchestrator/config/workflows/ | head -3` shows updated paths.

## Phase 4: Create New Files

- [x] T-11: Create install.sh (called by `make setup`)
  Files: ~/code/orchestrator/install.sh
  Verify: `bash -n ~/code/orchestrator/install.sh` exits 0. File: (1) exports WORKFLOW_HOME=~/code/orchestrator into ~/.zshrc (idempotent, guard with grep), (2) symlinks ~/code/orchestrator/agents → ~/.claude/agents, (3) symlinks ~/code/orchestrator/skills → ~/.claude/skills. Running `make setup` from repo root invokes it without error.

- [x] T-12: Create config/guidelines.yaml
  Files: ~/code/orchestrator/config/guidelines.yaml
  Verify: `test -f ~/code/orchestrator/config/guidelines.yaml && grep -c 'feature\|bugfix\|chore\|spike\|bootstrap' ~/code/orchestrator/config/guidelines.yaml` returns 5

- [x] T-13: Create skills/orchestrate/ by copying skills/develop/ (already in orchestrator after T-3)
  Files: ~/code/orchestrator/skills/orchestrate/SKILL.md
  Verify: `test -f ~/code/orchestrator/skills/orchestrate/SKILL.md && grep -c 'WORKFLOW_HOME' ~/code/orchestrator/skills/orchestrate/SKILL.md` returns at least 1. `grep 'SPEC_HOME' ~/code/orchestrator/skills/orchestrate/SKILL.md | wc -l` returns 0.
  depends: T-3, T-9

- [x] T-14: Update skills/develop/ to be a delegation alias to /orchestrate
  Files: ~/code/orchestrator/skills/develop/SKILL.md
  Verify: `grep -c 'orchestrate' ~/code/orchestrator/skills/develop/SKILL.md` returns at least 1. File size is significantly smaller than orchestrate/SKILL.md (delegation, not full copy).

## Phase 5: Verification

- [x] T-15: End-to-end verification — make doctor, stale refs, file counts
  Files: (none — read-only checks)
  Verify: Run all of: (1) `make doctor` in ~/code/orchestrator shows all green. (2) `grep -r 'SPEC_HOME' ~/code/orchestrator/ --include='*.md' --include='*.yaml' | grep -v '.git/' | grep -v 'SPEC_CHANGES_DIR'` returns nothing. (3) `ls ~/code/orchestrator/skills/orchestrate/SKILL.md` exists. (4) `ls ~/code/orchestrator/config/guidelines.yaml` exists. (5) Skill count: `ls -d ~/code/orchestrator/skills/*/` returns 22 dirs (21 migrated + orchestrate).
