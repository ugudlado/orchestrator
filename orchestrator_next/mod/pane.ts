import type { Elements, RenderElement, RenderSurface } from 'claude-code'

/**
 * The progress pane's model and its drawing.
 *
 * Nothing here runs a process or touches the engine: `register.ts` fetches
 * `orchestrator status --json` and `orchestrator events --json`, folds them
 * into a `PaneModel`, and this module turns that into the element tree a
 * `ui.render` hook on `{ component: 'Pane' }` returns.
 */

/** The pane's id and title — one instance per id (claude-code.d.ts:4903). */
export const PANE_ID = 'orchestrator'
export const PANE_TITLE = 'Orchestrator'

/** The slash command that toggles it. */
export const COMMAND_NAME = 'orchestrator'

/**
 * The narrowest terminal the pane opens itself on.
 *
 * `$.ui.open` already withholds an unasked pane below 144 columns and an
 * asked one below 110 (claude-code.d.ts:4897-4899); mirroring the floor here
 * keeps the mod from opening a pane the surface would only park undrawn.
 */
export const AUTO_OPEN_MIN_COLUMNS = 144

/** The floor for a pane the person asked for with `/orchestrator`. */
export const OPEN_MIN_COLUMNS = 110

/** How often a live driver re-reads the CLI while the pane is open. */
export const REFRESH_EVERY_MS = 15_000

/** One node of the run, as `orchestrator status --json` reports it. */
export type StatusNode = {
  id: string
  phase: string
  kind: string
  status: string
  attempts: number
}

/** What `orchestrator status <run> --json` answers (protocol.py `status`). */
export type StatusJson = {
  run_id: string
  slug: string
  run_status: string
  phase: string
  nodes: readonly StatusNode[]
  usage?: { input_tokens?: number; output_tokens?: number }
  cost_usd?: number
}

/**
 * What a node billed, folded out of `orchestrator events --json`.
 *
 * `status --json` reports no per-node model or cost: both live in
 * `step_history[].usage` (protocol.py `status` projects only id/phase/kind/
 * status/attempts), which `events` returns raw.
 */
export type NodeUsage = {
  model: string
  costUsd: number
  isPartial: boolean
}

/** Where the driver loop stands, as `register.ts` words its phases. */
export type DriverPhase =
  | 'running'
  | 'done'
  | 'needs_you'
  | 'error'
  | 'cancelled'

/** A gate the run is parked at, waiting for an approval. */
export type ParkedGate = {
  stepId: string
  token: string
}

/** An abandoned step the run is parked at, waiting on a retry decision. */
export type ParkedRetry = {
  stepId: string
  reason: string
}

/** Everything one drawing of the pane reads. */
export type PaneModel = {
  /** The last `status --json` read, or null before the first one settled. */
  status: StatusJson | null
  /** Per-node model and cost, by step id. */
  usage: ReadonlyMap<string, NodeUsage>
  /** The driver's own phase, which `status` says nothing about. */
  phase: DriverPhase
  /** Wall time since the run started, in milliseconds. */
  elapsedMs: number
  /** The gate awaiting an answer, when the run is parked at one. */
  gate: ParkedGate | null
  /** The abandoned step awaiting a retry decision, when the run is parked at one. */
  retry: ParkedRetry | null
  /** The question the run is parked on, when it is awaiting an answer. */
  ask: ParkedAsk | null
  /** What to say when there is no run to draw. */
  note: string
}

/** The pane before any run: nothing fetched, nothing to bill. */
export const INITIAL_MODEL: PaneModel = Object.freeze({
  status: null,
  usage: new Map<string, NodeUsage>(),
  phase: 'running' as DriverPhase,
  elapsedMs: 0,
  gate: null,
  retry: null,
  ask: null,
  note: 'No run yet. Press Start run, or type `/orchestrator run`.',
})

/** What the pane draws for each node status. */
const GLYPHS: Record<string, string> = {
  completed: '✓',
  done: '✓',
  running: '▶',
  active: '▶',
  in_progress: '▶',
  pending: '◦',
  skipped: '◦',
  failed: '✗',
  error: '✗',
  abandoned: '✗',
  blocked: '⏸',
  waiting: '⏸',
}

/** The glyph for a node's status; an unknown status stays a plain bullet. */
export const glyphOf = (status: string): string => GLYPHS[status] ?? '◦'

/** `mm:ss` (or `h:mm:ss`) for an elapsed span. */
export function elapsedTextOf(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const seconds = String(total % 60).padStart(2, '0')
  const minutes = total < 3600 ? String(Math.floor(total / 60)) : String(Math.floor(total / 60) % 60).padStart(2, '0')

  return total < 3600
    ? `${minutes}:${seconds}`
    : `${Math.floor(total / 3600)}:${minutes}:${seconds}`
}

