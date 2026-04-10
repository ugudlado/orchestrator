# Multi-Repo ORCHESTRATOR_HOME Isolation

## Idea
Currently, `WORKFLOW_STATE_DIR` defaults to `$ORCHESTRATOR_HOME/changes/$REPO_NAME`, which means all repos share the same `~/.config/orchestrator/changes/` parent. This works but has no isolation: a bug in one repo's state.yaml cleanup could affect another repo's active changes. More importantly, `install.sh` hardcodes `~/.zshrc` and does not support multiple orchestrator installations (e.g., a stable release and a development branch). Add: (1) per-repo override support via `.orchestrator.yaml` in repo root (setting a custom `WORKFLOW_STATE_DIR`), (2) `install.sh` support for `--profile` flag to install to a named profile instead of default, (3) documentation of the multi-repo state isolation model.

## Why Now
The vision says "universal workflow engine for LLMs -- define any process as config, run it on any tool." As adoption grows beyond a single developer's repos, the shared-state model will create conflicts. This is a foundational concern for the "portable across repos" story.

## Priority
- User value: 5/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 5.0**
