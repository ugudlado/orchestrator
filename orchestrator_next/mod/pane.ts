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
  /** The step log panel: which step is selected, and what was read for it. */
  logs: LogModel
  /** The run's working directory, for drawing artifact paths relative. */
  cwd: string
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
  logs: { selected: null, log: null, offset: 0 },
  cwd: '',
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

/**
 * The width tiers a *docked pane's own body* falls in, which decide the
 * column set.
 *
 * A real docked pane runs about 48-56 cells wide (a live session measured
 * ~50), never the 110-150 a full terminal width once suggested — the table
 * used to key off the terminal's columns (`PromptHint`'s viewport) and so
 * always fell to the compact list in a real session. These tiers are sized
 * for the pane's `bodyColumns` (claude-code.d.ts Pane props) instead.
 */
export const NARROW_MIN_COLUMNS = 40
export const COMPACT_MIN_COLUMNS = 64
export const ROOMY_MIN_COLUMNS = 90
export const WIDE_MIN_COLUMNS = 120

/** The pane body width assumed before any drawing has reported one. */
export const DEFAULT_BODY_COLUMNS = 48

/**
 * The columns to draw at a given pane body width, widest tier first so a
 * later tier's additions read as "everything the previous tier had, plus".
 *
 * | Tier                    | Columns                                     |
 * | ------------------------ | -------------------------------------------- |
 * | `< NARROW_MIN_COLUMNS`   | none — `paneView` falls back to the list     |
 * | `>= NARROW_MIN_COLUMNS`  | Step, Att, Time, Cost                        |
 * | `>= COMPACT_MIN_COLUMNS` | + Model, Out                                 |
 * | `>= ROOMY_MIN_COLUMNS`   | + In, Verdict                                |
 * | `>= WIDE_MIN_COLUMNS`    | + C-rd, C-wr, and the cost bar               |
 *
 * Each tier is additive over the previous one (never drops a column the
 * narrower tier already drew), which is what lets `shrinkStepColumn` assume
 * the Step column is always index 0.
 *
 * @param columns the pane body's width, or null before the first drawing
 */
export function columnsFor(columns: number | null): readonly TableColumn[] {
  const width = columns ?? DEFAULT_BODY_COLUMNS
  const byKey = new Map(ALL_COLUMNS.map(column => [column.key, column]))
  const pick = (keys: readonly string[]): TableColumn[] =>
    keys.map(key => byKey.get(key)).filter((c): c is TableColumn => c !== undefined)

  if (width >= WIDE_MIN_COLUMNS) {
    return pick([
      'step', 'model', 'attempts', 'verdict', 'seconds', 'input_tokens',
      'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'cost_usd',
    ])
  }
  if (width >= ROOMY_MIN_COLUMNS) {
    return pick([
      'step', 'model', 'attempts', 'verdict', 'seconds', 'input_tokens',
      'output_tokens', 'cost_usd',
    ])
  }
  if (width >= COMPACT_MIN_COLUMNS) {
    return pick(['step', 'model', 'attempts', 'seconds', 'output_tokens', 'cost_usd'])
  }
  if (width >= NARROW_MIN_COLUMNS) {
    return pick(['step', 'attempts', 'seconds', 'cost_usd'])
  }
  return []
}

/** Whether the cost bar has room; it rides with the widest tier only. */
export const showsCostBar = (columns: number | null): boolean =>
  (columns ?? DEFAULT_BODY_COLUMNS) >= WIDE_MIN_COLUMNS

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
 * @param columns the pane body's width, which picks the column set
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

  const widths = shrinkStepColumn(
    picked.map((_, index) =>
      body.reduce((max, row) => Math.max(max, (row.cells[index] ?? '').length), 0),
    ),
    withBar,
    columns,
  )

  return body.map(row => ({
    key: row.key,
    kind: row.kind,
    status: row.status,
    bar: row.bar,
    cells: row.cells.map((cell, index) => {
      const width = widths[index] ?? 0
      const clipped = cell.length > width ? ellipsize(cell, width) : cell

      return picked[index]?.align === 'right'
        ? clipped.padStart(width, ' ')
        : clipped.padEnd(width, ' ')
    }),
  }))
}

/**
 * Shrinks the Step column (always index 0, when the table has one) so the
 * whole row — every column's width plus the inter-column gaps and the bar —
 * never exceeds the pane's own width.
 *
 * A table wider than its pane wraps mid-row on a real terminal, which breaks
 * the alignment truncation is meant to protect; the Step cell (a step id,
 * usually the longest single field and the one already carrying a glyph and
 * `…`-safe text) absorbs the cut rather than every column shrinking a little.
 * Never shrinks below 1 cell — a table this squeezed already has no business
 * being drawn (see `columnsFor`'s `NARROW_MIN_COLUMNS` floor).
 *
 * @param widths each column's natural (content) width, widest-content-first
 * @param withBar whether a cost bar rides along (adds `GAP.length + COST_BAR_CELLS`)
 * @param paneColumns the pane body's width, or null to skip shrinking
 */
