/**
 * The verbs the mod offers, in one place.
 *
 * The `run`/`status` MCP tools, the `/orchestrator` command and the pane's
 * Buttons are three surfaces onto the SAME six actions. Each one used to
 * reach for the CLI (or the driver's parked promises) on its own, which is
 * how a Button and a dialog could answer one gate two different ways. Every
 * surface now calls a function here, so "approve" means one thing however it
 * was asked for.
 *
 * Nothing here draws: `register.ts` owns the engine and the driver loop and
 * passes them in as an `ActionHost`. That keeps this module free of `$` (the
 * engine only follows `$` into `register.ts`'s hoisted top-level functions)
 * and makes each verb a plain data-in/data-out call.
 */

import type { RecipeRow, RunRow, StepResult } from './protocol'

/** What one `$.process.run` of the CLI answered, with the argv that ran. */
export type Ran = {
  argv: readonly string[]
  exitCode: number
  stdout: string
  stderr: string
}

/** Runs the `orchestrator` CLI: the only kind of process the mod starts. */
export type Cli = (argv: readonly string[], timeoutMs?: number) => Promise<Ran>

/**
 * What an action needs from `register.ts`.
 *
 * `answerGate` / `answerRetry` are the driver loop's parked resolvers: when a
 * run is live and standing at a gate, answering it means settling the promise
 * the loop is already awaiting, NOT calling `orchestrator approve` behind the
 * loop's back — that would race the loop's own approve and leave it awaiting
 * a gate the CLI had already consumed. They answer null when no loop is
 * parked, and the action falls through to the CLI.
 */
export type ActionHost = {
  cli: Cli
  /** The run this session is driving, when there is one. */
  activeRun: () => string | null
  /** Settles a parked gate decision; false when no loop is waiting on one. */
  answerGate: (choice: 'approve' | 'cancel') => boolean
  /** Settles a parked retry decision; false when no loop is waiting on one. */
  answerRetry: (choice: 'retry' | 'cancel' | 'leave') => boolean
  /** Starts a run and its background driver. Returns what to tell the caller. */
  start: (recipe: string, slug: string, inputs?: Record<string, unknown>) => Promise<string>
  /** Re-reads the CLI and redraws the pane. */
  refresh: () => Promise<void>
}

/** What every action answers: a line for the person, and whether it worked. */
export type ActionResult = {
  ok: boolean
  text: string
}

/** The verb an action ran, for the toast that follows it. */
export type ActionName =
  | 'start'
  | 'approve'
  | 'cancel'
  | 'retry'
  | 'resume'
  | 'status'

const ok = (text: string): ActionResult => ({ ok: true, text })
const failed = (text: string): ActionResult => ({ ok: false, text })

/**
 * `JSON.parse` that answers null rather than throwing on CLI noise.
 *
 * Shared with the pane's own reads: a verb that printed something
 * unparseable must degrade to "no rows" rather than take a menu down.
 */
export function safeJson<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T
  } catch {
    return null
  }
}

/**
 * The rows a listing verb printed, or `[]` for anything that is not an array.
 *
 * An older `orchestrator` on PATH answers these verbs with its usage text or
 * with `{"status": "error", …}` — both of which `safeJson` either rejects or,
 * worse, parses into a non-array object that `?? []` does NOT catch. Checking
 * the shape is what keeps a stale CLI from throwing
 * `(...).filter is not a function` out of a `command.run` hook.
 */
function rowsOf<T>(ran: Ran | null): T[] {
  const parsed = ran === null ? null : safeJson<unknown>(ran.stdout)

  return Array.isArray(parsed) ? (parsed as T[]) : []
}

/**
 * The recipes the resolved pack(s) offer (`orchestrator recipes --json`).
 *
 * Answers an empty list rather than throwing, since the only caller is a
 * picker: with no rows it falls back to asking for a name as free text.
 */
export async function listRecipes(host: ActionHost): Promise<RecipeRow[]> {
  const ran = await host.cli(['orchestrator', 'recipes', '--json']).catch(() => null)

  return rowsOf<RecipeRow>(ran)
}

/**
 * The live runs in the store (`orchestrator status --json`, no run).
 *
 * This is how `/orchestrator` with no argument decides whether to toggle the
 * pane (something is running) or open the wizard (nothing is).
 */
export async function listRuns(host: ActionHost): Promise<RunRow[]> {
  const ran = await host.cli(['orchestrator', 'status', '--json']).catch(() => null)

  return rowsOf<RunRow>(ran)
}

/**
 * The run an action without an explicit one should act on: this session's
 * driver, else the single live run, else none.
 *
 * With more than one live run and no driver, nothing is guessed — acting on
 * the wrong run is worse than asking, and every caller reports the choice.
 */
export async function currentRun(host: ActionHost): Promise<string | null> {
  const active = host.activeRun()

  if (active !== null) {
    return active
  }

  const live = (await listRuns(host)).filter(row => row.run_status === 'active')

  return live.length === 1 ? live[0]?.slug || live[0]?.run_id || null : null
}

/** Start a run: the wizard, the `run` tool and the pane's Start all land here. */
export async function startRun(
  host: ActionHost,
  recipe: string,
  slug: string,
  inputs?: Record<string, unknown>,
): Promise<ActionResult> {
  if (recipe.trim() === '' || slug.trim() === '') {
    return failed('orchestrator: a recipe and a slug are both needed to start a run.')
  }

  try {
    return ok(await host.start(recipe.trim(), slug.trim(), inputs))
  } catch (error: unknown) {
    return failed(`orchestrator: could not start ${recipe} ${slug}: ${String(error)}`)
  }
}

