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

/** The `payload` of a `kind: gate` step (protocol.py `_gate_payload`). */
export type GatePayload = {
  step_id: string
  show?: string[]
  approve_as?: string
  gate_token?: string | null
  hint?: string
}

/** One `orchestrator step --json` result. */
export type StepResult = {
  status: StepStatus
  kind?: 'exec' | 'judgment' | 'gate' | null
  step_id?: string | null
  detail?: string
  payload?: JudgmentPayload | GatePayload
}

/** `orchestrator start --json`: the run's identity plus its first step. */
export type StartResult = {
  run_id: string
  slug: string
  state: string
  next: StepResult
}

/** The token counts `orchestrator done --usage` takes (record.py's guard). */
export type UsageCounts = {
  input_tokens: number
  output_tokens: number
}

/**
 * The four token counts of `TurnUsage` (claude-code.d.ts:8709, over
 * ModelForkUsage at d.ts:4178) reduced to the two `done --usage` records.
 *
 * A turn that got no response carries no usage (d.ts:8418), and record.py
 * refuses a completed agent step with zero tokens, so the caller must decide
 * what to do with a zeroed count rather than have it invented here.
 */
export function usageOf(usage: {
  input_tokens?: number
  output_tokens?: number
} | undefined): UsageCounts {
  return {
    input_tokens: usage?.input_tokens ?? 0,
    output_tokens: usage?.output_tokens ?? 0,
  }
}

/**
 * The last fenced ```json block of an agent's final message, parsed.
 *
 * Returns undefined when there is no block or it is not a JSON object: the
 * caller then records the step as `abandoned` rather than guessing an `out`.
 */
export function jsonBlockOf(answer: string): Record<string, unknown> | undefined {
  const fence = /```json\s*\n([\s\S]*?)```/g
  let last: string | undefined

  for (const match of answer.matchAll(fence)) {
    last = match[1]
  }

  if (last === undefined) {
    return undefined
  }

  try {
    const parsed: unknown = JSON.parse(last)

    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
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

/** `payload` narrowed to a gate step's, or undefined for anything else. */
export function gateOf(result: StepResult): GatePayload | undefined {
  return result.kind === 'gate' ? (result.payload as GatePayload | undefined) : undefined
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