export function shrinkStepColumn(
  widths: readonly number[],
  withBar: boolean,
  paneColumns: number | null,
): readonly number[] {
  if (paneColumns === null || widths.length === 0) {
    return widths
  }

  const gaps = Math.max(0, widths.length - 1) * GAP.length
  const barWidth = withBar ? GAP.length + COST_BAR_CELLS : 0
  const total = widths.reduce((sum, width) => sum + width, 0) + gaps + barWidth
  const overflow = total - paneColumns

  if (overflow <= 0) {
    return widths
  }

  const stepWidth = widths[0] ?? 0
  const shrunk = Math.max(1, stepWidth - overflow)

  return [shrunk, ...widths.slice(1)]
}

/** `cell` cut to `width` cells, the last one an ellipsis when anything was cut. */
export function ellipsize(cell: string, width: number): string {
  if (width <= 0) {
    return ''
  }
  if (cell.length <= width) {
    return cell
  }
  if (width === 1) {
    return '…'
  }

  return `${cell.slice(0, width - 1)}…`
}

/** One row's drawn line: its padded cells joined, plus the bar when drawn. */
export const rowTextOf = (row: TableRow): string =>
  (row.cells.join(GAP) + (row.bar === '' ? '' : `${GAP}${row.bar}`)).trimEnd()

/**
 * One node's line in the compact tier: glyph, id, kind, attempts, model, cost.
 *
 * What the pane drew everywhere before the table, and still draws below
 * `NARROW_MIN_COLUMNS`, where no table's columns would line up.
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

// --- the step log panel ----------------------------------------------------

/**
 * One `orchestrator events <run> --step <id> --json` line: one attempt of one
 * step, as `record.py` `_build_history_entry` writes it into `step_history`.
 *
 * Every field is optional because an inline script step records almost none of
 * them (a real entry: `{step_id, phase, status, attempt, started_at, ended_at,
 * usage: {}, evidence: {outputs: {...}}}`), and the panel must still draw.
 */
export type AttemptEvent = {
  step_id?: string
  phase?: string
  status?: string
  agent?: string | null
  attempt?: number
  started_at?: string
  ended_at?: string
  usage?: {
    model?: string
    duration_ms?: number
    cost_usd?: number
    cost_partial?: boolean
  }
  evidence?: {
    outputs?: Record<string, unknown>
    summary?: string
    detail?: unknown
  }
  /** Artifact paths the step wrote, as `record.py` lists them. */
  artifacts?: readonly string[]
  outputs?: Record<string, unknown>
}

/**
 * What the pane knows about the step a subagent is running RIGHT NOW.
 *
 * Fed from the `tool.call` hook (`e.agentId` on AgentLoop, claude-code.d.ts
 * 7916), which is the only event that reliably names the tool a spawned agent
 * is using: `turn.step`'s chunks stream the model's own response, so a tool
 * call only becomes visible there once the model has finished emitting it,
 * and a long `Bash` shows nothing until it returns. Never persisted — this is
 * live progress, and a reloaded pane simply has none until the next call.
 */
export type LiveTool = {
  /** The tool's name, e.g. `Bash`, `Read`. */
  tool: string
  /** The first 60 characters of its main argument. */
  argument: string
  /** When the call was seen, for the elapsed clock. */
  atMs: number
}

/** How many live tool calls the panel remembers per running step. */
export const MAX_LIVE_TOOLS = 3

/** The characters of a tool argument the panel keeps. */
export const LIVE_ARG_CHARS = 60

/** How many lines of a judgment step's final answer the panel shows. */
export const ANSWER_TAIL_LINES = 6

/** The most lines the whole log panel draws, before scrolling. */
export const LOG_PANEL_ROWS = 12

/** Everything the log panel knows about one step. */
export type StepLog = {
  /** The step's attempts, newest first. */
  attempts: readonly AttemptEvent[]
  /** The last answer a judgment subagent gave for this step, if any. */
  answer?: string
  /** The last few tool calls a running subagent made, newest last. */
  live: readonly LiveTool[]
}

