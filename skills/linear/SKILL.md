---
name: linear
description: This skill should be used when the user asks to "create a Linear issue", "file a ticket", "open a Linear ticket", "update a Linear issue", "check a Linear ticket", "add a label to a ticket", "assign an issue", "close a ticket", or any time a workflow step invokes create-linear-ticket, check-linear-config, load-context, store-commit-report, or wrap-up with Linear fields. Also use when reading or writing linear-ticket in state.yaml.
user-invocable: true
---

# Linear Issue Tracking
Provides Linear issue management via the `plugin:linear` MCP server, backed by a centralized config at `~/.config/linear/config.yaml`. Used both directly (user-initiated) and by Spec workflow steps.

---

## Configuration

All team settings live in `~/.config/linear/config.yaml`. Read this file before any MCP call.

```yaml
team:
  name: Home Labs
  id: <team-uuid>
  prefix: HL
  project_id: <project-uuid>   # "Tickets" project

repos:
  shell:
    label_ids:
      - <uuid>   # product label for this repo
  algoviz:
    label_ids:
      - <uuid>
  # ... one entry per registered repo
```

**Repo detection**: Run `basename $(git rev-parse --show-toplevel)` to get the repo key.

**Linear disabled**: If the repo is not listed under `repos:`, treat as `--no-linear` — skip all Linear calls without error. Never block on missing config.

---

## MCP Tools

All Linear operations go through the `plugin:linear` MCP server. Use the full tool names as registered:

| Operation | Tool |
|-----------|------|
| Create or update an issue | `mcp__plugin_linear_linear__save_issue` |
| Fetch issue details | `mcp__plugin_linear_linear__get_issue` |
| Get issue status | `mcp__plugin_linear_linear__get_issue_status` |
| List issues | `mcp__plugin_linear_linear__list_issues` |
| Save a comment | `mcp__plugin_linear_linear__save_comment` |
| Delete a comment | `mcp__plugin_linear_linear__delete_comment` |
| List comments | `mcp__plugin_linear_linear__list_comments` |
| Get team info | `mcp__plugin_linear_linear__get_team` |
| Get project info | `mcp__plugin_linear_linear__get_project` |
| List projects | `mcp__plugin_linear_linear__list_projects` |
| List issue statuses | `mcp__plugin_linear_linear__list_issue_statuses` |
| List issue labels | `mcp__plugin_linear_linear__list_issue_labels` |
| Create an issue label | `mcp__plugin_linear_linear__create_issue_label` |
| Get user info | `mcp__plugin_linear_linear__get_user` |
| List users | `mcp__plugin_linear_linear__list_users` |
| List cycles | `mcp__plugin_linear_linear__list_cycles` |
| Get milestone | `mcp__plugin_linear_linear__get_milestone` |
| Save milestone | `mcp__plugin_linear_linear__save_milestone` |
| List milestones | `mcp__plugin_linear_linear__list_milestones` |
| Get document | `mcp__plugin_linear_linear__get_document` |
| List documents | `mcp__plugin_linear_linear__list_documents` |
| Create document | `mcp__plugin_linear_linear__create_document` |
| Update document | `mcp__plugin_linear_linear__update_document` |
| Search documentation | `mcp__plugin_linear_linear__search_documentation` |
| Get attachment | `mcp__plugin_linear_linear__get_attachment` |
| Create attachment | `mcp__plugin_linear_linear__create_attachment` |
| Delete attachment | `mcp__plugin_linear_linear__delete_attachment` |
| Extract images | `mcp__plugin_linear_linear__extract_images` |

---

## Creating a New Issue

1. Read `~/.config/linear/config.yaml` for `team.id`, `team.project_id`, and `repos.<repo>.label_ids`.
2. Detect the repo key: `basename $(git rev-parse --show-toplevel)`.
3. If repo not found in config → skip (--no-linear behavior).
4. Call `mcp__plugin_linear_linear__save_issue` with:
   - `title`: concise description of the change
   - `description`: summary from spec.md, diagnose.md, or user input (markdown supported)
   - `teamId`: `team.id` from config
   - `projectId`: `team.project_id` from config
   - `labelIds`: product label from `repos.<repo>.label_ids` + type label + complexity label
5. Update `$WORKFLOW_STATE_DIR/<feature>/state.yaml` field `linear_ticket_id: HL-XXX` per State Field Registry.

### Required Labels on Every New Ticket

- **Product**: repo name label (UUID from `repos.<name>.label_ids`)
- **Type**: Feature | Bug | Improvement | Chore | Research
- **Complexity**: XS | S | M | L

Use `mcp__plugin_linear_linear__list_issue_labels` to resolve label names to UUIDs when not already known.

---

## Updating an Existing Issue

Call `mcp__plugin_linear_linear__save_issue` with `id` set to the existing ticket ID (e.g. `HL-134`). Pass only the fields to change — the tool performs a partial update.

Common update scenarios:
- Changing status → pass `stateId` (resolve via `list_issue_statuses`)
- Adding a comment → use `save_comment` with `issueId`
- Closing → pass `stateId` for the "Done" or "Cancelled" state

---

## Reading Issue Context (load-context)

When a Spec workflow `load-context` step needs ticket body or state:

```
mcp__plugin_linear_linear__get_issue  { id: "HL-XXX" }
```

The response includes `title`, `description`, `state`, `labels`, `assignee`, and `comments`.

---

## Workflow Integration

Step files under `$ORCHESTRATOR_HOME/steps/` are authoritative for **when** Linear calls run. This skill defines **how** (config lookup, tool selection, field mapping).

Key step files that invoke Linear:
- `create-linear-ticket.yaml` — creates issue at specify phase
- `check-linear-config.yaml` — checks repo registration at bootstrap (informational only, never blocks)

### state.yaml Fields

| Field | Description |
|-------|-------------|
| `linear_ticket_id` | Primary issue ID (e.g. `HL-134`) |

Never invent ticket IDs. Use only values returned from MCP or provided by the user.

---

## Adding a New Repo

To enable Linear for a new repo:

1. Create a product label in Linear for the repo.
2. Add an entry under `repos:` in `~/.config/linear/config.yaml` with the label UUID.
3. Run `/bootstrap` in the repo to verify the config is picked up.
