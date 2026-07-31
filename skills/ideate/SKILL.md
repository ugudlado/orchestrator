---
name: ideate
description: 'Brainstorm ideas, explore designs, and build a prioritized backlog. Use when the user wants new feature ideas, backlog management, or says "ideate", "brainstorm", "what should we build".'
user-invocable: true
args:
  - name: topic
    description: Focus area for ideation (optional)
    required: false
  - name: --next
    description: Pick the most valuable item from existing backlog
    type: flag
  - name: --refresh
    description: Re-prioritize existing backlog without creating new ideas
    type: flag
---

## Execution

1. Parse `$ARGUMENTS` for topic, --next, or --refresh flags.
2. Read CLAUDE.md / AGENTS.md / README for project vision, architecture, rules, and gotchas.
3. Invoke the `ideator` agent with:
   - the user's original request and parsed mode
   - the project context from the repo docs above
   - instructions to inspect current repo state before ranking anything
4. Require the `ideator` agent to perform a freshness pass:
   - List existing backlog tasks via `backlog task list --plain` (managed by the `backlog` CLI under `spec/changes/backlog/`). Filter by status (`-s "To Do"`) to exclude already-completed work; use `backlog task <id> --plain` for full body and `backlog search "<query>" --plain` for keyword scans.
   - Read completed work from `spec/changes/archive/*`.
   - Search the current repo for concrete evidence that each candidate is still missing.
   - Classify each backlog task as `fresh`, `partially_done`, `stale`, or `superseded`.
   - Exclude `stale` and `superseded` tasks from "next best" recommendations unless the user explicitly asks for historical backlog cleanup.
5. For `--next` or requests like "next N best ideas":
   - Do not sort by stored priority alone (the CLI's `high|medium|low` is a coarse bucket; tasks carry `score-X.X` and `recurrence-N` labels for finer-grained tiebreaks).
   - Re-rank live candidates using product vision, current project state, web research, strategic fit, dependency order, risk reduction, and effort.
   - Prefer ideas that unblock deterministic workflow execution or prevent recurring workflow failures.
6. For `--refresh`:
   - Refresh recommendations in the response only; do not write files unless the user explicitly asks to persist the refresh.
7. For open-ended ideation:
   - Ask the `ideator` agent to generate new ideas only after checking that they do not duplicate existing or completed work.
8. Do not create tickets, backlog directories, specs, or other files unless the user explicitly asks to store the result.
9. Present the agent's findings to the user with:
   - selected ideas and reasons
   - stale/superseded ideas discovered
   - concrete repo evidence used for the ranking
   - external research signals used, when web research was relevant
