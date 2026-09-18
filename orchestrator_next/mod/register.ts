import type { EngineInterface, On } from 'claude-code'

import {
  ANSWER_KEY,
  APPROVE_KEY,
  AUTO_OPEN_MIN_COLUMNS,
  CANCEL_KEY,
  CLOSE_KEY,
  COMMAND_NAME,
  INITIAL_MODEL,
  MAX_OPTION_BUTTONS,
  OPEN_MIN_COLUMNS,
  PANE_ID,
  PANE_TITLE,
  REFRESH_EVERY_MS,
  RETRY_KEY,
  START_KEY,
  isOnPaneSurface,
  nodeLineOf,
  optionKeyOf,
  paneView,
  type DriverPhase,
  type PaneModel,
  type StatusJson,
} from './pane'
import {
  approve as approveAction,
  cancel as cancelAction,
  currentRun,
  listRecipes,
  listRuns,
  resume as resumeAction,
  retry as retryAction,
  startRun,
  type ActionHost,
  type ActionResult,
  type Cli,
  type Ran,
} from './actions'
import {
  abandonedOf,
  argsOf,
  askOf,
  gateOf,
  isAgentFinished,
  isFinalTurn,
  isoStamp,
  judgmentOf,
  jsonBlockOf,
  MODEL_FAMILY_TO_SPAWN_ALIAS,
  paneOnlyToast,
  parseJson,
  promptOf,
  recipeRefOf,
  shouldRaisePopup,
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

/**
 * What every surface tells the MAIN agent about who drives the run.
 *
 * On run 01a0af3f the main agent read a step report and started running
 * `orchestrator` itself — the sandbox denied its writes to ~/.orchestrator/state
 * and it then "recorded a nominal estimate", i.e. invented a result for a step
 * this plugin was already driving. The loop here owns every `done`; a second
 * writer corrupts the run's state. So the tool descriptions, the `run` tool's
 * own result text and the live-run context block all say so in the same words.
 */
const DRIVER_GUIDANCE =
  'This plugin drives the run itself, in the background. Do NOT run ' +
  '`orchestrator` CLI commands, and never call `orchestrator done` yourself: ' +
  'report what the status tool prints and tell the person to use the pane or ' +
  '`/orchestrator`.'

/** Exec steps batch inside one `step` call, so give the child ten minutes. */
const STEP_TIMEOUT_MS = 600_000

/**
 * How often to ask `$.agent.list()` whether a waited-on subagent is still
 * running.
 *
 * Only the safety net: the answer normally arrives as a `reason: answer`
 * `turn.complete`. This catches the loop that ended without one (interrupted,
 * refused, an API error), which raises a non-final turn and then nothing —
 * so the driver would otherwise wait forever. Ten seconds is far below any
 * step's real duration and costs one in-process call.
 */
const AGENT_WATCH_MS = 10_000

/** Live `$.agent.list()` watchdogs, keyed by the agent id they watch. */
const watchdogs = new Map<string, { cancel: () => void }>()

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

  /**
   * The `reason` of the last non-final `turn.complete` seen per waited-on
   * agent (`aborted` / `refusal` / `error`).
   *
   * Kept so that when `$.agent.list()` finally reports the agent stopped, the
   * step is abandoned with the reason the ENGINE gave rather than a guess.
   */
  lastNonFinal: new Map<string, string>(),

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

  /**
   * The terminal's width as last drawn; null before any drawing.
   *
   * Only for the `$.ui.open` floors (`AUTO_OPEN_MIN_COLUMNS`,
   * `OPEN_MIN_COLUMNS`), which mirror the surface's own terminal-wide
   * thresholds (claude-code.d.ts:4897-4899) — the table's own column tiers
   * use `bodyColumns` below, not this.
   */
  columns: null as number | null,

  /**
   * The docked pane's own body width, in cells inside its frame
   * (`RenderPropsOf['Pane'].bodyColumns`, claude-code.d.ts); null before the
   * `ui.render` hook has drawn once. A real docked pane runs far narrower
   * than the terminal (~50 columns in a live session), so the table's tiers
   * key off this, not `columns`.
   */
  bodyColumns: null as number | null,

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

  /** Resolves the parked question a pane option Button answers. */
  answerAsk: null as null | ((answer: string) => void),
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
 * One call: `status --json` now carries each node's model, verdict, duration,
 * token counts and cost, plus the run's `totals` (protocol.py `node_metrics`),
 * so the pane no longer folds `events --json` itself. A failed read leaves the
 * last good numbers up rather than blanking the pane mid-run.
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
    const status = await cli(['orchestrator', 'status', run, '--json'])
      .catch(() => null)

    const parsed = status === null ? null : safeJson<StatusJson>(status.stdout)

    if (parsed !== null) {
      pane.model = { ...pane.model, status: parsed }
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

/**
 * The `ActionHost` the shared actions run against.
 *
 * `answerGate` / `answerRetry` hand a decision to the driver loop when one is
 * parked awaiting it, and report false when none is — which is what makes a
 * Button press, a `/orchestrator approve`, and the approval dialog three ways
 * of answering ONE gate rather than three racing approvals.
 */
function hostOf($: EngineInterface): ActionHost {
  const cli = cliOf($)

  return {
    cli,
    activeRun: () => state.active?.slug ?? state.active?.run ?? null,
    answerGate: choice => {
      const parked = pane.answerGate

      if (parked === null) {
        return false
      }

      parked(choice)

      return true
    },
    answerRetry: choice => {
      const parked = pane.answerRetry

      if (parked === null) {
        return false
      }

      parked(choice)

      return true
    },
    start: (recipe, slug, inputs) => beginRun($, cli, recipe, slug, inputs),
    refresh: () => refreshPane($, cli).catch(() => undefined),
  }
}

/**
 * Answer a parked question, however it was answered.
 *
 * A pane option Button and the `Answer…` popup both settle the promise
 * `runAsk` is awaiting; with no loop parked the text goes to
 * `orchestrator resume` instead, which is the same verb the loop would have
 * called.
 */
async function answerAsk(
  $: EngineInterface,
  text: string,
): Promise<ActionResult> {
  const parked = pane.answerAsk

  if (parked !== null) {
    parked(text)

    return { ok: true, text: `orchestrator: answered "${text}".` }
  }

  return resumeAction(hostOf($), text)
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
        'each gate. Returns the final status and artifacts. ' +
        DRIVER_GUIDANCE,
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
        'or slug; with none, the run this session started. ' +
        DRIVER_GUIDANCE,
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
    pane.bodyColumns = e.props.bodyColumns ?? pane.bodyColumns

    // Every Button routes through the SAME actions the tool and the command
    // call, so "approve" means one thing however it was asked for. Each is
    // fire-and-forget: `onPress` returns void (claude-code.d.ts:702), and the
    // `ui.press` hook above redraws once the press has been taken.
    return paneView({ Box, Text, Button }, pane.model, {
      approve: () => void report($, 'approve', approveAction(hostOf($))),
      cancel: () => void report($, 'cancel', cancelAction(hostOf($))),
      retry: () => void report($, 'retry', retryAction(hostOf($))),
      start: () => void report($, 'start', startWizard($, '')),
      answer: option => void report($, 'resume', answerAsk($, option)),
      answerOther: () => void report($, 'resume', askFreeText($)),
      close: () => {
        void $.ui.close({ id: PANE_ID }).catch(() => undefined)
        pane.isOpen = false
      },
    }, pane.bodyColumns)
  })

  // Every Button the pane draws. Core runs the element's `onPress` beneath
  // this hook (claude-code.d.ts:9021), which is where the action itself is
  // dispatched; this hook only redraws once the press has been taken, so the
  // row the person just used is replaced by the one for where the run now is.
  on(
    'ui.press',
    {
      element: [
        APPROVE_KEY,
        CANCEL_KEY,
        RETRY_KEY,
        START_KEY,
        ANSWER_KEY,
        CLOSE_KEY,
        ...Array.from({ length: MAX_OPTION_BUTTONS }, (_v, i) => optionKeyOf(i)),
      ],
    },
    async ($, e, next) => {
      const result = await next(e)

      await refreshPane($, cliOf($))

      return result
    },
  )

  on('ui.close', { id: PANE_ID }, async ($, e, next) => {
    const result = await next(e)

    if (result.deny === undefined) {
      pane.isOpen = false

      // A decision parked on the pane alone (the popup was suppressed because
      // the pane was open) would have NO surface left once the pane closes,
      // and the driver would wait on a Button nobody can press. Say where the
      // decision still lives: `/orchestrator` answers all three, and reopening
      // the pane brings the same action row back.
      if (pane.answerGate !== null || pane.answerRetry !== null || pane.answerAsk !== null) {
        $.ui.toast(
          'orchestrator: a decision is still waiting — reopen the pane with ' +
            '`/orchestrator`, or answer it there (approve / retry / resume).',
        )
      }
    }

    return result
  })

  on('command.run', { command: COMMAND_NAME }, async ($, e, next) => {
    pane.columns = e.presentation.columns

    return { text: await runCommand($, e.args.trim()) }
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

  // While a run is live, the MAIN agent carries one line saying it must not
  // touch the CLI. `prompt.context` fires once per conversation and is cached
  // until `$.ui.invalidate('prompt.context')` (claude-code.d.ts:3010-3020), so
  // the driver invalidates it when a run starts and again when one ends — the
  // block is otherwise computed before any run exists and would never appear.
  on('prompt.context', ($, e, next) => {
    if (state.active === null) {
      return next(e)
    }

    return next({
      ...e,
      blocks: [
        ...e.blocks,
        {
          name: 'orchestratorRun',
          text:
            `orchestrator run ${state.active.slug} is live and self-driving. ` +
            DRIVER_GUIDANCE,
        },
      ],
    })
  })

  // A subagent's answer arrives as its own `turn.complete`, keyed by the
  // `agentId` `$.agent.spawn` resolved (claude-code.d.ts:287-291).
  //
  // Only a turn that ENDED IN AN ANSWER is that agent's answer. `reason` is
  // one of `answer | aborted | refusal | error` (TurnCompleteReason,
  // d.ts:8432), and the other three carry a partial or empty `e.answer` that
  // must not be recorded as the step's result — `isFinalTurn` is the gate.
  // A non-final turn is remembered instead, so the driver can say WHY the
  // agent stopped rather than report "no fenced json block".
  on('turn.complete', ($, e, next) => {
    const agentId = e.agentId

    if (agentId === undefined || !state.waiting.has(agentId)) {
      return next(e)
    }

    if (!isFinalTurn(e)) {
      state.lastNonFinal.set(agentId, e.reason)

      return next(e)
    }

    state.waiting.get(agentId)?.({ answer: e.answer, usage: usageOf(e.usage) })

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

    const inputs =
      typeof args.inputs === 'object' && args.inputs !== null
        ? (args.inputs as Record<string, unknown>)
        : undefined

    return {
      result: await beginRun($, cli, recipe, slug, inputs).catch(
        (error: unknown) => `orchestrator start failed: ${String(error)}`,
      ),
    }
  })
}

/**
 * Seed a run and kick its background driver off, returning what to report.
 *
 * The `run` tool, the `/orchestrator run` wizard and the pane's **Start run**
 * button all land here, so a run started any of those three ways is the same
 * run with the same driver, pane and heartbeat.
 */
async function beginRun(
  $: EngineInterface,
  cli: Cli,
  recipe: string,
  slug: string,
  inputs?: Record<string, unknown>,
): Promise<string> {
  const startArgv = ['orchestrator', 'start', recipe, slug, '--json']

  if (inputs !== undefined) {
    startArgv.push('--inputs', JSON.stringify(inputs))
  }

  const started = await cli(startArgv, STEP_TIMEOUT_MS)
  const start = parseJson<StartResult>(started.argv, started)
  const running = drivers.get(start.slug)

  if (running !== undefined && running.record.phase === 'running') {
    return (
      `orchestrator: ${start.slug} is already running (run ${start.run_id}), ` +
      `at ${running.record.step ?? '-'}. Call the status tool for progress.`
    )
  }

  state.active = { run: start.run_id, slug: start.slug }
  state.gateToken = null
  // `prompt.context` is cached per conversation, so the live-run block only
  // appears if the cache is dropped now that there IS a run (d.ts:3010-3020).
  $.ui.invalidate('prompt.context')

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
  // (TimerCall, claude-code.d.ts:2553/7886): the pane's own heartbeat, so the
  // elapsed clock and any step the loop has not reported yet still land.
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
        // Drop the live-run context block again now the run is over.
        $.ui.invalidate('prompt.context')
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

      pane.model = {
        ...pane.model,
        phase: record.phase,
        gate: null,
        retry: null,
        ask: null,
      }
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

  return (
    `orchestrator: run ${start.slug} started (run_id ${start.run_id}).\n` +
    'It is driving in the background: progress shows in the pane, gates will ' +
    'prompt you, and a toast lands when it finishes. Press the pane buttons, ' +
    'use `/orchestrator status`, or call the status tool for where it stands.\n' +
    DRIVER_GUIDANCE
  )
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

        // An approve/mint failure leaves the gate exactly as open as it was —
        // this is not a person choosing to cancel, so it must not be reported
        // as one. The run is left standing at the same gate for a retry.
        if (answered.error !== undefined) {
          record.phase = 'needs_you'

          return (
            `orchestrator: ${start.slug} could not approve gate ${gate.step_id}: ` +
            `${answered.error}\nThe gate is still open. Retry from a shell:\n` +
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
): Promise<{ next: StepResult; stderr: string; error?: string }> {
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

  // When the step began, so `done` can carry a real `started_at`: record.py
  // defaults it to `now` and derives `duration_ms` from it, so omitting it
  // recorded a flat 0 for every judgment step however long it ran.
  const startedAt = isoStamp(Date.now())

  // Only a `reason: answer` turn resolves this (see the `turn.complete` hook).
  // A subagent that was interrupted, refused or died on an API error raises a
  // non-final turn and then never another, so the promise alone would hang:
  // the watchdog asks `$.agent.list()` whether the loop is still running and
  // gives up when it is not, with the engine's own reason.
  const turn = await new Promise<{
    answer: string
    usage: UsageCounts
    stopped?: string
  }>(resolve => {
    state.waiting.set(agentId, resolve)

    // `$.clock.every` is the engine's own ticker (the pane's refresh uses it);
    // a bare `setInterval` is not part of the mod runtime's surface.
    watchdogs.set(
      agentId,
      $.clock.every(AGENT_WATCH_MS, () => {
        void $.agent
          .list()
          .then(agents => {
            if (!state.waiting.has(agentId) || !isAgentFinished(agents, agentId)) {
              return
            }

            resolve({
              answer: '',
              usage: usageOf(undefined),
              stopped: state.lastNonFinal.get(agentId) ?? 'stopped without answering',
            })
          })
          .catch(() => undefined)
      }),
    )
  }).finally(() => {
    state.waiting.delete(agentId)
    state.lastNonFinal.delete(agentId)

    watchdogs.get(agentId)?.cancel()
    watchdogs.delete(agentId)
  })

  // The subagent's loop ended without an answer (interrupted, refused, or an
  // API error). Nothing it wrote is a result, so the step is abandoned with
  // the engine's reason rather than run through the JSON-block path, which
  // would report the far less useful "ended without a fenced ```json block".
  if (turn.stopped !== undefined) {
    return recordAbandoned(
      cli,
      run,
      stepId,
      `the subagent ended without answering (${turn.stopped})`,
      turn.usage,
      startedAt,
    )
  }

  const out = jsonBlockOf(turn.answer)

  if (out !== undefined) {
    return recordDone(cli, run, stepId, out, turn.usage, 'completed', startedAt)
  }

  // No parseable JSON block. That is not the same as "the step failed": a
  // contract whose outs are all optional or artifact-backed is satisfied by
  // what the step wrote to disk, and `learn`'s is exactly that (one optional
  // `proposed_scenarios` artifact, written in the live run while the agent's
  // final message carried no fence). The ENGINE owns that judgment, not this
  // harness, so offer an empty `out` and let `validate_out` decide
  // (protocol.py `done`).
  const attempted = await recordDone(cli, run, stepId, {}, turn.usage, 'completed', startedAt)

  if (attempted.error === undefined) {
    return attempted
  }

  // The engine refused it, so the contract really did want something the step
  // never produced. Record the abandon with the engine's own complaint as the
  // reason — it names the missing out, which "no fenced json block" does not.
  return recordAbandoned(
    cli,
    run,
    stepId,
    `${attempted.error} (the subagent ended without a fenced \`\`\`json block)`,
    turn.usage,
    startedAt,
  )
}

/**
 * Record a judgment step's result and return where the run stands after it.
 *
 * `done --status abandoned` skips the `out` contract check (protocol.py
 * `done`), so a step the harness could not complete records its reason in
 * `out.reason` instead of a contract-shaped payload.
 *
 * `error` is set when the engine REFUSED the call — `done` exits 3 with
 * `{"status": "error", …}` when `out` does not satisfy the contract, and
 * `nextOf` surfaces that rather than reading a rejected call as "the run did
 * not move". `runJudgment` needs it to tell a contract the step satisfied
 * from one it did not.
 */
async function recordDone(
  cli: Cli,
  run: string,
  stepId: string,
  out: Record<string, unknown>,
  usage: UsageCounts,
  status: 'completed' | 'abandoned',
  startedAt?: string,
): Promise<{ next: StepResult; stderr: string; error?: string }> {
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
      ...(startedAt === undefined ? [] : ['--started-at', startedAt]),
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
  startedAt?: string,
) => recordDone(cli, run, stepId, { reason }, usage, 'abandoned', startedAt)

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
): Promise<{
  next: StepResult | null
  stderr: string
  unattended?: boolean
  /** Set when `approve` itself failed — the gate is still open, not cancelled. */
  error?: string
}> {
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
      // The token VALUE, never `preview.token_name` (see the guard below,
      // in the approve path proper) — left empty rather than faked when the
      // gate has not actually minted one, so nothing downstream mistakes
      // this for something `approve` would accept.
      token: gate.token ?? '',
    },
  }

  $.ui.invalidate('ui.render')

  // With the pane open, do NOT raise the popup at all: both surfaces would
  // offer the same gate, the Button would win the race, and the dialog —
  // which cannot be retracted — would sit there stale, inviting a second
  // answer to a gate already decided. The pane's Approve/Cancel row is the
  // single surface then, and a toast says so. (Observed on run 01a0af3f.)
  if (!shouldRaisePopup(pane.isOpen)) {
    $.ui.toast(paneOnlyToast(`${gate.step_id} (${tokenName})`, 'Approve'))

    const choice = await pressed

    pane.answerGate = null
    pane.model = { ...pane.model, gate: null }
    $.ui.invalidate('ui.render')

    return choice === 'approve' ? approveGate($, cli, run, gate) : cancelGate(cli, run)
  }

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
    // The pane keeps its Approve/Cancel row, so say where it is — a dismissed
    // popup used to look like the only way the gate could be answered.
    $.ui.toast(
      'orchestrator: press Approve in the pane, or run `/orchestrator approve`.',
    )
    // The gate stays IN the model, so the pane keeps drawing the Approve /
    // Cancel row the toast just pointed at. `pane.answerGate` is dropped
    // because this loop has stopped awaiting it; the Buttons then route
    // through `approveAction`, which approves the standing gate via the CLI.
    pane.answerGate = null

    return { next: null, stderr: '', unattended: true }
  } finally {
    pane.answerGate = null
  }

  pane.model = { ...pane.model, gate: null }
  $.ui.invalidate('ui.render')

  return answer === 'approve'
    ? approveGate($, cli, run, gate)
    : cancelGate(cli, run)
}

