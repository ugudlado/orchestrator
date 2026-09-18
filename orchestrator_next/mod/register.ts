import type { EngineInterface, On } from 'claude-code'

import {
  APPROVE_KEY,
  AUTO_OPEN_MIN_COLUMNS,
  CANCEL_KEY,
  COMMAND_NAME,
  INITIAL_MODEL,
  OPEN_MIN_COLUMNS,
  PANE_ID,
  PANE_TITLE,
  REFRESH_EVERY_MS,
  RETRY_KEY,
  isOnPaneSurface,
  nodeLineOf,
  paneView,
  type DriverPhase,
  type NodeUsage,
  type PaneModel,
  type StatusJson,
} from './pane'
import {
  abandonedOf,
  argsOf,
  askOf,
  gateOf,
  judgmentOf,
  jsonBlockOf,
  MODEL_FAMILY_TO_SPAWN_ALIAS,
  parseJson,
  promptOf,
  stringArg,
  usageOf,
  type AbandonedPayload,
  type AskPayload,
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
 * Fallback only: a pack's `models.yaml` tier alias to a Claude Code model
 * alias (AgentSpawnInput.model, claude-code.d.ts:234: an alias or a full
 * id), used when a step's payload carries no `model_id`. The normal path
 * spawns on `payload.model_id` — the actual routed id (e.g. `claude-opus-5`)
 * from models.yaml, resolved by the CLI (protocol.py `_step_model_id`) — so
 * the subagent runs exactly the configured model and its usage.model lines
 * up with pricing.yaml. Mirrors ALIAS_TO_CLAUDE_MODEL in pack_export.py.
 */
const MODELS: Record<string, string> = {
  strong: 'opus',
  standard: 'sonnet',
  fast: 'haiku',
  code: 'sonnet',
  fable: 'fable',
  opus: 'opus',
  sonnet: 'sonnet',
}

/**
 * The `$.agent.spawn` `model` to pass for a step's payload.
 *
 * Verified against Claude Code 2.1.274: spawn's `model` only accepts the four
 * aliases `"sonnet" | "opus" | "haiku" | "fable"` — passing a full routed id
 * like `claude-sonnet-5` (what `payload.model_id` carries, per
 * protocol.py `_step_model_id`) was refused outright:
 * `InputValidationError: model — invalid value; allowed:
 * ["sonnet","opus","haiku","fable"]`.
 *
 * `payload.model_id` is the actual routed id from models.yaml, so it takes
 * priority: it is mapped down to its family's alias via
 * `MODEL_FAMILY_TO_SPAWN_ALIAS`. When it is absent or its family is unknown,
 * fall back to the pack's tier alias (`payload.model`, e.g. "strong") via
 * `MODELS`. When neither resolves, `undefined` is returned so `$.agent.spawn`
 * falls back to the spawned agent definition's own `model:` frontmatter.
 *
 * The *actual* model that answered (from `turn.complete`'s usage) is still
 * what gets recorded via `done --usage`, so pricing stays exact regardless of
 * what alias spawn was asked for.
 */
function spawnModelOf(payload: JudgmentPayload): string | undefined {
  const modelId = payload.model_id ?? ''

  if (modelId !== '') {
    const hit = MODEL_FAMILY_TO_SPAWN_ALIAS.find(({ family }) => family.test(modelId))

    if (hit !== undefined) {
      return hit.alias
    }
  }

  return payload.model === undefined ? undefined : MODELS[payload.model]
}

/**
 * The plugin's own name, from one of the tool names the engine handed back.
 * `mcp__<plugin>__<tool>`; anything else yields `''` and leaves types bare.
 */
function pluginOf(tool: string): string {
  const match = /^mcp__(.+)__[^_]+$/.exec(tool)

  return match?.[1] ?? ''
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
 * What a background driver reports about itself, for the `status` tool.
 *
 * The loop outlives the `run` tool call that started it, so the only way the
 * main agent can see where it stands is to read this back.
 */
type DriverRecord = {
  run: string
  slug: string
  /** Where the loop is: still driving, or why it stopped. */
  phase: 'running' | 'done' | 'needs_you' | 'error' | 'cancelled'
  step: string | null
  /** The loop's closing words, once it has any. */
  detail: string
  startedAt: number
}

/**
 * The live background drivers, keyed by slug.
 *
 * A `tool.call` hook has a ten-second budget: the engine skips a hook that
 * overruns it and answers the call itself, which is what made driving the loop
 * inside `run` fail. The loop therefore runs UNAWAITED, outside the hook's
 * dispatch, and the promise is parked here so a second `run` on the same slug
 * joins the running loop instead of starting a rival one.
 */
const drivers = new Map<string, { record: DriverRecord; done: Promise<void> }>()

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

/**
 * The progress pane's own state, beside `state` for the same reason: the
 * engine follows `$` only into the hoisted top-level functions, so the
 * refresh and the driver loop reach the pane through module scope.
 */
const pane = {
  /** What the next drawing reads; `pane.ts` owns its shape. */
  model: INITIAL_MODEL as PaneModel,

  /** True between our `$.ui.open` and the close that went through. */
  isOpen: false,

  /** The terminal's width as last drawn; null before any drawing. */
  columns: null as number | null,

  /** Set once per run so a re-entered driver does not re-open a closed pane. */
  hasAutoOpened: false,

  /** When the live run started, for the elapsed clock. */
  startedAt: 0,

  /** The run `refresh` reads; null when no driver is live. */
  run: null as string | null,

  /** The 15s poll, cancelled when the last driver stops. */
  ticker: null as null | { cancel: () => void },

  /** Resolves the gate a Button press answers, when one is parked. */
  answerGate: null as null | ((answer: 'approve' | 'cancel') => void),

  /** Resolves the abandoned-step Retry press, when one is parked. */
  answerRetry: null as null | ((answer: 'retry' | 'cancel' | 'leave') => void),
}

/**
 * Every popup this module raises, so each phase reads one way.
 *
 * `$.ui.toast` is the transient line under the prompt (claude-code.d.ts:1927)
 * and `$.ui.status` this plugin's pinned one (claude-code.d.ts:1938). A pane
 * opened with `holdToasts` would swallow these, so the pane is opened without
 * it.
 *
 * @param $ the engine
 * @param slug the run being reported
 * @param phase what just happened
 * @param detail a few extra words, when the phase has any
 */
function notify(
  $: EngineInterface,
  slug: string,
  phase: DriverPhase | 'start' | 'gate' | 'ask',
  detail = '',
): void {
  const tail = detail === '' ? '' : `: ${detail}`
  const text = `orchestrator: ${slug} ${phase}${tail}`

  $.ui.status(text)
  $.ui.toast(text)
}

/**
 * Re-read the CLI and redraw the pane.
 *
 * Two calls, because `status --json` carries no per-node model or cost:
 * those live in `step_history[].usage`, which `events --json` returns raw
 * (protocol.py `events`). A failure of either leaves the last good model up
 * rather than blanking the pane mid-run.
 *
 * @param $ the engine
 * @param cli the CLI runner
 * @param patch anything the caller knows that the CLI does not (the gate)
 */
async function refreshPane(
  $: EngineInterface,
  cli: Cli,
  patch: Partial<PaneModel> = {},
): Promise<void> {
  const run = pane.run

  if (run !== null) {
    const [status, events] = await Promise.all([
      cli(['orchestrator', 'status', run, '--json']).catch(() => null),
      cli(['orchestrator', 'events', run, '--json']).catch(() => null),
    ])

    const parsed = status === null ? null : safeJson<StatusJson>(status.stdout)

    if (parsed !== null) {
      pane.model = { ...pane.model, status: parsed }
    }

    const rows = events === null ? null : safeJson<unknown[]>(events.stdout)

    if (rows !== null) {
      pane.model = { ...pane.model, usage: usageByStepOf(rows) }
    }
  }

  pane.model = {
    ...pane.model,
    elapsedMs: pane.startedAt === 0 ? 0 : Date.now() - pane.startedAt,
    ...patch,
  }

  // `invalidate` re-runs the cached `ui.render` (claude-code.d.ts:1853).
  $.ui.invalidate('ui.render')
}

/** `JSON.parse` that answers null rather than throwing on CLI noise. */
function safeJson<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T
  } catch {
    return null
  }
}

