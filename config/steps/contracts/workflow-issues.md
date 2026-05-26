# Workflow Issues Contract

Authoritative reference for the `workflow_issues` optional field on the
`orchestrator done` payload (`contracts/done-payload.md`). Producers (dispatch
driver, inline scripts via `record-issue.sh`, agents in COMPLETION) emit
issues; `record.py` appends them to `spec/changes/<change_id>/retro.md` via
`config/scripts/inline/append-retro.sh`. Consumers (`/learn` workflow-learner,
complete-workflow renderer) read `retro.md`.

Emission is **best-effort and never-blocking**: malformed payloads, script
failures, or missing env must not fail the step. `record.py` swallows retro
append errors and still records `completed`.

## Payload schema

`workflow_issues` is a JSON **array** of issue objects on the `orchestrator
done` stdin payload. When absent, empty, or not a list, no retro append runs.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | no | Retro heading id (e.g. `ISSUE-3`). When omitted, `append-retro.sh` auto-assigns `ISSUE-N` by scanning existing `## ISSUE-*` headings. |
| `title` | string | no | Short summary in the H2 title. Default: `(no title)`. |
| `category` | string | yes* | Issue classifier — see [Category (seen-so-far)](#category-seen-so-far). *Best-effort: missing values default to `other` at write time. |
| `severity` | string | yes* | Impact level — see [Severity enum](#severity-enum). *Default: `workaround-applied`. |
| `surfaced_at` | string | no | Where/when the issue was observed, usually `phase/step_id` (e.g. `implement/task-T-2`). `record.py` sets `phase/step_id` when omitted. Legacy alias `phase_step` is accepted by `append-retro.sh`. |
| `detail` | string | recommended | What happened — multi-line prose allowed. |
| `workaround` | string | no | What was done to continue the run. |
| `fix_direction` | string | no | Suggested permanent fix for backlog or contract updates. |
| `dedup_key` | string | no | Stable id for deduplication — see [Dedup semantics](#dedup-semantics). Required for driver-detected issues; optional for agent-emitted (hash fallback applies). |
| `ticket_linear` | string | no | Linear ticket id when filed (e.g. `ORC-99`). Written to retro when present. |

Optional fields used in manual backfills but not yet written by
`append-retro.sh` (`backlog_entry`, etc.) may appear in archived `retro.md`
files; new producers should prefer `fix_direction` for follow-up work.

### Example payload fragment

```json
{
  "step_id": "task-T-2",
  "phase": "implement",
  "status": "completed",
  "workflow_issues": [
    {
      "category": "telemetry",
      "severity": "workaround-applied",
      "surfaced_at": "implement/task-T-2",
      "detail": "agent_task_result included agentId but usage block was empty after record",
      "workaround": "Step recorded as completed; cost may be under-reported until JSONL enrichment",
      "fix_direction": "Driver should pass agent_task_result so record.py loads usage from subagent JSONL",
      "dedup_key": "empty-usage:implement:task-T-2"
    }
  ]
}
```

Agents may also list `workflow_issues:` in a COMPLETION block; the dispatch
driver maps them into this payload field verbatim.

## Retro.md layout

Path: `$WORKTREE_PATH/spec/changes/<change_id>/retro.md` (same tree as
workflow artifacts; active runs use the worktree path from state).

### File header (first write)

When `retro.md` does not exist, `append-retro.sh` creates:

```markdown
# Retro: workflow issues surfaced during <change_id>

<!-- Appended by record.py when step payloads include workflow_issues.
     Workflow-improver reads this during run-learn-cycle.
     Format: one H2 per issue, auto-numbered ISSUE-N. -->
```

### Per-issue block (one H2 per issue)

Each issue object becomes one block. Layout matches
`config/scripts/inline/append-retro.sh` (embedded Python, lines 67–87):

```markdown
## <id> — <title>
- **category**: <category>
- **severity**: <severity>
- **surfaced_at**: <surfaced_at>
- **recorded_at**: <UTC ISO8601, set by append-retro.sh at write time>
- **detail**: <detail>          # omitted when empty
- **workaround**: <workaround>  # omitted when empty
- **fix_direction**: <fix_direction>  # omitted when empty
- **ticket_linear**: <ticket>    # omitted when empty
```

After orc-89 T-4, `append-retro.sh` also writes:

```markdown
- **dedup_key**: <dedup_key>      # omitted when empty; used for skip-if-seen
```

`recorded_at` is always UTC `YYYY-MM-DDTHH:MM:SSZ` at append time, not
producer-supplied.

### Parsing notes for consumers

- Issue boundaries: lines matching `^## ISSUE-[0-9]+`.
- Field lines: `- **<key>**: <value>` (single-line values in current writer).
- `/learn` and complete-workflow should treat unknown keys as opaque; do not
  require every optional field to be present.

## Dedup semantics

Duplicate anomalies (e.g. empty usage on retry 2 and 3) must produce **one**
retro block, not one per `orchestrator done` call.

### Producer-supplied `dedup_key`

- Opaque string, stable across retries and re-emits of the same root cause.
- When set, `append-retro.sh` scans existing `retro.md` for a line exactly
  matching `- **dedup_key**: <value>`; if found, the issue is **skipped**
  (`appended` count does not increase).
- When `dedup_key` is omitted, no skip runs (each payload entry may append).

### Hash fallback (when `dedup_key` omitted)

For agent-emitted issues without a key, producers and `append-retro.sh` may
derive:

```
dedup_key = sha256( f"{category}|{surfaced_at}|{detail}" ).hexdigest()[:16]
```

(pipe-separated, UTF-8, SHA-256, first 16 hex chars). Use the same inputs
after `record.py` fills default `surfaced_at`. Prefer explicit keys for
driver-detected issues instead of relying on the hash.

### Documented driver key patterns (orc-89)

| Anomaly | `dedup_key` pattern |
|---------|---------------------|
| Retry then success | `retry-success:<phase>:<step_id>` |
| Empty agent usage | `empty-usage:<phase>:<step_id>` |
| Manual phase advance | `manual-phase-advance:<phase>` |

Inline scripts should pass `--dedup-key` to `record-issue.sh` with a stable,
human-readable key (e.g. `sandbox-block:diagnose:preview-route`).

### Sentinel file

`record-issue.sh` appends one JSON issue object per line to
`$WORKFLOW_STATE_DIR/<change_id>/.pending-issues.jsonl`. The driver drains
this file into `workflow_issues` before `orchestrator done` and removes the
file after exit 0. Dedup in `retro.md` still applies when the same key is
drained on multiple loops.

## Category (seen-so-far)

`category` is an **open string**, not a closed enum — new values are allowed
when existing labels do not fit. Prefer reusing a value below before inventing
one. Values observed in archived `retro.md` and orc-89 design:

| Category | Typical use |
|----------|-------------|
| `telemetry` | Token/cost/usage tracking broken or incomplete |
| `telemetry-helper-drift` | Helper script paths or fields out of date |
| `metrics-accuracy` | Reported metrics disagree with canonical source |
| `driver-bug` | Dispatch driver chose wrong resume/focus/state |
| `driver-contract-ambiguity` | Driver must interpret misleading `orchestrator next` actions |
| `dispatch-bug` | Dispatcher/`record.py` plan advancement incorrect |
| `sandbox-block` | Sandbox denied an operation; step degraded gracefully |
| `workflow-gate-too-strict` | Step contract says non-blocking but script exits non-zero |
| `missing-contract` | Schema references a step contract file that does not exist |
| `contract-drift` | Step contract, design, or COMPLETION protocol inconsistent |
| `agent-contract-drift` | Agent used tools not in declared tools list |
| `quota-exceeded` | External quota (e.g. Linear) blocked optional integration |
| `tooling-bug` | Orchestrator script/CLI defect (non-driver) |
| `other` | Default when no label fits |

## Severity enum

`severity` uses a small fixed vocabulary. Parentheticals in older manual
backfills (e.g. `blocker (silently wrong)`) are prose in the value; prefer the
enum token plus detail in `detail` for new issues.

| Value | Meaning |
|-------|---------|
| `blocker` | Wrong outcome or halted progress without a workaround |
| `workaround-applied` | Run continued via manual intervention or degradation |
| `cosmetic` | No material impact on correctness; telemetry/docs/reporting only |

Default at write time when omitted: `workaround-applied`.

## Producers and consumers

| Role | Action |
|------|--------|
| Dispatch driver (`skills/orchestrate`) | Detect anomalies, drain `.pending-issues.jsonl`, merge agent COMPLETION issues, attach `workflow_issues` to `orchestrator done` |
| `config/scripts/inline/record-issue.sh` | Append one issue line to sentinel JSONL (exit 0 always) |
| Agents | Optional `workflow_issues:` in COMPLETION |
| `record.py` | Best-effort invoke `append-retro.sh`; set `retro_appended` in response |
| `/learn` (workflow-learner) | Read `retro.md` during run-learn-cycle |
| Complete phase | Renderer may include retro summary from same layout |

## Related contracts

- `contracts/done-payload.md` — optional `workflow_issues` field on stdin
- `contracts/auto-commit.md` — per-task commits during implement
