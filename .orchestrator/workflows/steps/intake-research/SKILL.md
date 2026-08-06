---
name: intake-research
description: "Normalize a research topic and gather a short completeness checklist before synthesize. Use when starting or resuming research intake."
user-invocable: true
---

# Intake Research

**Intent:** Turn the user's topic (and any follow-up answers) into a complete
intake brief. Stay on this step until the checklist is full — do **not** invent
durable `*_state.yaml`. Session state lives in Redis; you only write artifacts.

## Session workspace

- `$CHANGE_ID` / `$ORCHESTRATOR_CHANGE_ID` is the **session id** (not a topic slug).
- The workflow (not the engine) owns artifact placement: use
  `$REPO_ROOT/spec/changes/$CHANGE_ID/` as the workspace dir — create it if
  missing. (Tracked in git, unlike `.orchestrator/`, so research output
  actually reaches the repo.)
- Read/write:
  - `<workspace>/intake.json` — structured checklist (source of truth)
  - `<workspace>/topic.md` — short human-readable brief (written only when complete)

## Checklist (keep small)

| Field      | Meaning               | Example                                    |
| ---------- | --------------------- | ------------------------------------------ |
| `topic`    | What to research      | "postgres indexing for write-heavy OLTP"   |
| `audience` | Who the report is for | "platform engineers and DBAs"              |
| `depth`    | How deep / what shape | "practical ops guide" or "executive brief" |

Optional (fill if the user volunteers; do not block on them): `constraints`, `out_of_scope`.

## Instructions

1. **Load prior intake** — If `<workspace>/intake.json` exists, parse it.
   Merge new facts from the latest user direction (prompt / User direction).
2. **Seed topic** — If `topic` is empty, take it from the latest user text
   (first turn is usually the topic). Do not invent a different topic.
3. **Score completeness** — A field is present when it has a non-empty concrete
   value (not "TBD" / "unknown").
4. **If anything required is missing** — Update `intake.json` with what you have,
   then return `await_input` (same step stays next). Ask for **one** missing
   field at a time. Put the question in `outputs.ask` and the missing keys in
   `outputs.missing`.
5. **If checklist is full** — Write `intake.json` and `topic.md`, then return
   `completed`. Downstream `synthesize-findings` reads these files.

### `intake.json` shape

```json
{
  "topic": "...",
  "audience": "...",
  "depth": "...",
  "constraints": "",
  "out_of_scope": ""
}
```

### `topic.md` (only when completing)

```markdown
# Research Topic

**Topic:** …
**Audience:** …
**Depth:** …
**Session:** <CHANGE_ID>
```

## COMPLETION — still gathering

```text
COMPLETION:
  step_id: intake-research
  status: await_input
  outputs:
    ask: "Who is the audience for this research?"
    missing: [audience]
```

## COMPLETION — ready to advance

```text
COMPLETION:
  step_id: intake-research
  status: completed
  outputs:
    intake_file: <abs path to intake.json>
    topic_file: <abs path to topic.md>
    reason: >
      Checklist complete (topic, audience, depth); artifacts written under the workflow's own workspace dir.
```

## Rules

- Prefer `await_input` over guessing audience/depth.
- Never write `*_state.yaml` under the session folder.
- On re-entry after `await_input`, treat the new user text as the answer to
  the previous `ask` (or as free-form updates), merge into `intake.json`, and
  re-check the checklist.
