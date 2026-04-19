# Default Linear off for this workspace (ISSUE-21)

## Idea
For workspaces where Linear free-tier quota is exhausted, default `linear: false` in `spec/project.yaml` until quota resets. Today every workflow-init calls the Linear API, gets back a quota error, and sets linear_skip_reason before continuing. It's graceful but noisy — 3rd recurrence across autopilot sessions.

Implementation: add a `quota_exceeded_since: <date>` field to the Linear block in project.yaml. workflow-init checks it first; if set and still within 30 days, skip the API call entirely and set `linear_skip_reason` to "quota exhausted (cached)". After 30 days, re-probe automatically.

## Why Now
Wasted API call on every run; the graceful-handling path adds ~5s to workflow-init; noisy in logs. The quota doesn't reset on its own (free-tier workspace capped), so probing is pointless until the user upgrades or clears.

## Prototype
```yaml
# spec/project.yaml
linear:
  team: home-labs
  project: tickets
  quota_exceeded_since: 2026-04-17  # added by workflow-init on first 429
```

## Priority
- User value: 4/10
- Strategic fit: 5/10
- Technical leverage: 6/10
- Effort: XS
- **Score: 4.8**

## Source
spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-21
