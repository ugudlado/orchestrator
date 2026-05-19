---
name: autopilot
description: "Run one fully-autonomous development iteration. Picks a ticket (from --focus or backlog via ideator), then dispatches /orchestrate with --autopilot. This skill should be used when the user says 'autopilot', 'autonomous', 'self-improve'."
user-invocable: true
args:
  - name: focus
    description: "Optional ticket ID, slug, or focus hint. If provided and it looks like a ticket/slug, use it directly; otherwise pass to ideator as a focus area."
    required: false
---

## Input

$ARGUMENTS

## Overview

`/autopilot` is a thin wrapper that runs **one** complete autonomous feature workflow.

- If `$ARGUMENTS` is empty → invoke `ideate --next` to pick the most valuable backlog item.
- If `$ARGUMENTS` looks like a ticket/slug (matches `^[A-Z]+-\d+$` or `^[a-z][a-z0-9-]*$`) → use it directly.
- Otherwise treat it as a focus hint → invoke `ideate --next --focus "<hint>"`.

Then dispatch `/orchestrate <slug> --autopilot` and let it run to completion. No iteration loop, no session state, no checkpoints — those concepts were removed.

## Execution

### 1. Resolve the slug

```
INPUT = trim($ARGUMENTS)

IF INPUT is empty:
    pick = invoke Skill({ skill: "ideate", args: "--next" })
ELSE IF INPUT matches ^[A-Z]+-\d+$ OR ^[a-z][a-z0-9-]*$:
    SLUG = INPUT
    GOTO step 2
ELSE:
    pick = invoke Skill({ skill: "ideate", args: "--next --focus \"$INPUT\"" })

# Parse the ITEM line from ideator's --next output:
#   ITEM: <ID or title>
#   SCHEMA: <feature|bugfix|chore|spike>
#   REASON: ...
#   PERSISTED: no
SLUG = first line matching `^ITEM:\s*(\S+)` from `pick`

IF SLUG is empty or unparseable:
    HALT — print: "Autopilot: ideator returned no actionable item. Run /ideate manually to seed the backlog."
```

### 2. Dispatch orchestrate with full autonomy

```
Skill({ skill: "orchestrate", args: "$SLUG --autopilot" })
```

`--autopilot` is defined in `config/flags.yaml` and flips `auto=true, agents=true`. The orchestrate skill runs the full feature workflow (specify → implement → complete) without signoff prompts and spawns specialist agents instead of running inline. Do not pause for confirmation.

## What This Skill Does NOT Do

- No iteration loop. One run, one feature.
- No session files, checkpoints, or rollups.
- Does not duplicate orchestrate logic — orchestrate handles preflight, pre-dispatch init, dispatch, and complete.
- Does not pick the ticket itself — defers to ideator (or accepts an explicit slug).

## Failure modes

- **No actionable item** — ideator returns nothing parseable: halt with the message above. User runs `/ideate` to seed backlog.
- **Orchestrate fails mid-run** — surfaces inside orchestrate; this skill exits with whatever orchestrate returns. No retry, no fallback.
