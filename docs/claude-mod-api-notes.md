# Claude Code Mod API — verified notes (Sept 2026, claude 2.1.273)

Source: github.com/anthropics/claude-code/tree/main/mods (+ types/claude-code.d.ts,
10,772 lines; line refs below). Early access; API may change without notice.

## Enable / load / test

- Local enable: `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir <mod>`
  (debug log: "hooks module <name> loaded (worker, …)").
- Layout: `.claude-plugin/plugin.json` + `hooks/hooks.json`
  `{"description": "...", "modules": ["./register.ts"]}` + `hooks/register.ts`.
- `claude plugin test` is NOT in 2.1.273; `tsc -p tsconfig.json` against
  `types/claude-code.d.ts` is the check.

## Entry

`export const register = (on, options) => { … }` (Register, d.ts:5891).
Hook: `on(pattern, ($, e, next) => result)`; `next(e)` continues the chain,
returning without `next` short-circuits. `on(...).catch(h)` once.
`engine.create` fold adds nouns: `const beneath = await next(e); return {...beneath, noun}`.

## Nouns used by the orchestrator mod

| Need            | API                                                                                                                                                            | Notes                                                                                                                                                                                                                                                                            |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Custom tool     | `$.tool.register({name, description, inputSchema})` (ToolSpec d.ts:8262)                                                                                       | call from `session.start`; serve via `on('tool.call', e.tool === 'mcp__<plugin>__<name>' ? {result} : next(e))`                                                                                                                                                                  |
| Spawn subagent  | `$.agent.spawn({prompt, subagentType?, model?, name?, cwd?, description?})` → `{agentId, model} \| {deny}` (d.ts:191-317, 2405)                                | NO system/tools/schema/maxTurns fields. Isolation = generated `agents/<step>.md` (frontmatter: model, tools) selected by `subagentType`. Resolves when started, not when done. `model` accepts **only** the four aliases `"sonnet" \| "opus" \| "haiku" \| "fable"` — see below. |
| Subagent result | `on('turn.complete', ($,e,next) => …)` keyed by `e.agentId` (d.ts:8383)                                                                                        | `e.answer: string`, `e.usage?: TurnUsage`, `e.reason`, `e.durationMs`                                                                                                                                                                                                            |
| Gate prompt     | `$.ui.ask(question, {options: [...], header?})` → chosen label or free text (d.ts:1908)                                                                        | rejects in `-p` headless                                                                                                                                                                                                                                                         |
| Deny writes     | `on('tool.call', ($,e,next) => …{deny: reason})` (d.ts:2849, 7916)                                                                                             | `e.tool` discriminates; `e.agentId` present in subagent                                                                                                                                                                                                                          |
| Preflight perm  | `$.tool.check({tool,input})` → `{decision}` (d.ts:2301)                                                                                                        |                                                                                                                                                                                                                                                                                  |
| Subprocess      | `$.process.run(argv, {cwd, env, stdin, timeoutMs})` → `{exitCode, stdout, stderr}` (d.ts:2595, 5385)                                                           | timeout default 30s, max 10min                                                                                                                                                                                                                                                   |
| State           | `$.store.get/set/delete/keys` (d.ts:2486)                                                                                                                      | JSON, 4 MiB                                                                                                                                                                                                                                                                      |
| Status line     | `$.ui.status(text)`; toast `$.ui.toast`; pane `$.ui.open({id,title})` + `on('ui.render', {component:'Pane'})` returning Box/Text/Button from `$.ui.resolve(e)` | see mods/diff                                                                                                                                                                                                                                                                    |
| Files           | `$.fs.read/write/list/exists/stat` (d.ts:2430)                                                                                                                 | text only, cwd-relative                                                                                                                                                                                                                                                          |
| Env             | `$.env.get('NAME')` literal names only (d.ts:2632)                                                                                                             |                                                                                                                                                                                                                                                                                  |
| Slash cmd       | `on('command.register', …)`, `on('command.run', …)`                                                                                                            |                                                                                                                                                                                                                                                                                  |

## Events for the driver loop