/** The log panel's own state, which `register.ts` folds into the model. */
export type LogModel = {
  /** The step whose log is shown, or null to follow the running node. */
  selected: string | null
  /** What has been read for the selected step; null before the first read. */
  log: StepLog | null
  /** The panel's own scroll offset, in lines. */
  offset: number
}

/** A log panel with nothing selected and nothing read. */
export const INITIAL_LOG: LogModel = Object.freeze({
  selected: null,
  log: null,
  offset: 0,
})

/**
 * The step whose log the panel shows: the one the person selected, else the
 * running node, else the last node that got as far as running.
 *
 * "Follows the running step until the person selects one" is the whole rule:
 * `selected` is null until a row is pressed, and the running node moves on its
 * own as the run advances, so an untouched pane always shows live work.
 */
export function selectedStepOf(
  nodes: readonly StatusNode[],
  selected: string | null,
): string | null {
  if (selected !== null && nodes.some(node => node.id === selected)) {
    return selected
  }

  const running = nodes.find(node => isRunningStatus(node.status))

  if (running !== undefined) {
    return running.id
  }

  // No node is running: the last one that has attempted anything is the one
  // whose result the person is most likely looking for.
  const touched = nodes.filter(node => !isPendingStatus(node.status))

  return touched[touched.length - 1]?.id ?? nodes[0]?.id ?? null
}

/** A duration in milliseconds as the attempt line's `3m05s`. */
const durationTextOf = (ms: number): string => secondsTextOf(ms / 1000)

/**
 * The reason or verdict an attempt ended with — the tail of its line.
 *
 * Prefers the enum verdict the step declared (an out named `verdict` or
 * `decision`, the same keys `protocol.py` `_entry_verdict` reads), then the
 * summary an inline script wrote, then the plain detail. An attempt that
 * reported none answers "" and the line simply ends after the cost.
 */
export function attemptReasonOf(event: AttemptEvent): string {
  const outputs = event.evidence?.outputs ?? event.outputs ?? {}

  for (const key of ['verdict', 'decision']) {
    const value = outputs[key]

    if (typeof value === 'string' && value !== '') {
      return value
    }
  }

  const summary = event.evidence?.summary

  if (typeof summary === 'string' && summary !== '') {
    return summary
  }

  const detail = event.evidence?.detail

  return typeof detail === 'string' ? detail : ''
}

/**
 * One attempt's line: `#2 completed · sonnet-5 · 3m05s · $0.1731 · approved`.
 *
 * Every segment after the status is dropped when the attempt did not record
 * it, so an inline script (no model, no usage) draws `#1 completed · ok`
 * rather than a line of padding and `-` cells. Duration comes from
 * `usage.duration_ms`, which `record.py` derives from the two timestamps when
 * the driver omits it, and falls back to those timestamps here for an entry
 * written before that derivation existed.
 */
export function attemptLineOf(event: AttemptEvent): string {
  const parts = [`#${event.attempt ?? 1} ${event.status ?? 'unknown'}`]
  const model = shortModelOf(event.usage?.model ?? '')

  if (model !== '') {
    parts.push(model)
  }

  const ms = event.usage?.duration_ms ?? spanMsOf(event)

  if (ms > 0) {
    parts.push(durationTextOf(ms))
  }

  const cost = event.usage?.cost_usd ?? 0

  if (cost > 0 || event.usage?.cost_partial === true) {
    parts.push(costTextOf(cost, event.usage?.cost_partial === true))
  }

  const reason = attemptReasonOf(event)

  if (reason !== '') {
    parts.push(reason)
  }

  return parts.join(' · ')
}

/** An attempt's wall time from its two stamps, or 0 when either is unusable. */
function spanMsOf(event: AttemptEvent): number {
  const started = Date.parse(event.started_at ?? '')
  const ended = Date.parse(event.ended_at ?? '')

  return Number.isFinite(started) && Number.isFinite(ended) && ended > started
    ? ended - started
    : 0
}

/**
 * The non-artifact outputs an attempt recorded, one `key: value` line each.
 *
 * An artifact's out is its path, and paths already get their own `→` lines, so
 * a value that names one of `artifacts` is skipped rather than printed twice.
 * A nested value (a `state_patch`) is drawn as compact JSON: it is evidence,
 * not prose, and one line of it says more than "[object Object]".
 */
export function outputLinesOf(event: AttemptEvent): readonly string[] {
  const outputs = event.evidence?.outputs ?? event.outputs ?? {}
  const artifacts = new Set(event.artifacts ?? [])

  return Object.entries(outputs)
    .filter(([, value]) => !(typeof value === 'string' && artifacts.has(value)))
    .map(([key, value]) => `${key}: ${scalarTextOf(value)}`)
}

