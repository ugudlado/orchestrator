---
name: linear
description: This skill should be used when the user asks to "create a Linear issue", "file a ticket", "open a Linear ticket", "update a Linear issue", "check a Linear ticket", "add a label to a ticket", "assign an issue", "close a ticket", or any time a workflow step invokes create-linear-ticket, check-linear-config, load-context, store-commit-report, or wrap-up with Linear fields. Also use when reading or writing linear-ticket in state.yaml.
user-invocable: true
---

# Linear Issue Tracking

Provides Linear issue management via the `plugin:linear` MCP server. All config is read
from the repo's `spec/project.yaml` — no external config file needed.

---

## Configuration

Read Linear IDs from `spec/project.yaml`:

```yaml
ticketing: linear
linear:
  team_id: <uuid>
  team_prefix: HL
  project_id: <uuid>
  product_label_id: <uuid>  # repo-specific product label
```

**Linear disabled**: If `ticketing:` is not `linear` in `spec/project.yaml`, skip all
Linear calls without error. Never block on missing config.

---

## MCP Tools

All Linear operations go through the `plugin:linear` MCP server:

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
| Search documentation | `mcp__plugin_linear_linear__search_documentation` |
| Get attachment | `mcp__plugin_linear_linear__get_attachment` |
| Create attachment | `mcp__plugin_linear_linear__create_attachment` |
| Delete attachment | `mcp__plugin_linear_linear__delete_attachment` |
| Extract images | `mcp__plugin_linear_linear__extract_images` |

---

## Creating a New Issue

1. Read `spec/project.yaml` for `linear.team_id`, `linear.project_id`, `linear.product_label_id`.
2. If `ticketing:` is not `linear` → skip all Linear calls silently.
3. Call `mcp__plugin_linear_linear__save_issue` with:
   - `title`: concise description of the change
   - `description`: summary from design.md, diagnose.md, or user input (markdown supported)
   - `teamId`: `linear.team_id`
   - `projectId`: `linear.project_id`
   - `labelIds`: `linear.product_label_id` + type label + complexity label
4. Update `$WORKFLOW_STATE_DIR/<feature>/state.yaml` field `linear_ticket_id: HL-XXX`.

### Required Labels on Every New Ticket

- **Product**: `linear.product_label_id` from `spec/project.yaml`
- **Type**: Feature | Bug | Improvement | Chore | Research
- **Complexity**: XS | S | M | L

Use `mcp__plugin_linear_linear__list_issue_labels` to resolve label names to UUIDs when not already known.

---

## Updating an Existing Issue

Call `mcp__plugin_linear_linear__save_issue` with `id` set to the existing ticket ID (e.g. `HL-134`).
Pass only the fields to change — the tool performs a partial update.

Common update scenarios:
- Changing status → pass `stateId` (resolve via `list_issue_statuses`)
- Adding a comment → use `save_comment` with `issueId`
- Closing → pass `stateId` for the "Done" or "Cancelled" state

---

## Reading Issue Context (load-context)

```
mcp__plugin_linear_linear__get_issue  { id: "HL-XXX" }
```

The response includes `title`, `description`, `state`, `labels`, `assignee`, and `comments`.

---

## Workflow Integration

Step files under `$ORCHESTRATOR_HOME/steps/` are authoritative for **when** Linear calls run.
This skill defines **how** (config lookup, tool selection, field mapping).

Key step files that invoke Linear:
- `create-linear-ticket.yaml` — creates issue at specify phase
- `check-linear-config.yaml` — checks ticketing config at bootstrap (informational only, never blocks)

### state.yaml Fields

| Field | Description |
|-------|-------------|
| `linear_ticket_id` | Primary issue ID (e.g. `HL-134`) |

Never invent ticket IDs. Use only values returned from MCP or provided by the user.

---

## Adding a New Repo

To enable Linear for a new repo:

1. Create a product label in Linear for the repo.
2. Run `/bootstrap` in the repo — it will ask for the ticketing backend and embed Linear IDs into `spec/project.yaml`.