/**
 * Per-node model and cost, folded out of `events --json` rows.
 *
 * The last attempt of a step wins its model; the costs of every attempt sum,
 * so a step retried twice shows what it really billed. `cost_partial` is what
 * `record.py` stamps when a model has no pricing row.
 */
function usageByStepOf(rows: readonly unknown[]): Map<string, NodeUsage> {
  const out = new Map<string, NodeUsage>()

  for (const row of rows) {
    if (typeof row !== 'object' || row === null) {
      continue
    }

    const entry = row as { step_id?: unknown; usage?: unknown }
    const stepId = typeof entry.step_id === 'string' ? entry.step_id : ''
    const usage = (entry.usage ?? {}) as Record<string, unknown>

    if (stepId === '') {
      continue
    }

    const prior = out.get(stepId)
    const cost = typeof usage.cost_usd === 'number' ? usage.cost_usd : 0

    out.set(stepId, {
      model: typeof usage.model === 'string' ? usage.model : prior?.model ?? '',
      costUsd: (prior?.costUsd ?? 0) + cost,
      isPartial: prior?.isPartial === true || usage.cost_partial === true,
    })
  }

  return out
}

/**
 * Open the pane for a run that just started, when the terminal has the room.
 *
 * `$.ui.open` parks an unasked pane undrawn below 144 columns
 * (claude-code.d.ts:4897), so a narrow terminal is left alone rather than
 * handed an invisible pane; the person can still ask for it with
 * `/orchestrator` once they widen.
 */