/** One output value on one line: a string as-is, anything else as JSON. */
function scalarTextOf(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }

  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

/**
 * The artifact lines for an attempt: `→ design.md`, one per path.
 *
 * Paths are drawn relative to the run's cwd when they are absolute and share
 * its prefix — the pane is narrow and a repo path's leading 40 cells are the
 * same on every line, saying nothing.
 */
export function artifactLinesOf(
  event: AttemptEvent,
  cwd = '',
): readonly string[] {
  return (event.artifacts ?? []).map(path => `→ ${relativeTo(path, cwd)}`)
}

/** `path` with `cwd`'s prefix removed, when it has one. */
export function relativeTo(path: string, cwd: string): string {
  if (cwd === '' || !path.startsWith(cwd)) {
    return path
  }

  return path.slice(cwd.length).replace(/^\/+/, '') || path
}

/**
 * The last `ANSWER_TAIL_LINES` non-blank lines of a judgment subagent's answer.
 *
 * The tail, not the head: an agent's final message opens with what it set out
 * to do and closes with what it concluded, and the conclusion is the part a
 * person watching the pane is waiting for. The fenced JSON block the driver
 * parses is dropped — it is the machine's copy of the same outputs the panel
 * already lists above.
 */
export function answerTailOf(answer: string): readonly string[] {
  const withoutFences = answer.replace(/```(?:json)?[ \t]*\r?\n[\s\S]*?```/g, '')

  return withoutFences
    .split('\n')
    .map(line => line.trim())
    .filter(line => line !== '')
    .slice(-ANSWER_TAIL_LINES)
}

/**
 * The live line for a tool a spawned subagent is running now:
 * `now: Bash git status… (12s)`.
 *
 * The elapsed clock is the point: a `Bash` that has been the newest call for
 * four minutes is the difference between a step working and a step wedged,
 * and nothing else the pane draws says so.
 */
export function liveLineOf(live: LiveTool, nowMs: number): string {
  const elapsed = Math.max(0, nowMs - live.atMs)
  const argument = live.argument === '' ? '' : ` ${live.argument}`

  return `now: ${live.tool}${argument} (${secondsTextOf(elapsed / 1000)})`
}

/** A tool call's main argument, cut to `LIVE_ARG_CHARS` with an ellipsis. */
export const liveArgumentOf = (argument: string): string =>
  ellipsize(argument.replace(/\s+/g, ' ').trim(), LIVE_ARG_CHARS)

/**
 * The tool.call arguments, in priority order, that name what a call is doing.
 *
 * `command` for Bash, `file_path` for the file tools, `pattern` for the search
 * ones, `description` for a spawn, `prompt` last: whichever the call carries
 * first is the one drawn.
 */
export const LIVE_ARG_KEYS: readonly string[] = [
  'command',
  'file_path',
  'path',
  'pattern',
  'query',
  'description',
  'prompt',
]

/**
 * Every line of the log panel for one step, in drawing order: a heading, the
 * live tool calls (newest last), the attempts (newest first), then the
 * outputs and artifacts of the newest attempt, then the answer's tail.
 *
 * Built as plain strings so a test can assert the whole panel without a
 * terminal, exactly as `tableRowsOf` is.
 *
 * @param stepId the step the panel is showing
 * @param log what was read for it, or null before the first read
 * @param columns the pane body's width, which every line is cut to
 * @param nowMs the clock, for the live lines' elapsed spans
 * @param cwd the run's directory, for relative artifact paths
 */
export function logLinesOf(
  stepId: string | null,
  log: StepLog | null,
  columns: number | null,
  nowMs: number,
  cwd = '',
): readonly string[] {
  if (stepId === null) {
    return []
  }

  const width = columns ?? DEFAULT_BODY_COLUMNS
  const lines: string[] = [`── ${stepId}`]

  if (log === null) {
    lines.push('reading…')

    return lines.map(line => ellipsize(line, width))
  }

  for (const live of log.live) {
    lines.push(liveLineOf(live, nowMs))
  }

  const [newest] = log.attempts

  for (const attempt of log.attempts) {
    lines.push(attemptLineOf(attempt))
  }

  if (newest !== undefined) {
    lines.push(...outputLinesOf(newest), ...artifactLinesOf(newest, cwd))
  }

  if (log.answer !== undefined) {
    lines.push(...answerTailOf(log.answer))
  }

  if (lines.length === 1) {
    lines.push('no attempts yet')
  }

  return lines.map(line => ellipsize(line, width))
}

/**
 * The window of log lines drawn, given the panel's scroll offset.
 *
 * The offset is clamped here rather than where the scroll event lands, so a
 * panel whose step changed under a scrolled window (the run advanced) can
 * never draw blank: a too-large offset simply shows the last page.
 */
