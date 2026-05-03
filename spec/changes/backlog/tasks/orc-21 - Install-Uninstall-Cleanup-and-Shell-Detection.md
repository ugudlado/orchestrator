---
id: ORC-21
title: Install/Uninstall Cleanup and Shell Detection
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-install-uninstall-cleanup
  - feature
  - score-5.2
  - recurrence-1
dependencies: []
priority: low
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: install-uninstall-cleanup -->

**Original score:** 5.2 | **Recurrence:** 1

## Idea

`install.sh` has three issues: (1) It hardcodes `~/.zshrc` as the shell profile -- users on bash, fish, or nushell get no ORCHESTRATOR_HOME export. Detect `$SHELL` and write to the correct profile. (2) There is no `uninstall.sh` or `make uninstall` target -- removing the orchestrator requires manually deleting symlinks from `~/.claude/agents/`, `~/.claude/skills/`, and `~/.config/orchestrator/`, plus removing the export line from `.zshrc`. (3) The install script does not clean up stale symlinks -- if an agent `.md` file is renamed or deleted from the repo, the old symlink persists in `~/.claude/agents/`. Add a staleness check that removes symlinks pointing to non-existent targets.

## Why Now

The project learned (in `gotchas`) that "running make setup from the worktree sets ORCHESTRATOR_HOME to the worktree path, not the main repo." This is a direct consequence of install.sh not validating or warning about the source path. Hardening install.sh now prevents a class of setup errors that waste entire workflow runs.

## Priority

- User value: 6/10
- Strategic fit: 5/10
- Technical leverage: 4/10
- Effort: small
- **Score: 5.2**

---
<!-- SECTION:DESCRIPTION:END -->