/**
 * Approve the gate the run is parked at.
 *
 * A live driver is awaiting its own gate promise, so the answer goes THERE:
 * the loop then mints-and-approves through the same code path the dialog
 * uses, and the run advances once. Only a run with no driver in this session
 * (one left standing by an earlier session, say) is approved through the CLI,
 * which needs the token value `status --json` reports.
 */
export async function approve(host: ActionHost, run?: string): Promise<ActionResult> {
  if (host.answerGate('approve')) {
    return ok('orchestrator: gate approved.')
  }

  const ref = run ?? (await currentRun(host))

  if (ref === null) {
    return failed('orchestrator: no run to approve; name one.')
  }

  const ran = await host.cli(['orchestrator', 'status', ref, '--json'])
  const status = safeJson<{ gates?: { id?: string; token?: string; status?: string }[] }>(
    ran.stdout,
  )
  // The open gate's own token VALUE — never its `token_name`, which
  // `approve` accepts silently and then rejects as an unknown token.
  const gates = Array.isArray(status?.gates) ? status.gates : []
  const open = gates.find(gate => gate.status === 'open' && gate.token)

  if (open?.token === undefined) {
    return failed(`orchestrator: ${ref} is not parked at a gate with a minted token.`)
  }

  const approved = await host.cli(['orchestrator', 'approve', ref, open.token, '--json'])
  const parsed = safeJson<{ status?: string; error?: string }>(approved.stdout)

  if (parsed?.status === 'error') {
    return failed(`orchestrator: approve failed: ${parsed.error ?? 'unknown error'}`)
  }

  await host.refresh()

  return ok(`orchestrator: ${ref} gate approved.`)
}

/**
 * Cancel the run.
 *
 * A parked loop is told first for the same reason `approve` tells it: it is
 * mid-await and must unwind its own way, recording the cancel as the reason
 * it stopped rather than discovering the run gone underneath it.
 */
export async function cancel(host: ActionHost, run?: string): Promise<ActionResult> {
  if (host.answerGate('cancel') || host.answerRetry('cancel')) {
    return ok('orchestrator: cancelling.')
  }

  const ref = run ?? (await currentRun(host))

  if (ref === null) {
    return failed('orchestrator: no run to cancel; name one.')
  }

  const ran = await host.cli(['orchestrator', 'cancel', ref, '--json'])
  const parsed = safeJson<{ status?: string; error?: string }>(ran.stdout)

  if (parsed?.status === 'error') {
    return failed(`orchestrator: cancel failed: ${parsed.error ?? 'unknown error'}`)
  }

  await host.refresh()

  return ok(`orchestrator: ${ref} cancelled.`)
}

/**
 * Retry the abandoned step the run is parked at.
 *
 * Without a parked loop this needs the step to reset, which `status --json`
 * does not name (it reports node statuses, not "the one that gave up"), so
 * the last `abandoned` node is what gets reset — the same node the loop's own
 * `reset-step` would have taken.
 */
export async function retry(host: ActionHost, run?: string): Promise<ActionResult> {
  if (host.answerRetry('retry')) {
    return ok('orchestrator: retrying.')
  }

  const ref = run ?? (await currentRun(host))

  if (ref === null) {
    return failed('orchestrator: no run to retry; name one.')
  }

  const ran = await host.cli(['orchestrator', 'status', ref, '--json'])
  const status = safeJson<{ nodes?: { id?: string; status?: string }[] }>(ran.stdout)
  const nodes = Array.isArray(status?.nodes) ? status.nodes : []
  const stuck = [...nodes]
    .reverse()
    .find(node => node.status === 'abandoned' || node.status === 'failed')

  if (stuck?.id === undefined) {
    return failed(`orchestrator: ${ref} has no abandoned step to retry.`)
  }

  const reset = await host.cli(['orchestrator', 'reset-step', ref, stuck.id, '--json'])
  const parsed = safeJson<{ status?: string; error?: string }>(reset.stdout)

  if (parsed?.status === 'error') {
    return failed(`orchestrator: reset-step failed: ${parsed.error ?? 'unknown error'}`)
  }

  await host.refresh()

  return ok(
    `orchestrator: ${ref} reset ${stuck.id}. Start the recipe on this slug ` +
      'again to drive it from there.',
  )
}

/**
 * Answer the question the run is parked on (`await_input`).
 *
 * Unlike a gate, there is no parked-promise path: `runAsk` awaits `$.ui.ask`
 * directly, and a `/orchestrator resume` typed while that dialog is up simply
 * resumes the run — the dialog then rejects and the loop reads the run's new
 * position from the CLI, which is where the answer already landed.
 */
export async function resume(
  host: ActionHost,
  text: string,
  run?: string,
): Promise<ActionResult> {
  if (text.trim() === '') {
    return failed('orchestrator: resume needs the answer to pass to the run.')
  }

  const ref = run ?? (await currentRun(host))

  if (ref === null) {
    return failed('orchestrator: no run to resume; name one.')
  }

  const ran = await host.cli(['orchestrator', 'resume', ref, text.trim(), '--json'])
  const parsed = safeJson<{ status?: string; error?: string; next?: StepResult }>(ran.stdout)

  if (parsed?.status === 'error') {
    return failed(`orchestrator: resume failed: ${parsed.error ?? 'unknown error'}`)
  }

  await host.refresh()

  return ok(`orchestrator: ${ref} resumed with "${text.trim()}".`)
}
