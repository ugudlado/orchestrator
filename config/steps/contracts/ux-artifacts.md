# UX Artifact Contract

The `ux-design` step produces visual prototypes and critique feedback. This contract
defines the artifact format so downstream steps (task generation, implementation) can
reference approved designs instead of working from text-only specs.

## Artifacts

| File | Required | Producer | Format |
|------|----------|----------|--------|
| `ux-prototype.html` | Yes (when ux-design runs) | `ux-design` | Self-contained HTML file from /playground or /frontend-design |
| `ux-artifacts.yaml` | Yes (when ux-design runs) | `ux-design` | Manifest with artifact metadata |

## ux-artifacts.yaml Format

```yaml
prototype:
  file: ux-prototype.html
  description: "<one-line description of the design>"
  options_considered: <number of options generated>
  selected_option: <which option was chosen (1-indexed)>
  critique_status: "<passed|passed-with-fixes|skipped>"
  critique_rounds: <number of /critique iterations>
```

## Field Rules

| Field | Required | Format |
|-------|----------|--------|
| `prototype.file` | Yes | Always `ux-prototype.html` |
| `prototype.description` | Yes | One-line summary of the visual direction |
| `prototype.options_considered` | Yes | Integer >= 1 |
| `prototype.selected_option` | Yes | Integer, 1-indexed |
| `prototype.critique_status` | Yes | One of: `passed`, `passed-with-fixes`, `skipped` |
| `prototype.critique_rounds` | Yes | Integer >= 0 |

## Graceful Degradation

When `ux_design=false` or the ux-design step was filtered out:
- `ux-artifacts.yaml` does not exist
- Downstream steps MUST check for `ux-artifacts.yaml` existence before reading
- Missing UX artifacts is a normal condition, not an error

## Consumers

- `create-or-refresh-artifacts` — reads `ux-artifacts.yaml` to create UI-specific tasks referencing `ux-prototype.html`
- `execute-next-task` — developer reads `ux-prototype.html` as visual reference when implementing UI tasks
- `run-phase-review` — verifies UX artifacts exist when ux-design step completed
- `run-feature-verification` — verifies implementation matches prototype direction