/** `orchestrator cancel`, as both gate surfaces reach it. */
async function cancelGate(
  cli: Cli,
  run: string,
): Promise<{ next: StepResult | null; stderr: string; error?: string }> {
  const ran = await cli(['orchestrator', 'cancel', run, '--json'])

  return { next: null, stderr: ran.stderr.trim() }
}

/**
 * Approve the gate and advance the run — the ONE approve path, whether the
 * decision came from the popup or from the pane's Button.
 *
 * `approve` takes the token VALUE the gate minted (`payload.token`), never
 * `preview.token_name` — that is only the name the token gets bound to for
 * downstream `requires:`. A gate that reached here without a minted token
 * (payload.token missing) cannot be approved at all: sending the name
 * instead is exactly the bug this guards, since gates.approve_token()
 * accepts it silently as "some string that happens not to match any
 * record" and raises `unknown or expired gate token` — which nextOf used
 * to swallow rather than report.
 */
async function approveGate(
  $: EngineInterface,
  cli: Cli,
  run: string,
  gate: GatePayload,
): Promise<{ next: StepResult | null; stderr: string; error?: string }> {
  if (gate.token === undefined) {
    return {
      next: null,
      stderr: '',
      error: `gate ${gate.step_id} has no minted token to approve with`,
    }
  }

  state.gateToken = gate.token

  const ran = await cli([
    'orchestrator',
    'approve',
    run,
    state.gateToken,
    '--json',
  ])

  // The token only unlocks writes for the steps this gate opened; the next
  // gate re-arms the deny hook.
  state.gateToken = null

  const advanced = await nextOf(cli, run, ran)

  if (advanced.error !== undefined) {
    return {
      next: null,
      stderr: advanced.stderr,
      error: `approve failed for gate ${gate.step_id}: ${advanced.error}`,
    }
  }

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

  // Pane open → the pane's Retry row is the only surface (see `runGate`).
  if (!shouldRaisePopup(pane.isOpen)) {
    $.ui.toast(paneOnlyToast(abandoned.reason.slice(0, 120), 'Retry'))

    const choice = await pressed

    pane.answerRetry = null
    pane.model = { ...pane.model, retry: null }
    $.ui.invalidate('ui.render')

    return finishRetry($, cli, run, stepId, choice)
  }

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
    $.ui.toast(
      'orchestrator: press Retry in the pane, or run `/orchestrator retry`.',
    )
    // As in `runGate`: the parked step stays in the model so the Retry row
    // survives the dismissed dialog, and the Button falls through to
    // `retryAction`'s own `reset-step`.
    pane.answerRetry = null

    return null
  } finally {
    pane.answerRetry = null
  }

  pane.model = { ...pane.model, retry: null }
  $.ui.invalidate('ui.render')

  return finishRetry($, cli, run, stepId, answer)
}

