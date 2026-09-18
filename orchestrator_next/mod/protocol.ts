/**
 * The shapes `orchestrator <verb> --json` prints (docs/protocol-v2.md §3-§5)
 * and the small pure helpers the driver needs over them.
 *
 * Nothing here touches the engine's `$`; everything is data in, data out, so
 * `register.ts` stays a thin loop.
 */

/** `orchestrator step --json` statuses (protocol.py `step`). */
export type StepStatus =
  | 'ready'
  | 'running'
  | 'done'
  | 'blocked'
  | 'needs_you'
  | 'error'

/** The `payload` of a `kind: judgment` step (protocol.py `_judgment_payload`). */
export type JudgmentPayload = {
  step_id: string
  phase?: string
  attempt?: number
  model?: string
  model_id?: string
  max_turns?: number
  tools?: string[]
  side_effects?: string[]
  system?: string
  in?: Record<string, string>
  out?: Record<string, string>
  out_schema?: Record<string, unknown>
  cwd?: string
  env?: Record<string, string>
}

/**
 * One `show:` artifact's preview in a gate payload (protocol.py `gates.preview`
 * merged with `gates.provenance`): the rendered head plus who stands behind it.
 */
export type GateArtifactPreview = {
  path?: string
  exists?: boolean
  sha256?: string
  head?: string
  produced_by?: string
  producer_status?: string
  attempts?: number
  written_by?: string
  last_verdict?: string
}

/**
 * The `payload` of a `kind: gate` step (protocol.py `_gate_payload`).
 *
 * The gate's own id is `StepResult.step_id`, a sibling of `payload`, not a
 * field of it — `gateOf` copies it in below. `preview.token_name` is the
 * name `orchestrator approve <run> <token>` expects the token bound to;
 * `token` (present once the gate actually minted, i.e. `status: blocked`)
 * is that token's value.
 */
export type GatePayload = {
  step_id: string
  preview: {
    step_id: string
    show: Record<string, GateArtifactPreview>
    token_name: string
  }
  token?: string
  hint?: string
}

/**
 * The `payload` of a `status: needs_you, kind: judgment` await_input step
 * (protocol.py's `step`/`resume` contract): a question for the person, with
 * optional multiple-choice labels. `orchestrator resume <run> "<text>"`
 * matches `text` against `options` (by label or 1-based index) or takes it
 * as free text when there are none.
 */
export type AskPayload = {
  ask: string
  options?: string[]
}

/**
 * The `payload` of a `status: needs_you, kind: judgment` dead end where a
 * node was recorded `abandoned` and nothing downstream can run (protocol.py
 * `step`'s `EXIT_NEEDS_YOU` branch). Unlike `AskPayload` there is no question
 * to answer — only a retry (`reset-step`), an edit, or an abort.
 */
export type AbandonedPayload = {
  reason: string
  abandoned_step: string | null
}

/** One `orchestrator step --json` result. */
export type StepResult = {
  status: StepStatus
  kind?: 'exec' | 'judgment' | 'gate' | null
  step_id?: string | null
  detail?: string
  payload?: JudgmentPayload | GatePayload | AskPayload | AbandonedPayload
}

/**
 * One row of `orchestrator recipes --json` (protocol.py `recipes`).
 *
 * `name` is startable as-is when it is unique across packs; when it is not,
 * `<pack>/<name>` is what `resolve_workflow_ref` accepts. `inputs` is the
 * recipe's own `inputs:` block, which tells the wizard what to ask beyond a
 * slug. A recipe whose YAML would not load carries `error` and zero steps
 * rather than being dropped from the listing.
 */
export type RecipeRow = {
  name: string
  pack: string
  steps: number
  gates: readonly string[]
  inputs: Record<string, unknown>
  error?: string
}

/** One row of `orchestrator status --json` with no run (protocol.py `runs`). */
export type RunRow = {
  run_id: string
  slug: string
  run_status: string
  recipe: string
  current_step: string | null
}

/**
 * The CLI ref for a recipe row: the bare name, or `<pack>/<name>` when the
 * same name lives in more than one pack (which is exactly when a bare ref
 * raises `workflow … is not unique`).
 */
export function recipeRefOf(
  row: RecipeRow,
  rows: readonly RecipeRow[],
): string {
  const collides = rows.filter(other => other.name === row.name).length > 1

  return collides ? `${row.pack}/${row.name}` : row.name
}

