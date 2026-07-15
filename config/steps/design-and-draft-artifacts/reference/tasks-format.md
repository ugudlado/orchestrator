# Tasks YAML Format Contract (tasks.yaml)

The `tasks.yaml` file is a machine-readable structural contract between
`design-and-draft-artifacts` (producer) and `implement-tasks` (consumer).
Both steps MUST use this exact format.

The authoritative template is `$ORCHESTRATOR_CONFIG/steps/design-and-draft-artifacts/templates/$SCHEMA/tasks.yaml`
— read it in the artifact-generation step and use it as the structural skeleton for generation.

## Field rules

| Field          | Required | Format                                                                             |
| -------------- | -------- | ---------------------------------------------------------------------------------- |
| version        | Yes      | Integer `1`                                                                        |
| tasks          | Yes      | List of task objects                                                               |
| id             | Yes      | `T-<N>` or `fix-<N>`, unique within the file                                       |
| title          | Yes      | One line, imperative verb                                                          |
| depends_on     | No       | List of other task ids; empty list or absent means no deps                         |
| files          | Yes      | List of file paths the task is allowed to touch                                    |
| verify         | Yes      | List of repo-root-relative commands (no absolute paths, no `cd /abs/path &&`)      |
| test_scenarios | No       | List of human-readable test cases                                                  |
| why            | No       | Which design.md AC this task serves                                                |
| change         | No       | The mechanism — what edit, at which file:line                                      |
| status         | No       | `pending` (default) or `completed`; updated by `implement-tasks` after each commit |
| tokens_in      | No       | Input tokens used for this task; written by `implement-tasks` on completion        |
| tokens_out     | No       | Output tokens used for this task; written by `implement-tasks` on completion       |
| duration_s     | No       | Wall-clock seconds for this task; written by `implement-tasks` on completion       |

## Validation rules

- `id` values must be unique within the file (no duplicates).
- `depends_on` references must resolve to another task `id` in the same file.
- No dependency cycles.
- Missing required fields (`id`, `title`, `files`, `verify`) are rejected by
  `validate-tasks-yaml.sh`.
- `verify` commands must be repo-root-relative — no absolute paths, no `cd /...` prefix.
  The developer agent runs them from `$REPO_ROOT`. Absolute paths break worktrees
  and other machines.

## Validator

`config/steps/design-and-draft-artifacts/validate-tasks-yaml.sh <path-to-tasks.yaml>` — exits 0
on a well-formed file, exits non-zero with a diagnostic message otherwise.

## Consumers

- `implement-tasks` — reads this file, executes pending tasks in order, sets `status: completed` per task
- `run-phase-review` (needs_work branch) — appends fix tasks with `status: pending` before re-dispatch
