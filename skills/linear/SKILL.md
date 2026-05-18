---
name: linear
description: This skill should be used when the user asks to "create a Linear issue", "file a ticket", "open a Linear ticket", "update a Linear issue", "check a Linear ticket", "add a label to a ticket", "assign an issue", "close a ticket", or any time a workflow step needs to create/read a Linear ticket. Also use when reading or writing linear_ticket_id in state.yaml.
user-invocable: true
---

# Linear Issue Tracking

Provides Linear issue management via the `plugin:linear` MCP server.

Linear is driven entirely by the presence of a `linear:` block in the repo's
`spec/project.yaml`. There is no separate config step and no flag — if the
block is present, use it; if it is absent, skip every Linear call silently and
never block.

```yaml
linear:
  team_id: <uuid>
  team_prefix: HL
  project_id: <uuid>
  product_label_id: <uuid>  # repo-specific product label
```

If `spec/project.yaml` has no `linear:` block (or no file), do nothing — this
is the normal state for repos that use the `backlog` CLI instead.

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

1. Read `spec/project.yaml`. If there is no `linear:` block → skip silently.
2. Call `mcp__plugin_linear_linear__save_issue` with:
   - `title`: concise description of the change
   - `description`: summary from design.md, diagnose.md, or user input (markdown supported)
   - `teamId`: `linear.team_id`
   - `projectId`: `linear.project_id`
   - `labelIds`: `linear.product_label_id` + type label + complexity label
3. Update `$WORKFLOW_STATE_DIR/<feature>/state.yaml` field `linear_ticket_id: HL-XXX`.

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

## Reading Issue Context

```
mcp__plugin_linear_linear__get_issue  { id: "HL-XXX" }
```

The response includes `title`, `description`, `state`, `labels`, `assignee`, and `comments`.

---

## state.yaml Fields

| Field | Description |
|-------|-------------|
| `linear_ticket_id` | Primary issue ID (e.g. `HL-134`) |

Never invent ticket IDs. Use only values returned from MCP or provided by the user.

---

## Adding a New Repo

Add a `linear:` block to the repo's `spec/project.yaml` (team_id, team_prefix,
project_id, product_label_id) and create a matching product label in Linear.
No other wiring is required — the skill keys off that block alone.