/** `orchestrator start --json`: the run's identity plus its first step. */
export type StartResult = {
  run_id: string
  slug: string
  state: string
  next: StepResult
}

/**
 * What `orchestrator done --usage` takes (record.py's guard).
 *
 * All four token counts, plus the model that answered: pricing.py keys its
 * rate lookup on `usage.model`, and with no model it records no cost at all,
 * so dropping it silently zeroed every agent step's `cost_usd`.
 */
export type UsageCounts = {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  /** The id the API reported (TurnUsage.model, d.ts:8709); "" when unknown. */
  model: string
}

/**
 * The `TurnUsage` of a finished turn (claude-code.d.ts:8709, over
 * ModelForkUsage at d.ts:4178) as `done --usage` records it.
 *
 * The two cache counts are billed too, so leaving them out undercounts the
 * step rather than merely losing detail.
 *
 * A turn that got no response carries no usage (d.ts:8418), and record.py
 * refuses a completed agent step with zero tokens, so the caller must decide
 * what to do with a zeroed count rather than have it invented here.
 */
export function usageOf(usage: {
  input_tokens?: number
  output_tokens?: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
  model?: string
} | undefined): UsageCounts {
  return {
    input_tokens: usage?.input_tokens ?? 0,
    output_tokens: usage?.output_tokens ?? 0,
    cache_read_input_tokens: usage?.cache_read_input_tokens ?? 0,
    cache_creation_input_tokens: usage?.cache_creation_input_tokens ?? 0,
    model: usage?.model ?? '',
  }
}

/**
 * Model family regex -> the `$.agent.spawn` `model` alias it maps to.
 *
 * Observed against Claude Code 2.1.274: `$.agent.spawn({model: "claude-sonnet-5"})`
 * was refused with `InputValidationError: model — invalid value; allowed:
 * ["sonnet","opus","haiku","fable"]` — this build's spawn only accepts those
 * four aliases, never a full model id, even though `models.yaml` routes to a
 * full id like `claude-sonnet-5` (protocol.py `_step_model_id`). `spawnModelOf`
 * in register.ts maps a routed id down to its family alias via this table
 * before calling spawn. Kept here (not inlined) so a Python test can grep the
 * emitted `protocol.ts` for these five patterns as a drift guard (see
 * test_pack_export.py).
 */
export const MODEL_FAMILY_TO_SPAWN_ALIAS: ReadonlyArray<{
  family: RegExp
  alias: 'fable' | 'opus' | 'sonnet' | 'haiku'
}> = [
  { family: /^claude-fable-/, alias: 'fable' },
  { family: /^claude-mythos-/, alias: 'fable' },
  { family: /^claude-opus-/, alias: 'opus' },
  { family: /^claude-sonnet-/, alias: 'sonnet' },
  { family: /^claude-haiku-/, alias: 'haiku' },
]

/** `JSON.parse`, kept only if the result is a plain object (not array/null). */
function asObject(text: string): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(text)

    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

/**
 * The last fenced ```json (or bare ```) block of an agent's final message,
 * parsed, or — when no fence parses — the last top-level `{ … }` in the
 * text that does.
 *
 * Fences need not start at column 0 (a subagent hand-back indents every
 * quoted line; `JSON.parse` ignores the resulting surrounding whitespace).
 * Tolerant of a missing `json` language tag or a missing closing fence.
 * Returns undefined when nothing parses to a JSON object: the caller then
 * records the step as `abandoned` rather than guessing an `out`.
 */
export function jsonBlockOf(answer: string): Record<string, unknown> | undefined {
  const fence = /```(?:json)?[ \t]*\r?\n([\s\S]*?)```/g
  const bodies = [...answer.matchAll(fence)].map(m => m[1] ?? '')

  for (let i = bodies.length - 1; i >= 0; i--) {
    const parsed = asObject(bodies[i] ?? '')
    if (parsed !== undefined) {
      return parsed
    }
  }

  // Final fallback: no fence parsed (unterminated, or content ran on the
  // opening fence's own line) — scan from the end for the last top-level
  // `{ … }` and try it. Bounded by the string length, no backtracking.
  let depth = 0
  let end = -1
  for (let i = answer.length - 1; i >= 0; i--) {
    if (answer[i] === '}') {
      if (depth === 0) end = i
      depth++
    } else if (answer[i] === '{') {
      depth--
      if (depth === 0 && end !== -1) {
        const parsed = asObject(answer.slice(i, end + 1))
        if (parsed !== undefined) {
          return parsed
        }
        end = -1
      }
    }
  }

  return undefined
}

