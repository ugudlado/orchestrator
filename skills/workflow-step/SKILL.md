---
name: workflow-step
description: "TeamLead driver loop for buzz-dispatched workflow steps. Use when driving an orchestrator run whose steps are assigned to remote buzz agents: run next, post a plain-language ask, await the completion reply, record done. This skill should be used when the user says 'workflow step', 'drive the run', or a step action carries agent_pubkey."
user-invocable: true
args:
  - name: state
    description: Path to the run's state.yaml
    required: true
---

## Preconditions

- `ORCHESTRATOR_ROSTER=<roster.yaml>` is set so `orchestrator next` enriches
  agent actions with `role`, `agent`, `agent_pubkey`.
- Buzz CLI auth is set: `BUZZ_PRIVATE_KEY` (+ `BUZZ_RELAY_URL` if not the
  default). `BUZZ_CHANNEL` (or an equivalent known channel UUID) identifies
  the run's channel.
- Tools live in `.orchestrator/workflows/lib/buzz/` — `post_ask.py`,
  `await_reply.py`, `render_ask.py`, `asks.yaml`.

## The loop (serial, one step at a time)

Repeat until `orchestrator next <state>` exits nonzero
(1 = complete, 2 = blocked, 3 = error — stop and report):

1. `orchestrator next <state>` → action JSON.
2. **No `agent_pubkey` in the action** → execute the step locally as normal
   (script steps already ran; agent steps: follow the instruction yourself),
   then `orchestrator done`.
3. **`agent_pubkey` present** → dispatch over buzz:
   a. Post the ask (prints the posted event id):
   ```
   python3 .orchestrator/workflows/lib/buzz/post_ask.py \
     --step <step_id> --pubkey <agent_pubkey> --agent-name <agent> \
     --channel $BUZZ_CHANNEL --branch <branch> --repo-url <repo_url> \
     --change-id <change_id> --context-path <context_path>
   ```
   b. Await the reply (writes raw event JSON to `--out`, prints the
   completion YAML block; exit 2 on timeout):
   ```
   python3 .orchestrator/workflows/lib/buzz/await_reply.py \
     --channel $BUZZ_CHANNEL --pubkey <agent_pubkey> \
     --out <tmp>/reply-<step_id>.json --timeout 3600
   ```
   `await_reply` only surfaces replies that are cryptographically genuine
   and authored by the expected pubkey — transport trust is its job, not a
   workflow step's. (`verify-changes`, when the workflow includes it, is an
   ordinary step that checks the WORK on the branch against the requirement;
   it needs nothing special from this loop.)
   c. Map the completion block to a done payload (table below) and pipe it:
   `echo '<payload JSON>' | orchestrator done <state>`.

## Completion → done-payload mapping

| Completion field                             | Done payload field                                        |
| -------------------------------------------- | --------------------------------------------------------- |
| `status: success`                            | `status: "completed"`                                     |
| `status: failed`                             | `status: "failed"`                                        |
| `outputs.*`                                  | `outputs.*` passthrough                                   |
| `reason`                                     | `outputs.reason`; if missing/empty, use `outputs.summary` |
| `usage.input_tokens` / `usage.output_tokens` | `usage.input_tokens` / `usage.output_tokens`              |
| `usage.cost`                                 | `usage.cost_usd`                                          |
| —                                            | `step_id`, `phase` from the action JSON                   |
| —                                            | `agent`: the action's `model` field (tier alias)          |

`outputs.reason` is mandatory on every recorded outcome — if both `reason`
and `outputs.summary` are absent, synthesize one line from the reply.
Valid statuses are `completed` / `recovered` / `failed` / `abandoned`; buzz
completions only ever produce `completed` or `failed`.

## Error paths (never hang, never abort the run)

- **Timeout** (`await_reply` exit 2): record
  `status: "failed"` with `outputs.reason: "no completion reply from <agent> within timeout"`.
- **Malformed fence** (block present but not valid YAML, or missing
  `status`): record `status: "failed"` with the raw reply text (truncated to
  ~2000 chars) as `outputs.reason` — same fallback semantics as buzz's own
  parser.
- **buzz CLI missing / send failed**: fix the environment; do not record a
  step result for an ask that was never posted.

## Rules

- Channel text stays human: plain teammate language only, no step ids, exit
  codes, payload JSON, or orchestrator jargon in the channel. All jargon
  lives in these tools and in `orchestrator next`/`done`.
- Serial: never post the next ask before the previous step is recorded.
- Remote agents work in their own checkout on the run branch and must push
  before replying — the ask templates already say so; if a reply claims done
  but nothing was pushed, treat it as failed with that reason.
