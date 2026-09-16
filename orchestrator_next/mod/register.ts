import type { EngineInterface, On } from 'claude-code'

import {
  argsOf,
  gateOf,
  judgmentOf,
  jsonBlockOf,
  parseJson,
  promptOf,
  stringArg,
  usageOf,
  type GatePayload,
  type JudgmentPayload,
  type StartResult,
  type StepResult,
  type UsageCounts,
} from './protocol'

/** One `$.process.run` of the `orchestrator` CLI, with the argv it used. */
type Ran = {
  argv: readonly string[]
  exitCode: number
  stdout: string
  stderr: string
}

/** Runs the CLI: the only kind of process this module ever starts. */
type Cli = (argv: readonly string[], timeoutMs?: number) => Promise<Ran>

/**
 * The orchestrator Claude Mod: two tools (`run`, `status`) that drive a pack
 * recipe through the `orchestrator` CLI, spawning one subagent per judgment
 * step and asking the person at each gate.
 *
 * The CLI is the only process this module starts: every decision about what
 * runs next comes from `orchestrator step --json` (docs/protocol-v2.md §3).
 *
 * @param on the engine's registrar
 */

/**
 * A pack's `models.yaml` tier alias to the `model` `$.agent.spawn` takes
 * (AgentSpawnInput.model, claude-code.d.ts:234: an alias or a full id).
 * Mirrors ALIAS_TO_CLAUDE_MODEL in pack_export.py.
 */
const MODELS: Record<string, string> = {
  strong: 'opus',
  standard: 'sonnet',
  fast: 'haiku',
  code: 'sonnet',
}

/**
 * The plugin's own name, from one of the tool names the engine handed back.
 * `mcp__<plugin>__<tool>`; anything else yields `''` and leaves types bare.
 */
function pluginOf(tool: string): string {
  const match = /^mcp__(.+)__[^_]+$/.exec(tool)

  return match === null ? '' : match[1]
}

/**
 * The agent type for a step: the pack's agents load under `<plugin>:<id>`,
 * so that is what `$.agent.spawn` is asked for whenever the plugin's name is
 * known. Falls back to the bare id only when it is not.
 */
function agentTypeOf(stepId: string): string {
  return state.plugin === '' ? stepId : `${state.plugin}:${stepId}`
}

/** Exec steps batch inside one `step` call, so give the child ten minutes. */
const STEP_TIMEOUT_MS = 600_000

/**
 * Tools a gated run refuses inside our subagents until a gate is approved.
 * `MultiEdit` is not a tool in this engine's declarations, so it is not named.
 */
const WRITE_TOOLS = ['Edit', 'Write', 'NotebookEdit'] as const

/** Bash commands that publish work, refused on the same terms. */
const PUBLISHING_COMMAND = /\bgit\s+(push|commit)\b/

const DENY_REASON = 'orchestrator: write needs an approved gate'

/**
 * Narrows the serving hook to this plugin's own tools. A matcher cannot
 * spell the plugin's name (it comes from the manifest at load), so it
 * matches the shape and the hook checks the exact names `$.tool.register`
 * resolved.
 */
const SERVED_TOOL = /^mcp__[^_]*orchestrator[^_]*__(run|status)$/

/** What `$.store` holds for a live run, under `run:<slug>`. */
type RunRecord = {
  run: string
  slug: string
  lastStep: string | null
  status: string
}

/**
 * This module's state, at module scope because the engine only follows `$`
 * into a function declared at the top of the file: every helper that takes
 * `$` is hoisted, and reaches the run through here rather than a closure.
 */
const state = {
  /** Agent ids this module spawned, mapped to the resolver of their answer. */
  waiting: new Map<string, (turn: { answer: string; usage: UsageCounts }) => void>(),

  /** Every agent id this module spawned, so the deny hook knows its own. */
  ours: new Set<string>(),

  /** The run this session is driving, if any: also the deny hook's switch. */
  active: null as { run: string; slug: string } | null,

  /** A gate token the person approved; cleared when the next gate arrives. */
  gateToken: null as string | null,

  /**
   * The full names the engine gave this plugin's tools, `mcp__<plugin>__<name>`
   * (`$.tool.register` resolves to `{ tool }`, claude-code.d.ts:4779). The
   * plugin's name is its manifest's, so nothing here guesses it.
   */
  served: { run: '', status: '' },

  /**
   * This plugin's name, read off a served tool name (`mcp__<plugin>__run`).
   * A pack's agents load namespaced as `<plugin>:<step id>`, and
   * `subagentType` takes the resolved name exactly (AgentSpawnInput,
   * claude-code.d.ts:224), so a bare step id either misses or, worse,
   * case-folds onto a built-in: asking for `explore` silently ran the
   * engine's own `Explore` while `design` refused outright.
   */
  plugin: '',
}