/**
 * The prompt a judgment subagent runs with.
 *
 * `$.agent.spawn` takes prompt/subagentType/model/cwd and nothing else
 * (AgentSpawnArgs, d.ts:191), so the charter, the resolved artifact paths and
 * the output contract all ride in this one string. The agent definition
 * `agents/<step_id>.md` that `subagentType` selects carries the tools and the
 * model ceiling.
 */
export function promptOf(payload: JudgmentPayload): string {
  const lines: string[] = [payload.system ?? payload.step_id]

  const inputs = Object.entries(payload.in ?? {})
  if (inputs.length > 0) {
    lines.push('', '## Inputs (read these paths)', ...inputs.map(([k, v]) => `- ${k}: ${v}`))
  }

  const outputs = Object.entries(payload.out ?? {})
  if (outputs.length > 0) {
    lines.push('', '## Outputs (write these paths)', ...outputs.map(([k, v]) => `- ${k}: ${v}`))
  }

  const schema = payload.out_schema ?? {}
  if (Object.keys(schema).length > 0) {
    lines.push('', '## Output schema', '```json', JSON.stringify(schema, null, 2), '```')
  }

  lines.push(
    '',
    'End your final message with exactly one fenced ```json block holding the ' +
      'out object: a key per output above, each artifact key set to the path ' +
      'you wrote. Nothing after it.',
  )

  return lines.join('\n')
}

/**
 * An MCP tool call's arguments, read off the `tool.call` event.
 *
 * `ToolCallInput` is a union discriminated by `tool` (claude-code.d.ts:7916),
 * and an MCP tool nobody declared falls to `McpToolCallInputFallback`
 * (d.ts:4034), whose index signature holds the arguments. A matcher cannot
 * narrow to it here because the plugin's name — and so the tool's full name —
 * is only known at runtime, so this reads the arguments as data instead.
 */
export function argsOf(e: unknown): Record<string, unknown> {
  return typeof e === 'object' && e !== null ? (e as Record<string, unknown>) : {}
}

/** A `tool.call` argument as a string, or "" when absent. */
export function stringArg(e: unknown, name: string): string {
  const value = argsOf(e)[name]

  return typeof value === 'string' ? value : ''
}

/** `payload` narrowed to a judgment step's, or undefined for anything else. */
export function judgmentOf(result: StepResult): JudgmentPayload | undefined {
  return result.kind === 'judgment'
    ? (result.payload as JudgmentPayload | undefined)
    : undefined
}

/**
 * `payload` narrowed to a gate step's, or undefined for anything else.
 *
 * `payload.step_id` is unreliable in practice — `_gate_payload` only nests
 * the id under `payload.preview.step_id`, never at `payload.step_id` itself
 * — so this fills the top-level field in from `StepResult.step_id`, the one
 * the engine always sets, rather than trust whatever (if anything) is on
 * the payload object.
 */
export function gateOf(result: StepResult): GatePayload | undefined {
  if (result.kind !== 'gate' || result.payload === undefined) {
    return undefined
  }

  const payload = result.payload as GatePayload

  return { ...payload, step_id: result.step_id ?? payload.step_id }
}

/**
 * `payload` narrowed to an await_input question, or undefined for anything
 * else. Only meaningful on `status: needs_you`; a `payload.ask` is what
 * distinguishes this from the plain needs_you the loop already reports and
 * stops on.
 */
export function askOf(result: StepResult): AskPayload | undefined {
  if (result.status !== 'needs_you') {
    return undefined
  }

  const payload = result.payload as AskPayload | undefined

  return typeof payload?.ask === 'string' ? payload : undefined
}

/**
 * `payload` narrowed to an abandoned-step dead end, or undefined for
 * anything else. Distinguished from `askOf` by `abandoned_step` rather than
 * `ask`: this is a retry decision, not a question.
 */
export function abandonedOf(result: StepResult): AbandonedPayload | undefined {
  if (result.status !== 'needs_you') {
    return undefined
  }

  const payload = result.payload as AbandonedPayload | undefined

  return typeof payload?.abandoned_step === 'string' ? payload : undefined
}