/**
 * Act on a retry decision — the ONE path, whether it came from the popup or
 * the pane's Button.
 */
async function finishRetry(
  $: EngineInterface,
  cli: Cli,
  run: string,
  stepId: string,
  answer: string,
): Promise<{ next: StepResult; stderr: string } | null | 'cancelled'> {
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

  // The pane carries the question and its options while the popup is up, so
  // dismissing the popup does not take the question with it: the same choices
  // are still Buttons in the action row, and `pane.answerAsk` settles this
  // very promise when one is pressed.
  const answered = { byPress: false }

  const pressed = new Promise<string>(resolve => {
    pane.answerAsk = choice => {
      answered.byPress = true
      resolve(choice)
    }
  })

  pane.model = { ...pane.model, ask: { question: ask.ask, options } }
  $.ui.invalidate('ui.render')

  // Pane open → the pane already carries the question and its option Buttons,
  // so raising the popup too would leave a stale, unretractable dialog behind
  // whichever surface answered first (see `runGate`).
  if (!shouldRaisePopup(pane.isOpen)) {
    $.ui.toast(paneOnlyToast(ask.ask.slice(0, 120), 'Resume'))

    const choice = await pressed

    pane.answerAsk = null
    pane.model = { ...pane.model, ask: null }
    $.ui.invalidate('ui.render')

    return resumeWith(cli, run, stepId, choice)
  }

  const asked = (
    shown.length >= 2
      ? $.ui.ask(question, { options: shown, header: 'orchestrator' })
      : $.ui.ask(question, { header: 'orchestrator' })
  ).catch((error: unknown) => {
    if (answered.byPress) {
      return ''
    }

    throw error
  })

  let answer: string

  try {
    answer = await Promise.race([asked, pressed])
  } catch {
    // Dismissed, or nobody to ask. The question stays in the pane and the
    // Buttons stay live, so this is not the end of the road — but the loop
    // cannot go on until one of them is pressed, so it says where to press.
    $.ui.toast(
      'orchestrator: answer it in the pane, or run `/orchestrator resume <text>`.',
    )

    // A dismissed dialog is not the end of the road any more: the pane still
    // offers the same options, and `/orchestrator resume` still answers. So
    // the loop keeps waiting on the press rather than tearing the run down —
    // which is also what an unattended (`-p`) session wants, since nothing
    // will ever press and the run is left standing exactly as before.
    answer = await pressed
  } finally {
    pane.answerAsk = null
  }

  pane.model = { ...pane.model, ask: null }
  $.ui.invalidate('ui.render')

  return resumeWith(cli, run, stepId, answer)
}

