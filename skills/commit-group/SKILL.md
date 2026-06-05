---
name: commit-group
description: "Create commits in logical groups. This skill should be used when the user says 'commit-group', 'group commits', 'organize commits', or when there are multiple unstaged changes that should be organized into atomic commits."
user-invocable: true
args: []
---

## Execution

1. Run `git diff --stat` and `git diff` to analyze all unstaged changes
2. Group related changes by logical theme (e.g., refactors, features, fixes, config)
3. For each group: stage the relevant files with `git add`, then commit with a descriptive message
4. Present a summary table of all commits created
