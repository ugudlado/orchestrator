# Diagnosis Format Contract

The bugfix phase-opening brief lives in `discovery.md` (same filename as feature/spike
`explore` output). Internal structure follows this contract; `diagnose` is the producer
and `design` / `review` are consumers. Only produced in
the bugfix schema.

Use the template at
`diagnose/templates/bugfix/discovery.md` as the
structural guide.

## Format

```markdown
# Diagnosis: {title}

## Symptoms

{What's broken — error messages, screenshots, logs.}

## Reproduction Steps

1. {Step 1}
2. {Step 2}
3. {Observed failure}

## Expected vs Actual

- **Expected**: {what should happen}
- **Actual**: {what happens instead}

## Investigation

### Evidence Gathered

- {What was checked — logs, git blame, recent changes, config diffs}

### Data Flow Trace

{Trace from input to error point. Where does it diverge from expected?}

## Root Cause

{The actual cause — not symptoms, not guesses.}
Reference: `file_path:line_number`

## Impact

### Severity

{One of: critical, high, medium, low}

### Affected Areas

{Users, features, or systems impacted.}

### Since When

{Commit, PR, or date when introduced. "Unknown" if not determinable.}

## Linear Ticket

{HL-XXX or "none"}
```

## Field rules

| Field              | Required | Format                                              |
| ------------------ | -------- | --------------------------------------------------- |
| Symptoms           | Yes      | Prose with concrete evidence (error messages, logs) |
| Reproduction Steps | Yes      | Numbered list, must be runnable/followable          |
| Expected vs Actual | Yes      | Two items: `**Expected**:` and `**Actual**:`        |
| Evidence Gathered  | Yes      | Bulleted list of what was checked                   |
| Data Flow Trace    | Yes      | Prose tracing data path to error point              |
| Root Cause         | Yes      | Prose with `file_path:line_number` reference        |
| Severity           | Yes      | One of: `critical`, `high`, `medium`, `low`         |
| Affected Areas     | Yes      | Prose or bulleted list                              |
| Since When         | Yes      | Commit/PR/date or "Unknown"                         |
| Linear Ticket      | Yes      | `HL-XXX` or `none`                                  |

## Consumers

- `design` — reads Root Cause for design.md generation
- `review` — verifies structural compliance and root cause evidence
