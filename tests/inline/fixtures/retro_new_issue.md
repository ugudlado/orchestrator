# Retro: orc-91 test fixture — new issue

## ISSUE-1 — Learn does not sync retro issues to backlog
- **category**: workflow-gap
- **severity**: blocker
- **detail**: The workflow-learner agent routes findings to contracts but never files backlog tickets from retro.md.
- **fix_direction**: Add backlog-sync-from-retro.sh and invoke it from workflow-learner section 4b on every learn run.
