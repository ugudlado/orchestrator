# Workflow Issues Contract

Authoritative reference for the `workflow_issues` optional field on the
`orchestrator done` payload (`contracts/done-payload.md`). Two producers emit
issues:

1. **Agents** (semantic/work issues) — list `workflow_issues:` in their
   COMPLETION block. The driver forwards the field verbatim into the done
   payload.
2. **Driver** (workflow/mechanics issues) — invokes
   `scripts/lib/detect-workflow-issues.sh` after each step. The helper emits a
   JSON array on stdout; the driver merges it into the done payload's
   `workflow_issues` field. **One** helper, called by both the shell driver
   (`scripts/run-workflow.sh`) and the LLM driver (`skills/orchestrate/SKILL.md`).

There is exactly one writer to `retro.md`: `record.py` →
`scripts/inline/append-retro.sh`. The detection helper never writes to
retro.md directly — it only emits JSON for the driver to merge.

Consumers (`/learn` workflow-learner during `run-learn-cycle`,
complete-workflow renderer) read `retro.md`. The learn-cycle agent additionally
hands each unresolved issue to the `backlog-manager` skill for triage.

Emission is **best-effort and never-blocking**: malformed payloads, helper
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
      "category": "retry-success",
      "severity": "workaround-applied",
      "surfaced_at": "implement/task-T-2",
      "detail": "step succeeded on attempt 2 after attempt 1 failed",
      "fix_direction": "investigate root cause of first-attempt failure",
      "dedup_key": "retry-success:implement:task-T-2"
    }
  ]
}
```

Agents list `workflow_issues:` in a COMPLETION block for semantic issues with
their own work; the driver forwards them verbatim. The driver also calls
`scripts/lib/detect-workflow-issues.sh` for workflow-mechanics issues
(retry-success, script-warning, script-failed, tool-crashed,
manual-phase-advance) and merges
the helper's stdout into the same field.

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
`scripts/inline/append-retro.sh` (embedded Python, lines 67–87):

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

Duplicate anomalies (e.g. the same `retry-success` key on attempts 2 and 3)
must produce **one** retro block, not one per `orchestrator done` call.

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

### Documented driver key patterns

`scripts/lib/detect-workflow-issues.sh` emits issues with these dedup_key
patterns. All driver-detected issues must carry a stable key.

| Anomaly | `dedup_key` pattern | Emitted when |
|---------|---------------------|--------------|
| Retry then success | `retry-success:<phase>:<step_id>` | `state.yaml.step_history[-1].attempt > 1 && status == completed` |
| Script warning (soft-fail) | `script-warning:<step_id>` | An inline `run_step` exited 10 (see below) |
| Script failed (hard-fail) | `script-failed:<phase>:<step_id>` | An inline `run_step` exited non-zero (not 10) |
| Tool crashed | `tool-crashed:<phase>:<step_id>` | Agent-tool invocation exited non-zero |
| Manual phase advance | `manual-phase-advance:<phase>` | LLM driver passes `--manual-phase-advance PHASE` after patching state outside `orchestrator done` |

### Inline-script soft-fail (exit 10)

When an inline `run_step` script wants to flag a workflow issue without
aborting the run, it exits with code **10**. The shell driver maps exit 10 to
`status: completed` plus one `script-warning` `workflow_issues` entry whose
`detail` is the last 5 lines of the script's stderr. Any other non-zero exit
code is a hard failure (`status: failed`) plus one `script-failed`
`workflow_issues` entry (same stderr tail rule, `dedup_key`
`script-failed:<phase>:<step_id>`).

Exit codes 10–19 are reserved for future soft-warning variants (e.g. distinct
"degraded but continued" categories). Codes 1–7 retain their existing
hard-failure meanings throughout `run-workflow.sh`; codes 8–9 are unused.

Inline scripts emit the issue purely by their exit code — no helper invocation,
no sentinel file, no JSON construction in the script itself. The driver builds
the `workflow_issues` entry from the exit-code + stderr signal.

## Category (seen-so-far)

`category` is an **open string**, not a closed enum — new values are allowed
when existing labels do not fit. Prefer reusing a value below before inventing
one. Values observed in archived `retro.md` and orc-89 design:

| Category | Typical use |
|----------|-------------|
| `retry-success` | Step succeeded after a previous attempt failed |
| `script-warning` | Inline `run_step` exited 10 (soft-fail); detail carries last 5 lines of stderr |
| `script-failed` | Inline `run_step` hard-failed (exit not 0 or 10); detail carries last 5 lines of stderr |
| `tool-crashed` | Agent-tool invocation exited non-zero |
| `manual-phase-advance` | Driver patched phase outside `orchestrator done` |
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
| Driver (shell `run-workflow.sh` or LLM `skills/orchestrate`) | Invoke `scripts/lib/detect-workflow-issues.sh`; merge its stdout with agent COMPLETION issues; attach `workflow_issues` to `orchestrator done` |
| `scripts/lib/detect-workflow-issues.sh` | Single source of detection logic; emits JSON array on stdout; never writes retro.md |
| Inline `run_step` scripts | Exit 10 for soft-fail (driver synthesizes `script-warning`); other non-zero = hard fail |
| Agents | Optional `workflow_issues:` in COMPLETION for semantic/work issues |
| `record.py` | Best-effort invoke `append-retro.sh`; set `retro_appended` in response (sole writer to retro.md) |
| `run-learn-cycle` (workflow-learner) | Read `retro.md`; hand each unresolved issue to the `backlog-manager` skill for triage |
| Complete phase | Renderer may include retro summary from same layout |

## Related contracts

- `contracts/done-payload.md` — optional `workflow_issues` field on stdin
- `contracts/auto-commit.md` — per-task commits during implement
