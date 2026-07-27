# Diagnose

**Intent:** Reproduce the bug with runnable evidence, trace the exact root cause, and document findings.

## Inputs

- Ticket body at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
  (`spec/changes/<slug>/ticket-context.md`) when present — written by
  `load-ticket-context`. That is the bug report / ticket scope.

## Outputs

- `discovery_result` — COMPLETION output handle: `{path: "discovery.md"}`.
- Artifact: `discovery.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md`.

## Instructions

Follow these steps in order. Do not skip steps.

Read `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
(`spec/changes/<slug>/ticket-context.md`) first when it exists — that is the
ticket/bug report. Do not invent a different bug from the codebase.

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

Producing `discovery.md`? First Read
`skills/diagnose/reference/diagnosis-format.md` — it
holds the required section structure, field rules, template pointer, and
consumers. Write the diagnosis to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md`
with these sections (detail per that contract):

- **Symptoms** — what the user sees (from the bug report)
- **Reproduction Steps** — runnable command/script with expected vs actual output
- **Expected vs Actual**
- **Investigation** — Evidence Gathered + Data Flow Trace
- **Root Cause** — file, line, and why it's wrong
- **Impact** — Severity + Affected Areas + Since When
- **Linear Ticket**

List any unresolved questions explicitly. Do not propose a fix.

Then return COMPLETION:

```
COMPLETION:
  status: completed
  outputs:
    discovery_result: {path: "discovery.md"}
  artifacts: [discovery.md]
```

Do not return the diagnosis as chat prose — the file is the artifact.

### Rules (constraints on how)

- Reproduction MUST be runnable — a command or script, not just a description.
- Root cause must identify the EXACT line(s) where behavior diverges from expected.
- Do not propose a fix during diagnosis. Diagnosis and fix are separate concerns.
- For codebase-wide pattern bugs: search the ENTIRE source tree (including gitignored source dirs), cross-check catalog count against `find + grep + wc -l`.
- Catalog count mismatch protocol: if fresh grep count differs from earlier count by >0, update the impact assessment to use the fresh count. If counts differ by >20%, investigate the discrepancy (new files? false positives?) before proceeding.

## Verify

- Diagnosis document written to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md
- Root cause confirmed with evidence (file path + line number + explanation)
- Reproduction is a runnable command or script, not just prose
- Unresolved questions explicitly listed (not hidden)
- If pattern-based bug: catalog count matches `find + grep` count across entire source tree