export function register(on: On) {
  // --- tool registration ---------------------------------------------------

  // `$.tool.register` rejects until the session binds, so it happens here
  // (ToolSpec, claude-code.d.ts:8262).
  on('session.start', async ($, e, next) => {
    const runTool = await $.tool.register({
      name: 'run',
      description:
        'Run an orchestrator recipe end to end: seeds the run, executes its ' +
        'script steps, spawns one subagent per judgment step, and asks you at ' +
        'each gate. Returns the final status and artifacts.',
      inputSchema: {
        type: 'object',
        properties: {
          recipe: {
            type: 'string',
            description: 'Recipe name, or <pack>/<recipe> when ambiguous.',
          },
          slug: {
            type: 'string',
            description: 'Short identifier for this run (a ticket id works).',
          },
          inputs: {
            type: 'object',
            description: "Optional seed inputs, recorded as the run's inputs.",
          },
        },
        required: ['recipe', 'slug'],
      },
    })

    const statusTool = await $.tool.register({
      name: 'status',
      description:
        "Report an orchestrator run's nodes, usage and cost. Takes the run id " +
        'or slug; with none, the run this session started.',
      inputSchema: {
        type: 'object',
        properties: {
          run: { type: 'string', description: 'Run id or slug.' },
        },
      },
    })

    state.served.run = runTool.tool
    state.served.status = statusTool.tool
    state.plugin = pluginOf(runTool.tool)

    $.ui.status('orchestrator: idle')

    return next(e)
  })

  // --- write gating --------------------------------------------------------

  // Registered before the serving hook so it sits outside it in the onion: a
  // write inside one of our subagents is refused while a run is state.active and no
  // gate has been approved. The main session is untouched — `e.agentId`
  // (ToolCallInput/AgentLoop, claude-code.d.ts:7916) must be one we spawned.
  // A matcher is required: the engine refuses two bare `on('tool.call')`
  // registrations in one module, so each names the tools it serves.
  on('tool.call', { tool: [...WRITE_TOOLS, 'Bash'] }, ($, e, next) => {
    const agentId = e.agentId

    if (state.active === null || state.gateToken !== null || agentId === undefined) {
      return next(e)
    }

    if (!state.ours.has(agentId)) {
      return next(e)
    }

    const isPublish =
      e.tool !== 'Bash' || PUBLISHING_COMMAND.test(String(e.command ?? ''))

    return isPublish ? { deny: DENY_REASON } : next(e)
  })

  // A subagent's answer arrives as its own `turn.complete`, keyed by the
  // `agentId` `$.agent.spawn` resolved (claude-code.d.ts:287-291).
  on('turn.complete', ($, e, next) => {
    const agentId = e.agentId

    if (agentId !== undefined) {
      state.waiting.get(agentId)?.({ answer: e.answer, usage: usageOf(e.usage) })
    }

    return next(e)
  })

  // --- the driver ----------------------------------------------------------

  on('tool.call', { tool: SERVED_TOOL }, async ($, e, next) => {
    if (e.tool !== state.served.run && e.tool !== state.served.status) {
      // Another plugin's `run`/`status`: the names this one registered are the
      // only ones it answers.
      return next(e)
    }

    const cli = async (argv: readonly string[], timeoutMs = 60_000) => {
      const ran = await $.process.run(argv, { timeoutMs })

      return { argv, ...ran }
    }

    const args = argsOf(e)

    if (e.tool === state.served.status) {
      const ref = stringArg(e, 'run') || state.active?.run || ''

      if (ref === '') {
        return { result: 'orchestrator: no run in this session; pass `run`.' }
      }

      const ran = await cli(['orchestrator', 'status', ref, '--json'])

      return { result: ran.stdout || ran.stderr }
    }

    const recipe = stringArg(e, 'recipe')
    const slug = stringArg(e, 'slug')

    if (recipe === '' || slug === '') {
      return { result: 'orchestrator: `recipe` and `slug` are both required.' }
    }

    const startArgv = ['orchestrator', 'start', recipe, slug, '--json']

    if (args.inputs !== undefined && args.inputs !== null) {
      startArgv.push('--inputs', JSON.stringify(args.inputs))
    }

    const started = await cli(startArgv, STEP_TIMEOUT_MS)

    let start: StartResult

    try {
      start = parseJson<StartResult>(started.argv, started)
    } catch (error) {
      return { result: `orchestrator start failed: ${String(error)}` }
    }

    state.active = { run: start.run_id, slug: start.slug }
    state.gateToken = null

    try {
      return { result: await drive($, cli, start, start.next) }
    } finally {
      state.active = null
      state.gateToken = null
      state.ours.clear()
      state.waiting.clear()
      $.ui.status('orchestrator: idle')
    }
  })
}