/** Parses a CLI verb's stdout, or throws with the stderr the process wrote. */
export function parseJson<T>(argv: readonly string[], run: {
  exitCode: number
  stdout: string
  stderr: string
}): T {
  try {
    return JSON.parse(run.stdout) as T
  } catch {
    throw new Error(
      `${argv.join(' ')} exited ${run.exitCode} with unparseable output: ` +
        (run.stderr.trim() || run.stdout.trim() || '(no output)'),
    )
  }
}

/**
 * Why a turn ended (`TurnCompleteReason`, claude-code.d.ts:8432): the model
 * answered, the person interrupted it, the model refused with no fallback
 * model to retry on, or an API error ended it.
 *
 * There is no `awaiting-input` / `paused` member: a permission prompt or a
 * clarifying question does NOT end a turn in this build, so a subagent raises
 * at most one `turn.complete` per run of its loop (`agentId` "each run of its
 * loop one turn", d.ts:8400).
 */
export type TurnCompleteReason = 'answer' | 'aborted' | 'refusal' | 'error'

/**
 * Whether a `turn.complete` is the subagent's FINAL answer, i.e. the one a
 * judgment step may be recorded from.
 *
 * Only `answer` is. The other three all mean the loop stopped without the
 * model finishing its say:
 *
 * - `aborted`   — interrupted (`isAborted`); whatever text exists is partial.
 * - `refusal`   — the model refused and no fallback model retried it.
 * - `error`     — an API error killed the turn (retries exhausted, context
 *                 limit); `usage` may be absent entirely (d.ts:8418).
 *
 * Recording a step from any of those three writes a partial or empty `answer`
 * into `done` as though the agent had finished, which is how a step lands
 * `abandoned` while its work was never actually attempted. The driver keeps
 * waiting instead, and reports the reason rather than inventing an `out`.
 *
 * `isAborted` is checked as well as `reason`: d.ts:8396 ties the two together
 * (`true when the turn ended by interruption`), so a build that set one
 * without the other must not slip through as a final answer.
 */
export function isFinalTurn(turn: {
  reason?: string
  isAborted?: boolean
}): boolean {
  return turn.reason === 'answer' && turn.isAborted !== true
}

/**
 * Whether `$.agent.list()` considers the agent finished (`AgentInfo.status`,
 * claude-code.d.ts:104): `running` means it is still going, anything else
 * (`completed`, `failed`, `killed`, or another engine task status) means it
 * stopped.
 *
 * Belt and braces beside `isFinalTurn`: an agent the listing no longer calls
 * `running` will raise no further `turn.complete`, so waiting on one forever
 * would hang the driver. An id the listing does not name at all answers
 * `false` — a workflow's own agents carry ids no list names (d.ts:141), so
 * absence is not evidence of termination.
 */
export function isAgentFinished(
  agents: readonly { id: string; status: string }[],
  agentId: string,
): boolean {
  const found = agents.find(agent => agent.id === agentId)

  return found !== undefined && found.status !== 'running'
}

/**
 * The `started_at` a judgment step should be recorded with, given when its
 * subagent was spawned.
 *
 * `record.py` defaults `started_at` to `now` when the `done` payload omits it
 * and then derives `duration_ms` from `ended_at - started_at`, so a mod-driven
 * step that never sends one records `started_at == ended_at` and a flat
 * `duration_ms: 0` — for every judgment step, however long it actually ran.
 * (Observed on run 01a0af3f: `explore` attempt 2 spanned 13:11→20:05 and still
 * recorded 0.) An ISO-8601 stamp in the engine's own format is what fixes it.
 */
export function isoStamp(atMs: number): string {
  return new Date(atMs).toISOString().replace(/Z$/, 'Z')
}

/**
 * Whether a parked decision should raise the chat popup (`$.ui.ask`).
 *
 * Only when the pane is CLOSED. With the pane open, both surfaces raise the
 * same decision and the pane's Button wins the race — but `$.ui.ask` cannot be
 * retracted, so the dialog stays on screen after the decision was already
 * made, inviting a second, contradictory answer to a gate that is gone.
 *
 * The pane already draws the gate / retry / question with its own Buttons, so
 * closing that second surface loses nothing: a toast says where to press.
 */
export function shouldRaisePopup(paneIsOpen: boolean): boolean {
  return !paneIsOpen
}

/** The toast that stands in for the popup while the pane is open. */
export function paneOnlyToast(what: string, verb: string): string {
  return `orchestrator: ${what} — press ${verb} in the pane, or run \`/orchestrator ${verb.toLowerCase()}\`.`
}
