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
  note: 'No run in this session. Ask for one, or run `/orchestrator status`.',
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
}

/** The Button keys the pane draws, which `ui.press` names in `e.element`. */
export const APPROVE_KEY = 'orchestrator-approve'
export const CANCEL_KEY = 'orchestrator-cancel'
export const RETRY_KEY = 'orchestrator-retry'

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

  if (status === null) {
    return Box({
      flexDirection: 'column',
      children: Text({ dimColor: true, children: model.note }),
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

  const gate =
    model.gate === null
      ? []
      : [
          Box({
            key: 'gate',
            flexDirection: 'row',
            gap: 1,
            children: [
              Text({ children: `gate ${model.gate.stepId}:` }),
              Button({ key: APPROVE_KEY, label: 'Approve', onPress: actions.approve }),
              Button({ key: CANCEL_KEY, label: 'Cancel', onPress: actions.cancel }),
            ],
          }),
        ]

  const retry =
    model.retry === null
      ? []
      : [
          Box({
            key: 'retry',
            flexDirection: 'row',
            gap: 1,
            children: [
              Text({ children: `${model.retry.stepId} abandoned: ${model.retry.reason}` }),
              Button({ key: RETRY_KEY, label: 'Retry', onPress: actions.retry }),
            ],
          }),
        ]

  return Box({
    flexDirection: 'column',
    children: [
      Text({ bold: true, children: head }),
      nodes,
      ...gate,
      ...retry,
      Text({ dimColor: true, children: footerTextOf(model) }),
    ],
  })
}