/**
 * The loop: report each step, act on it, ask the CLI for the next one.
 *
 * Every branch either advances the run (judgment, gate) or returns, so a
 * status the engine cannot act on (`needs_you`, `error`, `done`) ends the
 * tool call with what the CLI last said.
 */
async function drive(
  $: EngineInterface,
  cli: Cli,
  start: StartResult,
  first: StepResult,
): Promise<string> {
  const run = start.run_id
  let result = first
  let lastStderr = ''

  for (;;) {
    const stepId = result.step_id ?? null

    $.ui.status(`orchestrator: ${start.slug} ${stepId ?? '-'} ${result.status}`)

    await $.store
      .set(`run:${start.slug}`, {
        run,
        slug: start.slug,
        lastStep: stepId,
        status: result.status,
      } satisfies RunRecord)
      .catch(() => undefined)

    if (result.status === 'done') {
      const ran = await cli(['orchestrator', 'status', run, '--json'])

      return `orchestrator: ${start.slug} complete.\n${ran.stdout || ran.stderr}`
    }

    if (result.status === 'needs_you' || result.status === 'error') {
      return (
        `orchestrator: ${start.slug} stopped (${result.status}).\n` +
        `${JSON.stringify(result, null, 2)}\n` +
        (lastStderr === '' ? '' : `stderr: ${lastStderr}`)
      )
    }

    const judgment = judgmentOf(result)

    if (judgment) {
      const done = await runJudgment($, cli, run, judgment)

      lastStderr = done.stderr
      result = done.next

      continue
    }

    const gate = gateOf(result)

    if (gate) {
      const answered = await runGate($, cli, run, gate)

      lastStderr = answered.stderr

      if (answered.next === null) {
        if (answered.unattended === true) {
          return (
            `orchestrator: ${start.slug} is waiting at gate ${gate.step_id}; ` +
            'there is nobody to ask in this session, so the run is left ' +
            'standing. Approve it from a shell:\n' +
            `  orchestrator status ${start.slug} --json\n` +
            `  orchestrator approve ${start.slug} <token>\n` +
            'then run this recipe on the same slug again to resume.'
          )
        }

        return (
          `orchestrator: ${start.slug} cancelled at gate ${gate.step_id}.` +
          (answered.stderr === '' ? '' : `\nstderr: ${answered.stderr}`)
        )
      }

      result = answered.next

      continue
    }

    // A `blocked` with no gate payload, or any other status the engine did
    // not pair with work: nothing to act on, so report it rather than spin.
    return (
      `orchestrator: ${start.slug} ${result.status} with nothing to do.\n` +
      JSON.stringify(result, null, 2)
    )
  }
}

/** Spawn the step's subagent, await its answer, record it, return the next step. */
async function runJudgment(
  $: EngineInterface,
  cli: Cli,
  run: string,
  payload: JudgmentPayload,
): Promise<{ next: StepResult; stderr: string }> {
  const stepId = payload.step_id

  const spawned = await $.agent.spawn({
    subagentType: agentTypeOf(stepId),
    model: payload.model === undefined ? undefined : MODELS[payload.model],
    cwd: payload.cwd,
    description: stepId,
    prompt: promptOf(payload),
  })

  if (spawned.deny !== undefined || spawned.agentId === undefined) {
    return recordAbandoned(cli, run, stepId, `spawn refused: ${spawned.deny ?? 'no agent id'}`)
  }

  const agentId = spawned.agentId
  state.ours.add(agentId)

  const turn = await new Promise<{ answer: string; usage: UsageCounts }>(resolve => {
    state.waiting.set(agentId, resolve)
  }).finally(() => {
    state.waiting.delete(agentId)
  })

  const out = jsonBlockOf(turn.answer)

  if (out === undefined) {
    return recordAbandoned(
      cli,
      run,
      stepId,
      'the subagent ended without a fenced ```json block',
      turn.usage,
    )
  }

  return recordDone(cli, run, stepId, out, turn.usage, 'completed')
}