/**
 * Advance a run parked on `await_input` with the person's answer — the ONE
 * resume path, whether the answer came from the popup or the pane's Button.
 *
 * An empty answer means nobody answered, so the run is left standing.
 */
async function resumeWith(
  cli: Cli,
  run: string,
  stepId: string | null,
  answer: string,
): Promise<{ next: StepResult; stderr: string } | null> {
  if (answer === '') {
    return null
  }

  const ran = await cli(
    ['orchestrator', 'resume', run, answer, '--json'],
    STEP_TIMEOUT_MS,
  )

  return nextOf(cli, run, ran)
}

/**
 * The step a `done` / `approve` / `resume` call left the run at.
 *
 * A verb that advanced the run prints `{..., "next": <step result>}`; a verb
 * that failed prints `{"status": "error", "error": "<message>"}` on the SAME
 * exit-0-looking stdout (protocol.py's `main` always emits parseable JSON,
 * exit 3 on failure) — that failure is reported via `error` rather than
 * treated as silence, since the run never moved and the caller must not
 * mistake that for "nothing to report, ask `step`". Only when the verb
 * printed neither shape (not valid JSON at all) does this fall back to
 * asking `step` for the loop's own reading of the run.
 */
async function nextOf(
  cli: Cli,
  run: string,
  ran: Ran,
): Promise<{ next: StepResult; stderr: string; error?: string }> {
  const stderr = ran.stderr.trim()

  try {
    const parsed = parseJson<{ next?: StepResult; status?: string; error?: string }>(
      ran.argv,
      ran,
    )

    // protocol.py's `main` prints valid JSON even on failure —
    // `{"status": "error", "error": "<message>"}`, exit 3 — so a verb that
    // did not advance the run must not be read as "no news, ask step": that
    // silently discarded a bad-token approve as if the run had simply not
    // moved, when the run in fact never budged.
    if (parsed.status === 'error') {
      return { next: { status: 'error', step_id: null, detail: parsed.error ?? '' },
        stderr, error: parsed.error ?? 'unknown error' }
    }

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

// --- the `/orchestrator` command -------------------------------------------

/**
 * `/orchestrator [verb …]`, the whole grammar in one place.
 *
 * The point of the command is that nothing has to be typed into the chat to
 * drive a run: every verb here is the same function the pane's Buttons and
 * the MCP tools call, so a run can be started, approved, answered and
 * cancelled without ever asking the model to do it.
 *
 * | Command                          | Does                                    |
 * | -------------------------------- | --------------------------------------- |
 * | `/orchestrator`                  | toggles the pane; opens the wizard when there is no run |
 * | `/orchestrator run [recipe] [slug] [ticket…]` | starts a run, asking for what was left out |
 * | `/orchestrator approve`          | approves the gate the run is parked at  |
 * | `/orchestrator cancel`           | cancels the run                         |
 * | `/orchestrator retry`            | resets the abandoned step and carries on|
 * | `/orchestrator resume <text>`    | answers the question the run is parked on|
 * | `/orchestrator status`           | prints the node list as text            |
 * | `/orchestrator pane`             | opens the pane (never toggles it shut)  |
 */
async function runCommand($: EngineInterface, argument: string): Promise<string> {
  const [verb = '', ...rest] = argument.split(/\s+/).filter(word => word !== '')
  const host = hostOf($)

  if (verb === 'run') {
    return (await startWizard($, rest.join(' '))).text
  }

  if (verb === 'approve') {
    return (await approveAction(host, rest[0])).text
  }

  if (verb === 'cancel') {
    return (await cancelAction(host, rest[0])).text
  }

  if (verb === 'retry') {
    return (await retryAction(host, rest[0])).text
  }

  if (verb === 'resume') {
    return (await answerAsk($, rest.join(' '))).text
  }

  if (verb === 'status') {
    return await statusText($)
  }

  if (verb === 'pane') {
    return await showPane($)
  }

  if (verb !== '') {
    return (
      `orchestrator: no verb "${verb}". Try: run, approve, cancel, retry, ` +
      'resume <text>, status, pane.'
    )
  }

  // Bare `/orchestrator`: with a run to look at, the pane is the thing to
  // toggle; with none, there is nothing to show, so offer to start one.
  const live = await currentRun(host)

  if (live === null && pane.model.status === null) {
    return (await startWizard($, '')).text
  }

  if (pane.isOpen) {
    await $.ui.close({ id: PANE_ID }).catch(() => undefined)
    pane.isOpen = false

    return 'orchestrator: pane hidden.'
  }

  return await showPane($)
}

/** Open the pane the person asked for, or say why it stayed shut. */
async function showPane($: EngineInterface): Promise<string> {
  if (pane.columns !== null && pane.columns < OPEN_MIN_COLUMNS) {
    return (
      `orchestrator: the pane needs ${OPEN_MIN_COLUMNS} columns; this ` +
      `terminal has ${pane.columns}.`
    )
  }

  await openPaneForRun($, true)
  await refreshPane($, cliOf($))

  return pane.isOpen
    ? 'orchestrator: pane shown.'
    : 'orchestrator: the surface declined to open the pane.'
}

/** The node list as text — what `/orchestrator status` prints. */
async function statusText($: EngineInterface): Promise<string> {
  const host = hostOf($)
  const run = pane.run ?? (await currentRun(host))

  if (run === null) {
    const rows = await listRuns(host)

    return rows.length === 0
      ? 'orchestrator: no runs. Start one with `/orchestrator run`.'
      : [
          'orchestrator: live runs (none is being driven in this session):',
          ...rows.map(
            row =>
              `  ${row.slug} · ${row.recipe} · ${row.run_status} · ` +
              `${row.current_step ?? '-'}`,
          ),
        ].join('\n')
  }

  await refreshPane($, host.cli)

  const model = pane.model

  return [
    `${model.status?.slug ?? run} · ${model.status?.run_status ?? '-'}`,
    ...(model.status?.nodes ?? []).map(node => nodeLineOf(node)),
  ].join('\n')
}

/**
 * Ask for whatever `/orchestrator run` was not given, then start the run.
 *
 * `argument` is the rest of the command line: `[recipe] [slug] [ticket…]`.
 * Each missing piece is asked for with `$.ui.ask`, which takes 2-4 option
 * labels (claude-code.d.ts:1908-1909) — so the recipe list is offered four
 * at a time with an "Other" escape for the rest, and the slug and ticket are
 * free text. A dismissed popup abandons the wizard without starting anything,
 * since a run seeded on a guessed slug is worse than no run.
 */
async function startWizard(
  $: EngineInterface,
  argument: string,
): Promise<ActionResult> {
  const host = hostOf($)
  const [given = '', slugGiven = '', ...ticketWords] = argument
    .split(/\s+/)
    .filter(word => word !== '')

  let recipe = given

  if (recipe === '') {
    const rows = await listRecipes(host)
    const shown = rows.slice(0, MAX_OPTION_BUTTONS - 1)
    const labels = shown.map(row => recipeRefOf(row, rows))
    const question =
      rows.length === 0
        ? 'Which recipe? (no pack found — type its name)'
        : `Which recipe? (${rows.length} available)`

    try {
      recipe =
        labels.length >= 2
          ? await $.ui.ask(question, {
              options: [...labels, 'Other'],
              header: 'orchestrator',
            })
          : await $.ui.ask(question, { header: 'orchestrator' })
    } catch {
      return { ok: false, text: 'orchestrator: no recipe chosen; nothing started.' }
    }

    // "Other" is the escape hatch for the recipes that did not fit the four
    // option slots: it is a label, not a recipe, so it asks again as free text.
    if (recipe === 'Other') {
      const names = rows.map(row => recipeRefOf(row, rows)).join(', ')

      try {
        recipe = await $.ui.ask(`Which recipe? (${names})`, {
          header: 'orchestrator',
        })
      } catch {
        return { ok: false, text: 'orchestrator: no recipe chosen; nothing started.' }
      }
    }
  }

  let slug = slugGiven

  if (slug === '') {
    try {
      slug = await $.ui.ask('Slug for this run? (a ticket id works)', {
        header: 'orchestrator',
      })
    } catch {
      return { ok: false, text: 'orchestrator: no slug given; nothing started.' }
    }
  }

  // The ticket is the one input every shipped recipe declares; it is optional
  // here because a dismissed popup should start the run rather than abandon
  // it, which is why this ask is not allowed to fail the wizard.
  let ticket = ticketWords.join(' ')

  if (ticket === '') {
    ticket = await $.ui
      .ask(`Ticket text for ${slug}? (empty to use the slug)`, {
        header: 'orchestrator',
      })
      .catch(() => '')
  }

  const inputs = ticket.trim() === '' ? undefined : { ticket: ticket.trim() }

  return await startRun(host, recipe, slug, inputs)
}

/**
 * Ask for a free-text answer to the parked question, then deliver it.
 *
 * This is the pane's **Answer…** Button: the options beside it cover the
 * first four, and anything else (including an option past the fourth) is
 * typed here. `orchestrator resume` matches free text against the step's
 * options by label or 1-based index, so a typed option is as good as a press.
 */
async function askFreeText($: EngineInterface): Promise<ActionResult> {
  const question = pane.model.ask?.question ?? 'Answer for the parked step?'

  try {
    return await answerAsk($, await $.ui.ask(question, { header: 'orchestrator' }))
  } catch {
    return { ok: false, text: 'orchestrator: nothing answered.' }
  }
}

/**
 * Report an action a Button fired, whose result nobody is awaiting.
 *
 * A pressed Button has no return channel (`onPress` is void,
 * claude-code.d.ts:702), so the outcome lands as a toast — including the
 * failures, which would otherwise be silent.
 */
function report(
  $: EngineInterface,
  name: string,
  running: Promise<ActionResult>,
): Promise<void> {
  return running
    .then(result => {
      $.ui.toast(result.text)

      if (!result.ok) {
        $.ui.log(`orchestrator: ${name} failed: ${result.text}`)
      }
    })
    .catch((error: unknown) => {
      $.ui.toast(`orchestrator: ${name} failed: ${String(error)}`)
    })
}