`session.start` (register tool here) → `tool.call` (our tool) → loop:
`$.process.run(['orchestrator','step',run,'--json'])` → judgment: `$.agent.spawn`
→ await `turn.complete[agentId]` → parse JSON block from `answer` →
`$.process.run(['orchestrator','done',run,step,'--out',…,'--usage',…])`;
gate: `$.ui.ask` → `orchestrator approve`; `needs_you` with `payload.ask`
(await_input): `$.ui.ask` → `orchestrator resume <run> "<answer>"`, loop;
`needs_you` with no `ask`: report, stop.

## Spawn's `model` only accepts the four aliases

Verified against Claude Code 2.1.274: `$.agent.spawn({model: "claude-sonnet-5"})`
was refused outright — no agent spawned, no `turn.complete` —
with `InputValidationError: model — invalid value; allowed:
["sonnet","opus","haiku","fable"]`. A full model id (what `payload.model_id`
carries — the actual routed id from `models.yaml`, resolved by
`protocol.py`'s `_step_model_id`) is **not** an accepted value for spawn's
`model`, only these four family aliases are, and `"fable"` is one of them
even though it is _not_ a valid agent frontmatter `model:` value (per the
`plugin-dev:agent-development` skill, which lists only
`inherit/sonnet/opus/haiku`).

`register.ts`'s `spawnModelOf` bridges this: it maps `payload.model_id`'s
family prefix (`claude-fable-`/`claude-mythos-` → `fable`, `claude-opus-` →
`opus`, `claude-sonnet-` → `sonnet`, `claude-haiku-` → `haiku`, table
`MODEL_FAMILY_TO_SPAWN_ALIAS` in `protocol.ts`) down to the alias spawn will
accept, falling back to the pack's tier alias (`payload.model`, e.g.
`"strong"` → `"opus"`) when `model_id` is absent or unrecognized, and to
`undefined` (letting the agent definition's own frontmatter `model:` decide)
when neither resolves. This only changes what's asked of `spawn`; the model
that actually answered (from `turn.complete`'s `usage.model`) is still what
`orchestrator done --usage` records, so `pricing.yaml` lookups stay exact
regardless of which alias was requested.

Generated agent frontmatter (`pack_export.py`'s `ALIAS_TO_CLAUDE_MODEL`) keeps
mapping `fable` → `opus`, since that file is inert at spawn time (spawn's
explicit `model` always overrides frontmatter) but must still parse as valid
frontmatter if `$.agent.spawn`'s `model` is ever omitted.

## The 10s hook budget, and driving a long run anyway

A `tool.call` hook gets **10,000ms of real time**. Past it the engine reports
`exceeded 10000ms budget (tool.call; skipped; what is below it ran in its
place)`, answers the call itself, and the MCP tool fails with "registered the
tool … but no tool.call hook answered this call". Driving a whole recipe inside
the `run` hook therefore cannot work — a single judgment step outlives it.

**Background work survives the hook returning.** Verified against 2.1.273
(`.tmp/mod-e2e-4.log`): `run` settled in 565.4ms with a `result`, and the
unawaited loop then drove `explore` → `design` → `design-review`, spawning
three subagents over the following eight minutes. `$.agent.spawn`,
`$.process.run` and `$.ui.status` all keep working after the hook settles, so
no `$.clock.after`/`every` tick scheduling is needed. The bundled `diff` mod
uses the same `void asyncFn().catch(…)` shape from its own hooks.

So `run` starts the run, kicks the loop off unawaited into a module-scope map
keyed by slug, and returns an acknowledgement inside a second; `status` reads
that record back beside `orchestrator status --json`.

**Unattended (`-p`) mode:** the loop is only alive as long as the session is.
A `-p` prompt that returns immediately takes the driver down with it, and
`$.ui.ask` rejects with nobody to ask, so a gate leaves the run standing for
`orchestrator approve` from a shell. To exercise a `-p` run end to end, tell
the agent to poll the status tool on a sleep so the session stays up.

## The progress pane (`hooks/pane.ts`)

Drawn by a `ui.render` hook on `{ component: 'Pane' }` matching
`e.requestId === 'orchestrator'`. Verified against the d.ts:

| API                                   | d.ts                      | Note                                                                                                                                                 |
| ------------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `$.ui.open({id,title,closeOnEscape})` | 1955, `PaneOpenArgs` 4901 | One pane per id; an **unasked** open is parked undrawn below 144 columns, an asked one below 110 (4897-4899).                                        |
| `$.ui.close({id})`                    | 1969                      | Raises `ui.close` with `e.origin` naming whose close it is.                                                                                          |
| `$.ui.invalidate('ui.render')`        | 1853                      | Re-runs the cached render; the only way to redraw after a refresh.                                                                                   |
| `$.ui.resolve(e)`                     | 1886                      | A read, not a dispatch. `Box`/`Text`/`Button` are **not** globals.                                                                                   |
| `ElementConstructor`                  | 2707                      | `(props: P & ElementChildren) => RenderElement` — children are a **prop**, not variadic arguments.                                                   |
| `BoxProps.key` / `ButtonProps.key`    | 518 / 641                 | `TextProps` (7832) has **no** `key`: a keyed `Text` is a tsc error.                                                                                  |
| `ui.press`                            | `UiPressArgument` 8988    | `e.element` is the Button's `key`; core runs `onPress` **beneath** the hook chain (9021).                                                            |
| `$.command.register`                  | `CommandSpec` 1359        | Takes `name`/`description`/`argumentHint`. It can reject when the name is taken, so the call is caught: a lost command must not take the tools down. |
| `$.clock.every`                       | 2553, `TimerCall` 7886    | Returns a `Timer` with `.cancel()` — not a bare function (the `every` at 1097 is a `Client` instance's, a different noun).                           |
| `$.ui.toast` / `$.ui.status`          | 1927 / 1938               | Transient line vs this plugin's pinned one. A pane opened with `holdToasts` **swallows** toasts until it closes, so this pane is opened without it.  |

**Per-node metrics ride on `status --json`.** `protocol.py`'s `node_metrics`
folds `step_history` into one row per step and `status` projects it onto each
node: `model`, `verdict`, `seconds`, the four token counts
(`input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_write_tokens`),
`cost_usd` and `cost_partial`, plus a run-level `totals` of the same numeric
keys. Counts and `seconds` **sum** across a step's attempts; `model` and
`verdict` take the **last** attempt's. The pane therefore makes one call and
no longer folds `events --json` itself. `record.py` stamps `cost_partial` when
a model has no pricing row, which is what a `?` on a cost cell and the
footer's `(partial)` report.

**The pane draws a metrics table, in three width tiers.** `tableRowsOf`
(pane.ts) pads every cell to its column's width so the numbers line up, and
`columnsFor` picks the column set from the terminal width:

| Width   | Columns                                                                        |
| ------- | ------------------------------------------------------------------------------ |
| ≥150    | Step · Model · Att · Verdict · Time · In · Out · C-rd · C-wr · Cost + cost bar |
| 110–149 | Step · Model · Att · Verdict · In · Out · Cost                                 |
| <110    | no table — the one-line-per-node list (`nodeLineOf`)                           |

A header row and a bold Totals row bracket the nodes. The cost bar is eight
cells of block characters scaled to the priciest row, and rounds **up** to one
eighth so a cheap step still draws something rather than vanishing. Row
styling is limited to what `TextProps` allows (7841-7845: `color`, `dimColor`,
`bold` — no `key`): header and Totals bold, the running row `color: 'cyan'`,
untouched rows dimmed.

**A missing JSON block is not a failed step.** When a judgment subagent's
final message carries no parseable fence, `runJudgment` does NOT record an
abandon: it calls `done --out '{}'` and lets `protocol.validate_out` decide,
because a contract whose outs are all optional or artifact-backed is satisfied
by what the step wrote to disk (`learn`'s single optional `proposed_scenarios`
artifact is the live case). Only when the engine refuses the call — `done`
exits 3 with `{"status": "error", …}`, which `nextOf` surfaces as `error` — is
the step recorded `abandoned`, with the engine's complaint as the reason
because it names the out that is actually missing.

**Do not re-prefix the engine's abandoned reason.** `record.py` writes
`needs_you_reason` as `"<step_id> abandoned: <detail>"` (or `rejected` for a
`fail_on:` verdict) and `dispatch.py` falls back to a bare
`"<step_id> abandoned"`, so both the pane's `retryTextOf` and the retry popup
print it as-is when it already opens with the step id. Prefixing
unconditionally drew `learn abandoned: learn abandoned: …` in a live run.

**The gate buttons and the approval dialog answer the same gate.** `runGate`
races `$.ui.ask` against a promise the pane's `onPress` resolves, so a press
and a dialog answer run one code path. The dialog that loses the race can
still reject later (the surface tearing it down), so its rejection is
swallowed once a press has won — otherwise it reads as an unattended run and
would leave the gate standing after the person already approved it.

## Making the mod the control surface (`hooks/actions.ts`)

The mod used to need the chat for everything a person decides: a run started
by asking the model to call the `run` tool, and anything that parked was
unstuck with shell commands. Three surfaces now drive a run — the MCP tools,
the `/orchestrator` command, and the pane's Buttons — and all three call the
same six functions in `actions.ts` (`startRun`, `approve`, `cancel`, `retry`,
`resume`, plus the `listRecipes`/`listRuns`/`currentRun` reads).

`actions.ts` never touches `$`. `register.ts` passes an `ActionHost` holding
the CLI runner, the active run, and the driver loop's two parked resolvers.
That split matters for correctness, not just tidiness:

**An action answers the parked loop first, the CLI second.** When a driver is
awaiting its gate promise, `approve` settles THAT promise rather than calling
`orchestrator approve` itself — a second approve behind the loop's back would
consume the token the loop is about to use and leave it awaiting a gate that
no longer exists. `answerGate`/`answerRetry` return false when nothing is
parked, and only then does the action go to the CLI (which is how a run left
standing by an earlier session is still approvable).

**A listing verb must be shape-checked, not just parsed.** `safeJson(...) ?? []`
is not enough: an older `orchestrator` on `$PATH` answers `status --json`
with `{"status": "error", …}`, which parses fine into a non-array object and
then throws `(...).filter is not a function` out of the `command.run` hook —
where it surfaces as "registered /orchestrator but no command.run hook
answered it", naming nothing useful. `rowsOf` checks `Array.isArray`, so a
stale CLI degrades to an empty menu. Verified against 2.1.273: with the old
wheel on PATH the command answers "no runs. Start one with `/orchestrator
run`" instead of failing.

### `$.command.register`'s `argumentHint` is the whole grammar

One command carries every verb (`run`, `approve`, `cancel`, `retry`,
`resume <text>`, `status`, `pane`); `command.run` gets them in `e.args` as
typed and `runCommand` splits them. Bare `/orchestrator` is contextual: it
toggles the pane when there is a run to look at, and opens the start wizard
when there is not.

### A dismissed popup must not take the decision with it

`$.ui.ask` rejects both on dismissal and in `-p`. The loop used to treat that
as the end of the road. Now the parked gate/question STAYS in the pane model
when the dialog rejects, so the action row keeps offering the same choices, a
toast says where to press, and `pane.answerGate` is dropped only because this
loop stopped awaiting it — a later press then routes through `approve`, which
approves the standing gate via the CLI. An unattended run is unchanged:
nothing ever presses, and the run is left standing exactly as before.

### Two new CLI verbs back the pickers

`orchestrator recipes --json` lists every recipe in the resolved pack(s) with
`{name, pack, steps, gates, inputs}` — `inputs` is what the wizard asks for
beyond a slug, and `recipeRefOf` qualifies a name as `<pack>/<name>` only when
it collides. `orchestrator status --json` with **no run** lists live runs
(`{slug, run_id, run_status, recipe, current_step}`). `state list` was not
reusable for this: it takes a store URL and prints a fixed-width table for a
human at a shell.

### Button keys carry data the press event does not

`ui.press` reports only `e.element`, the Button's `key` (d.ts:8988), so an
option Button encodes its index in its key (`orchestrator-option-<n>`) and
`pressOf` reads the LABEL back out of the pane model. The `ui.press` matcher
lists every key the row can draw, including all four option keys.