/**
 * Record a judgment step's result and return where the run stands after it.
 *
 * `done --status abandoned` skips the `out` contract check (protocol.py
 * `done`), so a step the harness could not complete records its reason in
 * `out.reason` instead of a contract-shaped payload.
 */
async function recordDone(
  cli: Cli,
  run: string,
  stepId: string,
  out: Record<string, unknown>,
  usage: UsageCounts,
  status: 'completed' | 'abandoned',
): Promise<{ next: StepResult; stderr: string }> {
  const ran = await cli(
    [
      'orchestrator',
      'done',
      run,
      stepId,
      '--out',
      JSON.stringify(out),
      '--usage',
      JSON.stringify(usage),
      '--status',
      status,
      '--json',
    ],
    STEP_TIMEOUT_MS,
  )

  return nextOf(cli, run, ran)
}

/** `recordDone` for a step nothing usable came back from. */
const recordAbandoned = (
  cli: Cli,
  run: string,
  stepId: string,
  reason: string,
  usage: UsageCounts = { input_tokens: 0, output_tokens: 0 },
) => recordDone(cli, run, stepId, { reason }, usage, 'abandoned')

/**
 * Ask the person, then approve or cancel through the CLI.
 *
 * `$.ui.ask` returns the label chosen or free text typed under Other
 * (claude-code.d.ts:1908), so the answer is compared with the labels
 * exactly and anything else counts as a cancel. It rejects in a `-p` run,
 * which the catch turns into a cancel too.
 */
async function runGate(
  $: EngineInterface,
  cli: Cli,
  run: string,
  gate: GatePayload,
): Promise<{ next: StepResult | null; stderr: string; unattended?: boolean }> {
  const shown = (gate.show ?? []).join(', ')

  const question =
    `Approve ${gate.step_id}?` + (shown === '' ? '' : ` (review: ${shown})`)

  // `$.ui.ask` rejects both when the person dismisses it and in a `-p` run
  // where there is nobody to ask (claude-code.d.ts:1908). Those are not the
  // same answer: a dismissal is a cancel, but an unattended run must leave
  // the gate standing for `orchestrator approve` from a shell rather than
  // destroy the run. Neither case is consent, so only the literal label is.
  let answer: string

  try {
    answer = await $.ui.ask(question, {
      options: ['approve', 'cancel'],
      header: 'gate',
    })
  } catch {
    return { next: null, stderr: '', unattended: true }
  }

  if (answer !== 'approve') {
    const ran = await cli(['orchestrator', 'cancel', run, '--json'])

    return { next: null, stderr: ran.stderr.trim() }
  }

  state.gateToken = gate.gate_token ?? gate.approve_as ?? gate.step_id

  const ran = await cli([
    'orchestrator',
    'approve',
    run,
    state.gateToken,
    '--json',
  ])

  const advanced = await nextOf(cli, run, ran)

  // The token only unlocks writes for the steps this gate opened; the next
  // gate re-arms the deny hook.
  state.gateToken = null

  return advanced
}

/**
 * The step a `done` / `approve` call left the run at.
 *
 * Both verbs print `{..., "next": <step result>}`; when the verb is missing
 * or failed (Phase 3 lands `approve`/`cancel`), fall back to asking `step`
 * so the loop keeps its own reading of the run rather than the CLI's error.
 */
async function nextOf(
  cli: Cli,
  run: string,
  ran: Ran,
): Promise<{ next: StepResult; stderr: string }> {
  const stderr = ran.stderr.trim()

  try {
    const parsed = parseJson<{ next?: StepResult }>(ran.argv, ran)

    if (parsed.next) {
      return { next: parsed.next, stderr }
    }
  } catch {
    // Fall through: `step` is the authority on where the run stands.
  }

  const stepped = await cli(['orchestrator', 'step', run, '--json'], STEP_TIMEOUT_MS)

  try {
    return { next: parseJson<StepResult>(stepped.argv, stepped), stderr }
  } catch (error) {
    return {
      next: { status: 'error', step_id: null, detail: String(error) },
      stderr: stderr === '' ? stepped.stderr.trim() : stderr,
    }
  }
}