/** A run id shortened to its first eight characters, as `status` prints it. */
export const shortRunOf = (run: string): string => run.slice(0, 8)

/** `$0.0123`, or a dash when a node billed nothing. */
const costTextOf = (usage: NodeUsage | undefined): string =>
  usage === undefined || usage.costUsd === 0
    ? '-'
    : `$${usage.costUsd.toFixed(4)}${usage.isPartial ? '?' : ''}`

/** One node's line: glyph, id, kind, attempts, model, cost. */
export function nodeLineOf(
  node: StatusNode,
  usage: NodeUsage | undefined,
): string {
  const attempts = node.attempts > 1 ? ` x${node.attempts}` : ''
  const model = usage?.model ?? ''

  return (
    `${glyphOf(node.status)} ${node.id} · ${node.kind}${attempts}` +
    (model === '' ? '' : ` · ${model}`) +
    ` · ${costTextOf(usage)}`
  )
}

/** The footer: the run's total cost, whether any of it is a guess, the phase. */
export function footerTextOf(model: PaneModel): string {
  const total = model.status?.cost_usd ?? 0
  const isPartial = [...model.usage.values()].some(entry => entry.isPartial)

  return (
    `$${total.toFixed(4)}${isPartial ? ' (partial)' : ''} · ` +
    `${model.status?.phase ?? '-'} · ${model.phase}`
  )
}

/** Whether a render event comes from a surface that can draw this pane. */
export const isOnPaneSurface = <E extends Record<'surface', RenderSurface>>(
  e: E,
): e is Exclude<E, Record<'surface', 'mobile'>> => e.surface !== 'mobile'

/** What a pressed Button asks the driver to do. */
export type PaneActions = {
  approve: () => void
  cancel: () => void
  retry: () => void
  /** Start a run (the wizard), from the idle and finished rows. */
  start: () => void
  /** Answer the parked question with this exact option label. */
  answer: (option: string) => void
  /** Answer the parked question with free text, via a popup. */
  answerOther: () => void
  /** Hide the pane. */
  close: () => void
}

/** The Button keys the pane draws, which `ui.press` names in `e.element`. */
export const APPROVE_KEY = 'orchestrator-approve'
export const CANCEL_KEY = 'orchestrator-cancel'
export const RETRY_KEY = 'orchestrator-retry'
export const START_KEY = 'orchestrator-start'
export const ANSWER_KEY = 'orchestrator-answer'
export const CLOSE_KEY = 'orchestrator-close'

/**
 * The key of the Button for the nth option of a parked question.
 *
 * `ui.press` addresses a Button by its `key` (claude-code.d.ts:643), so the
 * index rides in the key and `register.ts` reads the label back out of the
 * model rather than off the event.
 */
export const optionKeyOf = (index: number): string => `orchestrator-option-${index}`

/** The option index a press names, or null when the key is not an option's. */
export function optionIndexOf(key: string): number | null {
  const match = /^orchestrator-option-(\d+)$/.exec(key)
  const index = match?.[1]

  return index === undefined ? null : Number(index)
}

/**
 * The most options the pane draws as Buttons.
 *
 * Mirrors `$.ui.ask`'s own 2-4 option ceiling (claude-code.d.ts:1908-1909),
 * so the pane and the popup offer the same choices; the rest are reachable
 * through **Answer…**, whose free text `orchestrator resume` matches by
 * label or 1-based index either way.
 */
export const MAX_OPTION_BUTTONS = 4

/** A question the run is parked on, waiting for an answer. */
export type ParkedAsk = {
  question: string
  options: readonly string[]
}

/** One Button in the contextual action row: its key and its label. */
export type PaneAction = {
  key: string
  label: string
}

/**
 * The action row for what the run is doing right now.
 *
 * This is the pane's whole point as a control surface: whatever the run is
 * parked on, the thing to do about it is a Button at the bottom, so nothing
 * has to be typed in chat. The row is derived rather than stored, so it can
 * never disagree with the model the same drawing renders above it.
 *
 * Ordered most-parked-first, because a run can be both (a gate is only ever
 * reached with no question outstanding, but the model is patched
 * independently and the more specific prompt should win):
 *
 * | State                      | Buttons                                   |
 * | -------------------------- | ----------------------------------------- |
 * | awaiting an answer         | one per option (≤4) + Answer…  + Cancel   |
 * | parked at a gate           | Approve, Cancel                           |
 * | parked on an abandoned step| Retry, Cancel                             |
 * | driving                    | Cancel                                    |
 * | finished / no run          | Start run, Close                          |
 */
