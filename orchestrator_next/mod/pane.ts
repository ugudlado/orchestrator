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

/**
 * The numeric columns a node bills, as `status --json` now reports them
 * (protocol.py `node_metrics`) and as the Totals row sums them.
 *
 * Every field is optional because an engine older than the enrichment answers
 * without them, and the table must still draw: a missing count reads 0.
 */
export type NodeMetrics = {
  seconds?: number
  input_tokens?: number
  output_tokens?: number
  cache_read_tokens?: number
  cache_write_tokens?: number
  cost_usd?: number
  /** True when some of this row billed tokens the engine could not price. */
  cost_partial?: boolean
}

/** One node of the run, as `orchestrator status --json` reports it. */
export type StatusNode = NodeMetrics & {
  id: string
  phase: string
  kind: string
  status: string
  attempts: number
  /** The model the last attempt ran as; "" before the node has run. */
  model?: string
  /** The last attempt's enum verdict; "" when the step declares none. */
  verdict?: string
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
  /** The Totals row: the numeric columns summed over `nodes`. */
  totals?: NodeMetrics
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

/**
 * A token count in the table's compact form: `840`, `1.2k`, `12.5k`, `1.3M`.
 *
 * Kept to at most six cells so ten numeric columns fit a 150-column pane. A
 * thousands value keeps one decimal up to `999.9k`, because the difference
 * between a 12.5k and a 12.9k cache read is real money at cache-read rates;
 * only past a hundred thousand is the decimal noise, and it is dropped.
 */
export function tokenTextOf(value: number): string {
  const n = Math.max(0, Math.round(value))

  if (n === 0) return '-'
  if (n < 1000) return String(n)
  if (n < 1_000_000) {
    const k = n / 1000
    return k < 100 ? `${k.toFixed(1)}k` : `${Math.round(k)}k`
  }
  const m = n / 1_000_000
  return m < 100 ? `${m.toFixed(1)}M` : `${Math.round(m)}M`
}

/**
 * A duration as `12s`, `3m05s` or `1h04m`.
 *
 * Distinct from `elapsedTextOf`, which draws the run's own clock in the
 * header as `mm:ss`: a table cell has to stay narrow and self-labelling,
 * because it sits under a `Seconds` heading beside other numbers.
 */
export function secondsTextOf(value: number): string {
  const total = Math.max(0, Math.round(value))

  if (total === 0) return '-'
  if (total < 60) return `${total}s`
  if (total < 3600) {
    return `${Math.floor(total / 60)}m${String(total % 60).padStart(2, '0')}s`
  }
  return `${Math.floor(total / 3600)}h${String(Math.floor(total / 60) % 60).padStart(2, '0')}m`
}

/**
 * A cost as `$0.1731`, with `?` when part of it could not be priced.
 *
 * Four decimals because a cheap step really does bill $0.0002, and rounding
 * that to cents would draw a column of `$0.00` that reads as free.
 */
export function costTextOf(value: number, isPartial = false): string {
  return value === 0 && !isPartial
    ? '-'
    : `$${value.toFixed(4)}${isPartial ? '?' : ''}`
}

/** How many cells wide the cost bar is drawn. */
export const COST_BAR_CELLS = 8

/** The eighths a partial cell is drawn with, lightest first. */
const BAR_EIGHTHS = ['', '▏', '▎', '▍', '▌', '▋', '▊', '▉'] as const

/**
 * A cost as a bar `COST_BAR_CELLS` wide, scaled to the largest row's cost.
 *
 * Uses the eighth-block characters so a step costing a twentieth of the
 * priciest still draws something rather than rounding to an empty cell: the
 * bar is there to make the expensive row obvious at a glance, which it cannot
 * do if the cheap rows are invisible AND the expensive one is also clipped.
 * A zero (or an unpriced max) draws blank, never a full bar.
 */
export function costBarOf(value: number, max: number): string {
  if (!(max > 0) || value <= 0) {
    return ' '.repeat(COST_BAR_CELLS)
  }

  const eighths = Math.min(
    COST_BAR_CELLS * 8,
    Math.max(1, Math.round((value / max) * COST_BAR_CELLS * 8)),
  )
  const full = Math.floor(eighths / 8)
  const rest = eighths % 8
  const bar = '█'.repeat(full) + BAR_EIGHTHS[rest]

  return bar.padEnd(COST_BAR_CELLS, ' ').slice(0, COST_BAR_CELLS)
}

/** A number off a node, defaulting to 0 so a pre-enrichment engine still draws. */
const numOf = (value: number | undefined): number => value ?? 0

/**
 * One column of the metrics table.
 *
 * `cellOf` renders a node's cell and `totalOf` the Totals row's, which is not
 * always the same thing: `Skill` totals to the word "Totals", `Model` and
 * `Verdict` total to nothing, and `Cost` totals with the run's partial flag
 * rather than any one row's.
 */
export type TableColumn = {
  key: string
  label: string
  align: 'left' | 'right'
  cellOf: (node: StatusNode) => string
  totalOf: (totals: NodeMetrics) => string
}

/**
 * Every column the table can draw, widest tier first.
 *
 * Mirrors the reference table's column set (agentdos run_detail.html's
 * DataTable: Skill · Model · Attempt · Verdict · Seconds · In tok · Out tok ·
 * Cache read · Cache write · Cost), plus a leading status glyph and a trailing
 * cost bar, which a terminal can draw and a web table gets from CSS.
 */
export const ALL_COLUMNS: readonly TableColumn[] = [
  {
    key: 'step',
    label: 'Step',
    align: 'left',
    cellOf: node => `${glyphOf(node.status)} ${node.id}`,
    totalOf: () => 'Totals',
  },
  {
    key: 'model',
    label: 'Model',
    align: 'left',
    cellOf: node => shortModelOf(node.model ?? ''),
    totalOf: () => '',
  },
  {
    key: 'attempts',
    label: 'Att',
    align: 'right',
    cellOf: node => (node.attempts > 0 ? String(node.attempts) : '-'),
    totalOf: () => '',
  },
  {
    key: 'verdict',
    label: 'Verdict',
    align: 'left',
    cellOf: node => node.verdict ?? '',
    totalOf: () => '',
  },
  {
    key: 'seconds',
    label: 'Time',
    align: 'right',
    cellOf: node => secondsTextOf(numOf(node.seconds)),
    totalOf: totals => secondsTextOf(numOf(totals.seconds)),
  },
  {
    key: 'input_tokens',
    label: 'In',
    align: 'right',
    cellOf: node => tokenTextOf(numOf(node.input_tokens)),
    totalOf: totals => tokenTextOf(numOf(totals.input_tokens)),
  },
  {
    key: 'output_tokens',
    label: 'Out',
    align: 'right',
    cellOf: node => tokenTextOf(numOf(node.output_tokens)),
    totalOf: totals => tokenTextOf(numOf(totals.output_tokens)),
  },
  {
    key: 'cache_read_tokens',
    label: 'C-rd',
    align: 'right',
    cellOf: node => tokenTextOf(numOf(node.cache_read_tokens)),
    totalOf: totals => tokenTextOf(numOf(totals.cache_read_tokens)),
  },
  {
    key: 'cache_write_tokens',
    label: 'C-wr',
    align: 'right',
    cellOf: node => tokenTextOf(numOf(node.cache_write_tokens)),
    totalOf: totals => tokenTextOf(numOf(totals.cache_write_tokens)),
  },
  {
    key: 'cost_usd',
    label: 'Cost',
    align: 'right',
    cellOf: node => costTextOf(numOf(node.cost_usd), node.cost_partial === true),
    totalOf: totals => costTextOf(numOf(totals.cost_usd), totals.cost_partial === true),
  },
]

/**
 * A model id shortened to what distinguishes it in a narrow column.
 *
 * `claude-sonnet-5` is drawn `sonnet-5`: the vendor prefix is the same on
 * every row of every run, so it costs eight cells and says nothing.
 */
export const shortModelOf = (model: string): string =>
  model.replace(/^claude-/, '')

/** The width tier a terminal falls in, which decides the column set. */
export const WIDE_MIN_COLUMNS = 150
export const MEDIUM_MIN_COLUMNS = 110

/** The columns dropped first, when the pane is too narrow for all of them. */
const MEDIUM_DROPS = new Set(['cache_read_tokens', 'cache_write_tokens', 'seconds'])

/**
 * The columns to draw at a given terminal width.
 *
 * Three tiers, because a table that overflows its pane is worse than a list:
 * the surface wraps or truncates, and either way the numbers stop lining up.
 * Below `MEDIUM_MIN_COLUMNS` this answers an empty set and `paneView` falls
 * back to the one-line-per-node list the pane drew before the table.
 *
 * @param columns the terminal's width, or null before the first drawing
 */
export function columnsFor(columns: number | null): readonly TableColumn[] {
  const width = columns ?? WIDE_MIN_COLUMNS

  if (width >= WIDE_MIN_COLUMNS) {
    return ALL_COLUMNS
  }
  if (width >= MEDIUM_MIN_COLUMNS) {
    return ALL_COLUMNS.filter(column => !MEDIUM_DROPS.has(column.key))
  }
  return []
}

/** Whether the cost bar has room; it rides with the widest tier only. */
export const showsCostBar = (columns: number | null): boolean =>
  (columns ?? WIDE_MIN_COLUMNS) >= WIDE_MIN_COLUMNS

/** One drawn row: its cells, and what the row is (for its styling). */
export type TableRow = {
  key: string
  cells: readonly string[]
  /** The bar cell, when the tier draws one. */
  bar: string
  kind: 'header' | 'node' | 'totals'
  /** The node's status, for a node row; "" otherwise. */
  status: string
}

/** The gap between columns, in cells. */
const GAP = '  '

/**
 * The table's rows — header, one per node, Totals — with every cell padded to
 * its column's width.
 *
 * Widths are computed from the content (label included) so no column is wider
 * than it needs, and `align` decides which side the padding goes on. The
 * whole table is built as plain strings here, so a test can assert the drawn
 * text without a terminal, and `paneView` only wraps each row in a `Text`.
 *
 * @param nodes the run's nodes, in plan order
 * @param totals the Totals row's numbers
 * @param columns the terminal width, which picks the column set
 */
export function tableRowsOf(
  nodes: readonly StatusNode[],
  totals: NodeMetrics,
  columns: number | null,
): readonly TableRow[] {
  const picked = columnsFor(columns)

  if (picked.length === 0) {
    return []
  }

  const withBar = showsCostBar(columns)
  const maxCost = nodes.reduce((max, node) => Math.max(max, numOf(node.cost_usd)), 0)

  const body: readonly { key: string; cells: string[]; bar: string; kind: TableRow['kind']; status: string }[] = [
    {
      key: 'header',
      cells: picked.map(column => column.label),
      bar: '',
      kind: 'header' as const,
      status: '',
    },
    ...nodes.map(node => ({
      key: node.id,
      cells: picked.map(column => column.cellOf(node)),
      bar: withBar ? costBarOf(numOf(node.cost_usd), maxCost) : '',
      kind: 'node' as const,
      status: node.status,
    })),
    {
      key: 'totals',
      cells: picked.map(column => column.totalOf(totals)),
      bar: '',
      kind: 'totals' as const,
      status: '',
    },
  ]

  const widths = picked.map((_, index) =>
    body.reduce((max, row) => Math.max(max, (row.cells[index] ?? '').length), 0),
  )

  return body.map(row => ({
    key: row.key,
    kind: row.kind,
    status: row.status,
    bar: row.bar,
    cells: row.cells.map((cell, index) => {
      const width = widths[index] ?? 0

      return picked[index]?.align === 'right'
        ? cell.padStart(width, ' ')
        : cell.padEnd(width, ' ')
    }),
  }))
}

/** One row's drawn line: its padded cells joined, plus the bar when drawn. */
export const rowTextOf = (row: TableRow): string =>
  (row.cells.join(GAP) + (row.bar === '' ? '' : `${GAP}${row.bar}`)).trimEnd()

/**
 * One node's line in the compact tier: glyph, id, kind, attempts, model, cost.
 *
 * What the pane drew everywhere before the table, and still draws below
 * `MEDIUM_MIN_COLUMNS`, where no table's columns would line up.
 */
export function nodeLineOf(node: StatusNode): string {
  const attempts = node.attempts > 1 ? ` x${node.attempts}` : ''
  const model = shortModelOf(node.model ?? '')

  return (
    `${glyphOf(node.status)} ${node.id} · ${node.kind}${attempts}` +
    (model === '' ? '' : ` · ${model}`) +
    ` · ${costTextOf(node.cost_usd ?? 0, node.cost_partial === true)}`
  )
}

/**
 * The footer: the run's total cost, whether any of it is a guess, the phase.
 *
 * Reads the run total off `status.totals` when the engine reported one, so
 * the footer and the table's Totals row can never disagree; `cost_usd` is the
 * fallback for an engine that predates the enrichment.
 */
export function footerTextOf(model: PaneModel): string {
  const totals = model.status?.totals
  const total = totals?.cost_usd ?? model.status?.cost_usd ?? 0
  const isPartial =
    totals?.cost_partial === true ||
    (model.status?.nodes ?? []).some(node => node.cost_partial === true)

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
 * The line for a run parked on an abandoned step.
 *
 * The engine's own reason usually already names the step: `record.py` writes
 * `needs_you_reason` as "<step_id> abandoned: <detail>" (or "<step_id>
 * rejected: …" for a `fail_on:` verdict), and `dispatch.py` falls back to a
 * bare "<step_id> abandoned". Prefixing that again here drew it twice — a
 * live pane showed "learn abandoned: learn abandoned: …".
 *
 * So the reason is printed as-is whenever it already opens with the step id,
 * and prefixed only when it does not, which keeps a bare detail (a reason
 * from somewhere that never named the step) saying which step it is about.
 */
export function retryTextOf(retry: ParkedRetry): string {
  const reason = retry.reason.trim()

  if (reason === '') {
    return `${retry.stepId} abandoned`
  }

  return reason.startsWith(`${retry.stepId} `) || reason === retry.stepId
    ? reason
    : `${retry.stepId} abandoned: ${reason}`
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
    return retryTextOf(model.retry)
  }

  return ''
}

/**
 * The node body: the metrics table, or the compact list when the pane is too
 * narrow for one.
 *
 * Styling is limited to what `TextProps` offers (claude-code.d.ts:7841-7845:
 * `color`, `dimColor`, `bold`; no `key`, which is why no row carries one):
 * the header and the Totals row are bold, a running node is highlighted with
 * `color`, and a node nothing has touched yet is dimmed so the eye lands on
 * the rows that have actually billed something.
 *
 * @param Text the surface's Text constructor
 * @param status the last `status --json` read
 * @param columns the terminal's width, which picks the tier
 */
export function nodeChildrenOf(
  Text: Elements['terminal']['Text'],
  status: StatusJson,
  columns: number | null,
): RenderElement[] {
  const rows = tableRowsOf(status.nodes, status.totals ?? {}, columns)

  if (rows.length === 0) {
    return status.nodes.map(node =>
      Text({ dimColor: isPendingStatus(node.status), children: nodeLineOf(node) }),
    )
  }

  return rows.map(row =>
    Text({
      bold: row.kind !== 'node',
      dimColor: row.kind === 'node' && isPendingStatus(row.status),
      ...(row.kind === 'node' && isRunningStatus(row.status)
        ? { color: 'cyan' }
        : {}),
      children: rowTextOf(row),
    }),
  )
}

/** Whether a node status means "not started" — the rows the table dims. */
export const isPendingStatus = (status: string): boolean =>
  status === 'pending' || status === '' || status === 'skipped'

/** Whether a node status means "running now" — the row the table highlights. */
export const isRunningStatus = (status: string): boolean =>
  status === 'running' || status === 'active' || status === 'in_progress'

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
 * @param columns the terminal's width, which picks the table's column set;
 *   null (before any drawing reported one) draws the widest tier
 * @returns the tree to return from the `ui.render` hook
 */
export function paneView(
  ui: Pick<Elements['terminal'], 'Box' | 'Text' | 'Button'>,
  model: PaneModel,
  actions: PaneActions,
  columns: number | null = null,
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
    children: nodeChildrenOf(Text, status, columns),
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
