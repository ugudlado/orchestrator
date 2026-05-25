Follow these steps in order. Do not skip steps.

### Step 1: Reproduce
Write a minimal script or command that triggers the bug. Run it and capture the
output (error message, stack trace, wrong result). This is your reproduction evidence.

If the bug report includes reproduction steps, run them first. If they don't
reproduce, investigate why — the environment or version may differ.

Save the reproduction script/command in the diagnosis document. It must be
copy-pasteable — another developer should be able to run it and see the same failure.

### Step 2: Trace the Root Cause
Read the source code along the execution path from bug trigger to failure point.
Do NOT guess — actually read each function in the call chain.

Identify the EXACT line(s) where the behavior diverges from what the user expects.
Common patterns:
- Wrong type check (isinstance vs type())
- Missing edge case handling
- Incorrect string/path manipulation
- Off-by-one or boundary condition
- Stale state or missing reset

Record the file, line number, and what the code does vs what it should do.

### Step 3: Assess Impact
Check what else calls or depends on the buggy code:
- grep for other callers of the function
- Check if the bug affects other code paths
- Identify existing tests that cover this area (they may need updating)

### Step 4: Document
Write a diagnosis document to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md per
the Diagnosis Format Contract below, containing:
- **Symptom**: What the user sees (from the bug report)
- **Reproduction**: Runnable command/script with expected vs actual output
- **Root cause**: File, line, and explanation of why it's wrong
- **Impact**: Other callers/paths affected
- **Proposed approach**: One-sentence fix direction (not implementation)
- **Unresolved questions**: Anything that needs user input

5. Return COMPLETION per contracts/done-payload.md with
   outputs.discovery_result: {path: "discovery.md"} and artifacts: [discovery.md].
   Do not return the diagnosis as chat prose — the file is the artifact.

---

## Diagnosis Format Contract

The bugfix phase-opening brief lives in `discovery.md` (same filename as feature/spike
`explore` output). Internal structure follows this contract; `diagnose` is the producer
and `design-and-draft-artifacts` / `run-phase-review` are consumers. Only produced in
the bugfix schema.

### Format

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

### Field rules

| Field | Required | Format |
|-------|----------|--------|
| Symptoms | Yes | Prose with concrete evidence (error messages, logs) |
| Reproduction Steps | Yes | Numbered list, must be runnable/followable |
| Expected vs Actual | Yes | Two items: `**Expected**:` and `**Actual**:` |
| Evidence Gathered | Yes | Bulleted list of what was checked |
| Data Flow Trace | Yes | Prose tracing data path to error point |
| Root Cause | Yes | Prose with `file_path:line_number` reference |
| Severity | Yes | One of: `critical`, `high`, `medium`, `low` |
| Affected Areas | Yes | Prose or bulleted list |
| Since When | Yes | Commit/PR/date or "Unknown" |
| Linear Ticket | Yes | `HL-XXX` or `none` |

### Consumers

- `create-or-refresh-artifacts` — reads Root Cause for fix-plan.md generation
- `run-phase-review` — verifies structural compliance and root cause evidence
