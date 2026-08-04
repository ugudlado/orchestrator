# Tasks YAML Format Contract (tasks.yaml)

The `tasks.yaml` file is a machine-readable structural contract between
`design` (producer), `implement` (consumer), and `code-review` (reopens or
appends on needs_work). Both producer and consumers MUST use this exact format.

The authoritative template is `architect/templates/$SCHEMA/tasks.yaml`
— read it in the artifact-generation step and use it as the structural skeleton for generation.

## Field rules

| Field          | Required | Format                                                                                                                                         |
| -------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| version        | Yes      | Integer `1`                                                                                                                                    |
| tasks          | Yes      | List of task objects                                                                                                                           |
| id             | Yes      | `T-<N>` or `fix-<N>`, unique within the file                                                                                                   |
| title          | Yes      | One line, imperative verb                                                                                                                      |
| depends_on     | No       | List of other task ids; empty list or absent means no deps                                                                                     |
| files          | Yes      | List of file paths the task is allowed to touch                                                                                                |
| verify         | Yes      | List of repo-root-relative commands (no absolute paths, no `cd /abs/path &&`)                                                                  |
| test_scenarios | No       | List of human-readable test cases                                                                                                              |
| why            | No       | Which design.md AC this task serves                                                                                                            |
| change         | No       | The mechanism — what edit, at which file:line                                                                                                  |
| status         | No       | `pending` (default) or `completed`. `implement` sets `completed` after commit; `code-review` may reopen to `pending` when addressing a finding |
| reviews        | No       | List of reviewer comments (see below). Appended when a task is reopened or when a new `fix-N` is created from a finding                        |
| tokens_in      | No       | Input tokens used for this task; written by `implement` on completion                                                                          |
| tokens_out     | No       | Output tokens used for this task; written by `implement` on completion                                                                         |
| duration_s     | No       | Wall-clock seconds for this task; written by `implement` on completion                                                                         |

### `reviews[]` entries (optional)

Minimal shape — one object per reviewer comment:

| Field   | Required | Format                                               |
| ------- | -------- | ---------------------------------------------------- |
| at      | Yes      | ISO-8601 UTC timestamp (e.g. `2026-08-03T10:15:00Z`) |
| comment | Yes      | Actionable reviewer note (what to fix and why)       |

Example:

```yaml
reviews:
  - at: "2026-08-03T10:15:00Z"
    comment: "csv.ts:41 — null-guard empty cells before formatRow; test 'escapes embedded quotes' still fails"
```

## Validation rules

- `id` values must be unique within the file (no duplicates).
- `depends_on` references must resolve to another task `id` in the same file.
- No dependency cycles.
- Missing required fields (`id`, `title`, `files`, `verify`) are rejected by
  `validate-tasks-yaml.sh`.
- `verify` commands must be repo-root-relative — no absolute paths, no `cd /...` prefix.
  The developer agent runs them from `$REPO_ROOT`. Absolute paths break worktrees
  and other machines.
- When `reviews` is present it must be a list of mappings each with `at` and `comment`.

## Validator

`architect/validate-tasks-yaml.sh <path-to-tasks.yaml>` — exits 0
on a well-formed file, exits non-zero with a diagnostic message otherwise.

## Consumers

- `implement` — reads this file, executes pending tasks in order (including
  reopened ones), sets `status: completed` per task. Treat the latest
  `reviews[].comment` as the work order when present.
- `code-review` (needs_work) — prefer **reopen**: set owning task
  `status: pending` and append a `reviews` entry. Create a new `fix-N` task
  only when no existing task owns the finding (files / named task id).