export function logWindowOf(
  lines: readonly string[],
  offset: number,
  rows: number = LOG_PANEL_ROWS,
): readonly string[] {
  if (lines.length <= rows) {
    return lines
  }

  const last = lines.length - rows
  const from = Math.min(Math.max(0, offset), last)

  return lines.slice(from, from + rows)
}

/** The Button key for the row that selects a step; `register.ts` reads it back. */
export const stepKeyOf = (stepId: string): string => `orchestrator-step-${stepId}`

/** The step a press names, or null when the key is not a step row's. */
export function stepIdOf(key: string): string | null {
  const match = /^orchestrator-step-(.+)$/.exec(key)

  return match?.[1] ?? null
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
  /** Show this step's log in the panel below the table. */
  select: (stepId: string) => void
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
 * @param columns the pane body's width, which picks the tier
 */
export function nodeChildrenOf(
  Text: Elements['terminal']['Text'],
  status: StatusJson,
  columns: number | null,
  select?: {
    Button: Elements['terminal']['Button']
    onSelect: (stepId: string) => void
    selected: string | null
  },
): RenderElement[] {
  const rows = tableRowsOf(status.nodes, status.totals ?? {}, columns)
  const lineOf = (node: StatusNode): string => nodeLineOf(node)

  // Every node row is a `plain` Button, which the terminal draws as its bare
  // label (claude-code.d.ts:678-684) — so the table still reads as a table —
  // while the site's focus ring can land on it and Enter selects the step
  // (`ui.press`, d.ts:639). The selected row is marked with `›` rather than a
  // background, because `TextProps` offers no highlight a Button's label
  // inherits and a glyph survives every terminal.
  const rowElement = (
    key: string,
    text: string,
    props: { bold?: boolean; dimColor?: boolean; color?: string },
  ): RenderElement => {
    if (select === undefined) {
      return Text({ ...props, children: text })
    }

    const isSelected = select.selected === key
    const marked = `${isSelected ? '›' : ' '}${text}`

    return select.Button({
      key: stepKeyOf(key),
      plain: true,
      ...(props.dimColor === true ? { dimColor: true } : {}),
      label: marked,
      onPress: () => select.onSelect(key),
    })
  }

  if (rows.length === 0) {
    return status.nodes.map(node =>
      rowElement(node.id, lineOf(node), { dimColor: isPendingStatus(node.status) }),
    )
  }

  return rows.map(row => {
    const props = {
      bold: row.kind !== 'node',
      dimColor: row.kind === 'node' && isPendingStatus(row.status),
      ...(row.kind === 'node' && isRunningStatus(row.status)
        ? { color: 'cyan' }
        : {}),
    }

    // The header and Totals rows are not steps: they stay plain Text, and the
    // leading space keeps their cells lined up with the marked node rows.
    return row.kind === 'node'
      ? rowElement(row.key, rowTextOf(row), props)
      : Text({ ...props, children: select === undefined ? rowTextOf(row) : ` ${rowTextOf(row)}` })
  })
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
 * @param columns the pane body's width, which picks the table's column set;
 *   null (before any drawing reported one) assumes `DEFAULT_BODY_COLUMNS`
 * @returns the tree to return from the `ui.render` hook
 */
export function paneView(
  ui: Pick<Elements['terminal'], 'Box' | 'Text' | 'Button'>,
  model: PaneModel,
  actions: PaneActions,
  columns: number | null = null,
  nowMs: number = Date.now(),
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

  const selected = selectedStepOf(status.nodes, model.logs.selected)

  const nodes = Box({
    key: 'nodes',
    flexDirection: 'column',
    children: nodeChildrenOf(Text, status, columns, {
      Button,
      onSelect: actions.select,
      selected,
    }),
  })

  // The log panel sits between the table and the parked-question block, so
  // the action row stays at the bottom where the eye already looks for it.
  const logLines = logWindowOf(
    logLinesOf(selected, model.logs.log, columns, nowMs, model.cwd),
    model.logs.offset,
  )

  const logPanel =
    logLines.length === 0
      ? []
      : [
          Box({
            key: 'logs',
            flexDirection: 'column',
            children: logLines.map((line, index) =>
              Text({ dimColor: index > 0, children: line }),
            ),
          }),
        ]

  return Box({
    flexDirection: 'column',
    children: [
      Text({ bold: true, children: head }),
      nodes,
      ...logPanel,
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
  const stepId = stepIdOf(key)

  if (stepId !== null) {
    actions.select(stepId)

    return
  }

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