/**
 * The CLI runner over an engine: the only kind of process this module starts.
 *
 * `$.process.run` answers `{ exitCode, stdout, stderr }`; the argv rides back
 * with it so `parseJson` can name the call that produced unusable output.
 */
const cliOf =
  ($: EngineInterface): Cli =>
  async (argv, timeoutMs = 60_000) => {
    const ran = await $.process.run(argv, { timeoutMs })

    return { argv, ...ran }
  }

async function openPaneForRun($: EngineInterface, asked: boolean): Promise<void> {
  const floor = asked ? OPEN_MIN_COLUMNS : AUTO_OPEN_MIN_COLUMNS

  if (pane.columns !== null && pane.columns < floor) {
    return
  }

  await $.ui
    .open({ id: PANE_ID, title: PANE_TITLE, closeOnEscape: true })
    .then(() => {
      pane.isOpen = true
    })
    .catch(() => undefined)
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

    // `$.command.register` takes the slash command this plugin serves
    // (CommandSpec, claude-code.d.ts:1359). It can reject when another
    // plugin already holds the name, which must not take the tools down.
    await $.command
      .register({
        name: COMMAND_NAME,
        description: 'Show or hide the orchestrator progress pane.',
        argumentHint: 'status',
      })
      .catch((error: unknown) => {
        $.ui.log(`orchestrator: /${COMMAND_NAME} not registered: ${String(error)}`)
      })

    $.ui.status('orchestrator: idle')

    return next(e)
  })

  // --- the progress pane ---------------------------------------------------

  // The width the pane's own floors are judged against: `PromptHint` draws on
  // every turn and carries the viewport (RenderInputOf, claude-code.d.ts:6237).
  on('ui.render', { component: 'PromptHint' }, ($, e, next) => {
    if (isOnPaneSurface(e)) {
      pane.columns = e.viewport?.columns ?? pane.columns
    }

    return next(e)
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE_ID || !isOnPaneSurface(e)) {
      return next(e)
    }

    // A read, not a dispatch: the table is the surface's, never a global
    // (claude-code.d.ts:1886).
    const { Box, Text, Button } = await $.ui.resolve(e)

    pane.columns = e.viewport?.columns ?? pane.columns

    return paneView({ Box, Text, Button }, pane.model, {
      approve: () => pane.answerGate?.('approve'),
      cancel: () => pane.answerGate?.('cancel'),
      retry: () => pane.answerRetry?.('retry'),
    })
  })

  // A press on one of the gate buttons: core runs the element's `onPress`
  // beneath this hook (claude-code.d.ts:9021), so nothing is answered here —
  // the hook only keeps the pane honest once the press has been taken.
  on('ui.press', { element: [APPROVE_KEY, CANCEL_KEY] }, async ($, e, next) => {
    const result = await next(e)

    await refreshPane($, cliOf($), { gate: null })

    return result
  })

  on('ui.press', { element: [RETRY_KEY] }, async ($, e, next) => {
    const result = await next(e)

    await refreshPane($, cliOf($), { retry: null })

    return result
  })

  on('ui.close', { id: PANE_ID }, async ($, e, next) => {
    const result = await next(e)

    if (result.deny === undefined) {
      pane.isOpen = false
    }

    return result
  })

  on('command.run', { command: COMMAND_NAME }, async ($, e, next) => {
    pane.columns = e.presentation.columns

    const argument = e.args.trim()

    if (argument === 'status') {
      const run = pane.run ?? state.active?.slug ?? state.active?.run ?? ''

      if (run === '') {
        return { text: 'orchestrator: no run in this session.' }
      }

      await refreshPane($, cliOf($))

      const model = pane.model
      const lines = (model.status?.nodes ?? []).map(node =>
        nodeLineOf(node, model.usage.get(node.id)),
      )

      return {
        text: [
          `${model.status?.slug ?? run} · ${model.status?.run_status ?? '-'}`,
          ...lines,
        ].join('\n'),
      }
    }

    if (pane.isOpen) {
      await $.ui.close({ id: PANE_ID }).catch(() => undefined)
      pane.isOpen = false

      return { text: 'orchestrator: pane hidden.' }
    }

    if (pane.columns !== null && pane.columns < OPEN_MIN_COLUMNS) {
      return {
        text:
          `orchestrator: the pane needs ${OPEN_MIN_COLUMNS} columns; this ` +
          `terminal has ${pane.columns}.`,
      }
    }

    await openPaneForRun($, true)
    await refreshPane($, cliOf($))

    // The command is this plugin's own, so there is no core run beneath it to
    // pass to (`next` would find none): the answer is the hook's.
    return pane.isOpen
      ? { text: 'orchestrator: pane shown.' }
      : { text: 'orchestrator: the surface declined to open the pane.' }
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

    const cli = cliOf($)
    const args = argsOf(e)

    if (e.tool === state.served.status) {
      const ref = stringArg(e, 'run') || state.active?.slug || state.active?.run || ''

      if (ref === '') {
        return { result: 'orchestrator: no run in this session; pass `run`.' }
      }

      // The driver's own view first: `orchestrator status` reports what the run
      // has recorded, which says nothing about whether a loop is still driving
      // it or died with an error it never got to record.
      const driver = drivers.get(ref)?.record
      const ran = await cli(['orchestrator', 'status', ref, '--json'])
      const live =
        driver === undefined
          ? 'driver: not running in this session.'
          : `driver: ${driver.phase} at ${driver.step ?? '-'}` +
            (driver.detail === '' ? '' : `\n${driver.detail}`)

      return { result: `${live}\n\n${ran.stdout || ran.stderr}` }
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

    const running = drivers.get(start.slug)

    if (running !== undefined && running.record.phase === 'running') {
      return {
        result:
          `orchestrator: ${start.slug} is already running (run ${start.run_id}), ` +
          `at ${running.record.step ?? '-'}. Call the status tool for progress.`,
      }
    }

    state.active = { run: start.run_id, slug: start.slug }
    state.gateToken = null

    pane.run = start.run_id
    pane.startedAt = Date.now()
    pane.model = { ...INITIAL_MODEL, phase: 'running' }
    notify($, start.slug, 'start', `run ${start.run_id}`)

    if (!pane.hasAutoOpened) {
      pane.hasAutoOpened = true
      await openPaneForRun($, false)
    }

    await refreshPane($, cli)

    // `$.clock.every` keeps calling until its Timer is cancelled
    // (TimerCall, claude-code.d.ts:2553/7886): the pane's own heartbeat, so the elapsed clock
    // and any step the loop has not reported yet still land.
    pane.ticker?.cancel()
    pane.ticker = $.clock.every(REFRESH_EVERY_MS, () => {
      if (pane.isOpen) {
        void refreshPane($, cli).catch(() => undefined)
      }
    })

    const record: DriverRecord = {
      run: start.run_id,
      slug: start.slug,
      phase: 'running',
      step: start.next.step_id ?? null,
      detail: '',
      startedAt: Date.now(),
    }

    // Unawaited on purpose: see `drivers`. The engine keeps the module's
    // environment alive after the hook settles, so the loop goes on running
    // (the subagent it spawns, and the CLI calls after it, are what the debug
    // log shows continuing past the tool's return).
    const done = drive($, cli, start, start.next, record)
      .then(detail => {
        record.detail = detail
        if (record.phase === 'running') {
          record.phase = 'done'
        }
      })
      .catch((error: unknown) => {
        record.phase = 'error'
        record.detail = `orchestrator: ${start.slug} driver failed: ${String(error)}`
      })
      .finally(() => {
        // A finished run leaves its record in `drivers` for `status` to read;
        // only the session-wide switches are cleared.
        if (state.active?.slug === start.slug) {
          state.active = null
        }
        state.gateToken = null
        // The spawn bookkeeping is per-run: every agent this loop waited on has
        // settled by now, so dropping it keeps a long session from growing a
        // map of dead agent ids. Not cleared while another run is live, since
        // the sets are shared and that run still needs its own entries.
        if (state.active === null) {
          state.ours.clear()
          state.waiting.clear()
        }

        pane.model = { ...pane.model, phase: record.phase, gate: null, retry: null }
        pane.answerGate = null
        pane.answerRetry = null

        // The heartbeat belongs to a live driver; a finished one leaves the
        // pane up with its last reading rather than a timer polling forever.
        if (state.active === null) {
          pane.ticker?.cancel()
          pane.ticker = null
        }

        void refreshPane($, cli).catch(() => undefined)
        notify($, start.slug, record.phase)
      })

    drivers.set(start.slug, { record, done })

    return {
      result:
        `orchestrator: run ${start.slug} started (run_id ${start.run_id}).\n` +
        'It is driving in the background: progress shows in the status line, ' +
        'gates will prompt you, and a toast lands when it finishes. Call the ' +
        'status tool for where it stands.',
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
  record: DriverRecord,
): Promise<string> {
  const run = start.run_id
  let result = first
  let lastStderr = ''

  for (;;) {
    const stepId = result.step_id ?? null

    record.step = stepId

    $.ui.status(`orchestrator: ${start.slug} ${stepId ?? '-'} ${result.status}`)

    // Every step the loop takes redraws the pane, so its node list tracks the
    // run rather than waiting on the 15s heartbeat.
    await refreshPane($, cli).catch(() => undefined)

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

    const ask = askOf(result)

    if (ask) {
      notify($, start.slug, 'ask', ask.ask)

      const answered = await runAsk($, cli, run, stepId, ask)

      if (answered === null) {
        record.phase = 'needs_you'
        notify($, start.slug, 'needs_you', `unanswered at ${stepId ?? '-'}`)

        return (
          `orchestrator: ${start.slug} is waiting at ${stepId ?? '-'} ` +
          `(${ask.ask}); there is nobody to ask in this session, so the run ` +
          'is left standing. Answer it from a shell:\n' +
          `  orchestrator resume ${start.slug} "<answer>" --json\n` +
          'then run this recipe on the same slug again to resume.'
        )
      }

      lastStderr = answered.stderr
      result = answered.next

      continue
    }

    const abandoned = abandonedOf(result)

    if (abandoned) {
      notify($, start.slug, 'needs_you', `${abandoned.abandoned_step ?? '-'} abandoned`)

      const answered = await runRetry($, cli, run, abandoned)

      if (answered === null) {
        record.phase = 'needs_you'
        notify($, start.slug, 'needs_you', `left standing at ${stepId ?? '-'}`)

        return (
          `orchestrator: ${start.slug} is parked at ${stepId ?? '-'} ` +
          `(${abandoned.reason}); there is nobody to ask in this session, so ` +
          'the run is left standing. Decide from a shell:\n' +
          `  orchestrator reset-step ${start.slug} ${abandoned.abandoned_step ?? stepId ?? ''} --json   # retry\n` +
          `  orchestrator cancel ${start.slug}                                    # give up`
        )
      }

      if (answered === 'cancelled') {
        record.phase = 'cancelled'

        return `orchestrator: ${start.slug} cancelled at ${stepId ?? '-'} (${abandoned.reason}).`
      }

      lastStderr = answered.stderr
      result = answered.next

      continue
    }

    if (result.status === 'needs_you' || result.status === 'error') {
      record.phase = result.status
      notify($, start.slug, result.status, stepId ?? '-')

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
      notify($, start.slug, 'gate', gate.step_id)

      const answered = await runGate($, cli, run, gate)

      lastStderr = answered.stderr

      if (answered.next === null) {
        if (answered.unattended === true) {
          record.phase = 'needs_you'

          return (
            `orchestrator: ${start.slug} is waiting at gate ${gate.step_id}; ` +
            'there is nobody to ask in this session, so the run is left ' +
            'standing. Approve it from a shell:\n' +
            `  orchestrator status ${start.slug} --json\n` +
            `  orchestrator approve ${start.slug} <token>\n` +
            'then run this recipe on the same slug again to resume.'
          )
        }

        record.phase = 'cancelled'

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
    record.phase = 'error'

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
    model: spawnModelOf(payload),
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
  usage: UsageCounts = usageOf(undefined),
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
  const tokenName = gate.preview.token_name
  const showLines = Object.entries(gate.preview.show).map(([name, entry]) => {
    const producer = entry.produced_by || '?'
    const verdict = entry.last_verdict || 'no verdict'
    return `  - ${name}: ${producer} (${verdict})`
  })

  const question =
    `Approve ${gate.step_id} (${tokenName})?` +
    (showLines.length === 0 ? '' : `\n${showLines.join('\n')}`)

  // `$.ui.ask` rejects both when the person dismisses it and in a `-p` run
  // where there is nobody to ask (claude-code.d.ts:1908). Those are not the
  // same answer: a dismissal is a cancel, but an unattended run must leave
  // the gate standing for `orchestrator approve` from a shell rather than
  // destroy the run. Neither case is consent, so only the literal label is.
  //
  // The pane's Approve/Cancel Buttons answer the SAME gate: `pane.answerGate`
  // settles this promise, so a press and the dialog run one code path and
  // whichever comes first wins. `ui.press` runs the Button's `onPress`
  // beneath the hook chain (claude-code.d.ts:9021).
  // True once a Button press has answered: the dialog's own rejection after
  // that is the surface tearing the dialog down, not an unattended run.
  const answered = { byPress: false }

  const pressed = new Promise<string>(resolve => {
    pane.answerGate = choice => {
      answered.byPress = true
      resolve(choice)
    }
  })

  pane.model = {
    ...pane.model,
    gate: {
      stepId: gate.step_id,
      token: gate.token ?? gate.preview.token_name,
    },
  }

  $.ui.invalidate('ui.render')

  // The loser of the race is never awaited, so a dialog that rejects after a
  // press already won must not surface as an unhandled rejection.
  const asked = $.ui
    .ask(question, { options: ['approve', 'cancel'], header: 'gate' })
    .catch((error: unknown) => {
      if (answered.byPress) {
        return 'cancel'
      }

      throw error
    })

  let answer: string

  try {
    answer = await Promise.race([asked, pressed])
  } catch {
    // A rejected dialog does not settle `pressed`: the gate stays pressable
    // until the run is left standing, which is what an unattended run wants.
    pane.answerGate = null
    pane.model = { ...pane.model, gate: null }

    return { next: null, stderr: '', unattended: true }
  } finally {
    pane.answerGate = null
  }

  pane.model = { ...pane.model, gate: null }
  $.ui.invalidate('ui.render')

  if (answer !== 'approve') {
    const ran = await cli(['orchestrator', 'cancel', run, '--json'])

    return { next: null, stderr: ran.stderr.trim() }
  }

  state.gateToken = gate.token ?? gate.preview.token_name

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
 * Ask whether to retry a step the run parked at `needs_you` because it was
 * recorded `abandoned` (e.g. `$.agent.spawn` was refused) — there is no
 * routing for this dead end, only a human decision.
 *
 * `retry` resets the node (and everything declared after it) back to
 * pending via `orchestrator reset-step` and returns the run's next step;
 * `cancel` aborts the run; `leave`, a dialog rejection, or the pane closing
 * with nobody answering all leave the run standing for a shell command,
 * exactly like an unattended gate (`runGate`).
 */
async function runRetry(
  $: EngineInterface,
  cli: Cli,
  run: string,
  abandoned: AbandonedPayload,
): Promise<{ next: StepResult; stderr: string } | null | 'cancelled'> {
  // `abandoned.reason` already reads "<step_id> abandoned: <detail>" (or
  // "rejected: <detail>") — protocol.py's `step` forwards record.py's
  // `needs_you_reason` verbatim (dispatch.py's EXIT_NEEDS_YOU branch), so
  // prefixing it again here doubled it to "explore abandoned: explore
  // abandoned: …".
  const stepId = abandoned.abandoned_step ?? ''
  const question = `${abandoned.reason.slice(0, 200)}. Retry it?`

  const answered = { byPress: false }

  const pressed = new Promise<'retry' | 'cancel' | 'leave'>(resolve => {
    pane.answerRetry = choice => {
      answered.byPress = true
      resolve(choice)
    }
  })

  pane.model = {
    ...pane.model,
    retry: { stepId, reason: abandoned.reason },
  }

  $.ui.invalidate('ui.render')

  const asked = $.ui
    .ask(question, { options: ['retry', 'cancel', 'leave'], header: 'needs_you' })
    .catch((error: unknown) => {
      if (answered.byPress) {
        return 'cancel'
      }

      throw error
    })

  let answer: string

  try {
    answer = await Promise.race([asked, pressed])
  } catch {
    pane.answerRetry = null
    pane.model = { ...pane.model, retry: null }

    return null
  } finally {
    pane.answerRetry = null
  }

  pane.model = { ...pane.model, retry: null }
  $.ui.invalidate('ui.render')

  if (answer === 'leave') {
    return null
  }

  if (answer === 'cancel') {
    await cli(['orchestrator', 'cancel', run, '--json'])

    return 'cancelled'
  }

  if (stepId === '') {
    // Nothing to reset — report the run standing rather than guess a step.
    return null
  }

  const ran = await cli(['orchestrator', 'reset-step', run, stepId, '--json'])

  return nextOf(cli, run, ran)
}

/**
 * Ask the person the step's question, then advance the run with the answer.
 *
 * `$.ui.ask` takes 2-4 option labels (claude-code.d.ts:1908-1909); a step
 * offering more sends only the first four, with the rest folded into the
 * question text so a person can still type one as free "Other" text — the
 * CLI's `resume` matches by label or 1-based index either way. Returns null
 * when there is nobody to ask (a rejected `-p` call), leaving the run
 * standing exactly like an unattended gate.
 */
async function runAsk(
  $: EngineInterface,
  cli: Cli,
  run: string,
  stepId: string | null,
  ask: AskPayload,
): Promise<{ next: StepResult; stderr: string } | null> {
  const options = ask.options ?? []
  const shown = options.slice(0, 4)
  const overflow = options.slice(4)

  const question =
    overflow.length === 0
      ? ask.ask
      : `${ask.ask} (also available: ${overflow.join(', ')})`

  let answer: string

  try {
    answer =
      shown.length >= 2
        ? await $.ui.ask(question, { options: shown, header: 'orchestrator' })
        : await $.ui.ask(question, { header: 'orchestrator' })
  } catch {
    return null
  }

  const ran = await cli(
    ['orchestrator', 'resume', run, answer, '--json'],
    STEP_TIMEOUT_MS,
  )

  return nextOf(cli, run, ran)
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