export function actionRowOf(model: PaneModel): readonly PaneAction[] {
  if (model.ask !== null) {
    return [
      ...model.ask.options
        .slice(0, MAX_OPTION_BUTTONS)
        .map((label, index) => ({ key: optionKeyOf(index), label })),
      { key: ANSWER_KEY, label: 'Answer…' },
      { key: CANCEL_KEY, label: 'Cancel' },
    ]
  }

  if (model.gate !== null) {
    return [
      { key: APPROVE_KEY, label: 'Approve' },
      { key: CANCEL_KEY, label: 'Cancel' },
    ]
  }

  if (model.retry !== null) {
    return [
      { key: RETRY_KEY, label: 'Retry' },
      { key: CANCEL_KEY, label: 'Cancel' },
    ]
  }

  // A finished run (however it finished) offers another one; only a run
  // still being driven offers to stop.
  if (model.status !== null && model.phase === 'running') {
    return [{ key: CANCEL_KEY, label: 'Cancel' }]
  }

  return [
    { key: START_KEY, label: model.status === null ? 'Start run' : 'Start another' },
    { key: CLOSE_KEY, label: 'Close' },
  ]
}

/**
 * The line above the action row saying what is being asked, if anything.
 *
 * A parked question is the one state where the pane must carry text the run
 * produced: the options alone do not say what they answer.
 */
export function promptTextOf(model: PaneModel): string {
  if (model.ask !== null) {
    return model.ask.question
  }

  if (model.gate !== null) {
    return `gate ${model.gate.stepId}: approve to let the run write.`
  }

  if (model.retry !== null) {
    return `${model.retry.stepId} abandoned: ${model.retry.reason}`
  }

  return ''
}

/**
 * The pane's element tree.
 *
 * `Box`, `Text` and `Button` come from the surface's table, which the hook
 * reads with `$.ui.resolve(e)` (claude-code.d.ts:1886); they are not globals.
 * Each constructor takes ONE props object with `children` among the props
 * (ElementConstructor, claude-code.d.ts:2707), not variadic children. A
 * Button's `key` is its address at `ui.press` (claude-code.d.ts:643) and its
 * `onPress` runs in this plugin's environment (claude-code.d.ts:702). `Box`
 * and `Button` take a `key`; `TextProps` (claude-code.d.ts:7832) does not, so
 * the node lines carry none.
 *
 * @param ui the resolved elements
 * @param model what the last refresh read
 * @param actions what the gate buttons run
 * @returns the tree to return from the `ui.render` hook
 */
export function paneView(
  ui: Pick<Elements['terminal'], 'Box' | 'Text' | 'Button'>,
  model: PaneModel,
  actions: PaneActions,
): RenderElement {
  const { Box, Text, Button } = ui
  const { status } = model

  // The row is drawn the same way whether or not a run has been read: an
  // idle pane's Start button is what makes the pane a control surface rather
  // than a readout, so it is built before the early return.
  const row = Box({
    key: 'actions',
    flexDirection: 'row',
    gap: 1,
    children: actionRowOf(model).map(action =>
      Button({
        key: action.key,
        label: action.label,
        onPress: () => pressOf(model, actions, action.key),
      }),
    ),
  })

  const prompt = promptTextOf(model)
  const promptLines = prompt === '' ? [] : [Text({ children: prompt })]

  if (status === null) {
    return Box({
      flexDirection: 'column',
      children: [Text({ dimColor: true, children: model.note }), row],
    })
  }

  const head =
    `${status.slug} · ${shortRunOf(status.run_id)} · ` +
    `${status.run_status} · ${elapsedTextOf(model.elapsedMs)}`

  const nodes = Box({
    key: 'nodes',
    flexDirection: 'column',
    children: status.nodes.map(node =>
      Text({ children: nodeLineOf(node, model.usage.get(node.id)) }),
    ),
  })

  return Box({
    flexDirection: 'column',
    children: [
      Text({ bold: true, children: head }),
      nodes,
      ...promptLines,
      row,
      Text({ dimColor: true, children: footerTextOf(model) }),
    ],
  })
}

/**
 * Route a pressed Button's key to the action it stands for.
 *
 * Kept beside `actionRowOf` so a key added to the row cannot be added
 * without a press to answer it. An option Button carries its index in its
 * key, and the LABEL is read back out of the model here rather than off the
 * event, because `ui.press` reports only the key (claude-code.d.ts:8988).
 */
export function pressOf(
  model: PaneModel,
  actions: PaneActions,
  key: string,
): void {
  const optionIndex = optionIndexOf(key)

  if (optionIndex !== null) {
    const label = model.ask?.options[optionIndex]

    if (label !== undefined) {
      actions.answer(label)
    }

    return
  }

  if (key === APPROVE_KEY) {
    actions.approve()
  } else if (key === CANCEL_KEY) {
    actions.cancel()
  } else if (key === RETRY_KEY) {
    actions.retry()
  } else if (key === START_KEY) {
    actions.start()
  } else if (key === ANSWER_KEY) {
    actions.answerOther()
  } else if (key === CLOSE_KEY) {
    actions.close()
  }
}
