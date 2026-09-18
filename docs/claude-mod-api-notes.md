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

**The table's width tiers key off the PANE's own body, not the terminal.**

A live session showed the bug: the docked pane's `ui.render` hook was reading
`e.viewport?.columns`, which is the **terminal's** width (`RenderInputOf`,
d.ts:6237-6244) — the conversation's full column count, not what the pane
itself has room for. A docked pane's actual body ran ~50 columns in that
session, so `columnsFor`'s old 110/150 thresholds never fired and the table
always fell back to the compact list, even in a wide terminal.

The `Pane` component's own props carry the real number: `bodyColumns`,
"Cells across the body, inside the frame" (d.ts, `RenderPropsOf['Pane']`,
read-only). `register.ts`'s render hook now reads `e.props.bodyColumns` into
`pane.bodyColumns` and passes _that_ to `paneView`/`tableRowsOf`, not
`pane.columns` (which still exists, fed by `e.viewport?.columns`, but only for
the `$.ui.open` floors — `AUTO_OPEN_MIN_COLUMNS`/`OPEN_MIN_COLUMNS` mirror the
surface's own terminal-wide thresholds at 4897-4899, a separate concern from
the table's layout). The diff mod (`mods/diff/hooks/register.ts`) reads the
same field the same way, reserving one column at the right edge
(`PANE_RIGHT_PAD_COLUMNS`) — this pane does not pad the edge, but truncates
the Step id instead (below).

`tableRowsOf` (pane.ts) pads every cell to its column's width so the numbers
line up, and `columnsFor` picks the column set from the pane body's width —
retiered for a docked pane's real size (no unknown-width case tests above
~50, since a real pane rarely gets there):

| Width  | Columns                                                                        |
| ------ | ------------------------------------------------------------------------------ |
| ≥120   | Step · Model · Att · Verdict · Time · In · Out · C-rd · C-wr · Cost + cost bar |
| 90–119 | Step · Model · Att · Verdict · Time · In · Out · Cost                          |
| 64–89  | Step · Model · Att · Time · Out · Cost                                         |
| 40–63  | Step · Att · Time · Cost                                                       |
| <40    | no table — the one-line-per-node list (`nodeLineOf`)                           |

`DEFAULT_BODY_COLUMNS = 48` is what `columnsFor(null)` assumes before the
first `ui.render` reports a real `bodyColumns` — the 40-63 tier, not the
widest, since a docked pane is more often narrow than wide.

A header row and a bold Totals row bracket the nodes. The cost bar is eight
cells of block characters scaled to the priciest row, and rounds **up** to one
eighth so a cheap step still draws something rather than vanishing. Row
styling is limited to what `TextProps` allows (7841-7845: `color`, `dimColor`,
`bold` — no `key`): header and Totals bold, the running row `color: 'cyan'`,
untouched rows dimmed.

**A table row never exceeds the pane's own width.** `shrinkStepColumn`
compares the natural (unpadded) row width — every column's width, the
inter-column gaps, and the cost bar when it rides — against the pane's
`bodyColumns`, and shrinks the Step column (always index 0) by however much
the row overflows, down to a floor of one cell. `ellipsize` then clips the
Step cell's text to that width with a trailing `…`. A step id is the column
most likely to be long and the one already carrying a status glyph, so it
absorbs the cut rather than every numeric column shrinking a little and
breaking alignment with the header.

### The per-step log panel

**A step row is a `plain` Button, which is how selection works at all.** The
pane has no key event of its own: `ClientKeyEvent`/`surface.onKey` (d.ts:1107, 946) belongs to a `Client`, not to a render hook, and there is no `ui.key`.
What a pane body _does_ have is a focus ring over the `Button`/`Input`/`Select`
elements a hook drew in it (`UiFocusComponent`, d.ts:8789), with Enter under
the ring raising `ui.press` (d.ts:639). So each node row is drawn as a Button
with `plain: true` — the terminal then draws its bare label rather than
`[ label ]` (d.ts:678-684) — and the table still reads as a table while Tab
reaches every row and Enter selects it. Header and Totals rows stay `Text`,
with a leading space so their cells line up with the marked node rows. The
selected row is marked with a leading `›`: `ButtonProps` offers no background,
and `TextProps` (7832) has no highlight a Button's label would inherit.

Keys round-trip through `stepKeyOf` / `stepIdOf` (`orchestrator-step-<id>`),
the same shape the option Buttons use, because `ui.press` reports only the key
(d.ts:8988) and never the label.

**Selection follows the running step until the person picks one.**
`selectedStepOf` answers the pinned selection when it still names a live node,
else the running node, else the last node that got past `pending`. A selection
naming a step that left the plan falls back rather than blanking the panel.

**The panel scrolls itself, not the pane body.** `on('ui.scroll', {requestId:
PANE_ID})` takes the person's own moves (`e.origin.kind === 'person'`,
`UiScrollOrigin` d.ts:9142-9160) and moves the panel's own offset, returning
`{}` so the engine's body window stays put — otherwise scrolling the log would
slide the action row off screen. A plugin's own `$.ui.scroll` passes through
with `next(e)`, and so does every move when the log fits in its twelve rows.

**Live progress comes from `tool.call`, not `turn.step`.** `turn.step` is a
streaming event (`StreamingEventName`, d.ts:7629) whose chunks are the model's
own response — a `TurnStepToolChunk` (8669) only lands once the model has
finished emitting the call, so a long `Bash` would show nothing at all while it
ran, which is exactly the case the line exists for. `tool.call` carries
`e.agentId` (AgentLoop, d.ts:7916), which ties a call to the subagent and so to
its step. The hook is a strict observer: it always returns `next(e)`, so it can
neither block a call nor alter one. It matches a named tool list rather than
registering bare, because the engine refuses two bare `on('tool.call')` in one
module — and every name in it must be a key of this build's `BuiltinToolInputs`
(d.ts:610). `Grep`, `Glob` and `Task` are **not** declared there and were a tsc
error.

**The answer tail is in-memory only.** `record.py` writes the parsed `out` into
`step_history`, never the agent's message, so the tail is kept in a Map at
`turn.complete` and is gone on reload. That is the trade: this is a live
progress aid, not a record.

**`events` takes `--step`.** The panel asks for one node's attempts, and
without the filter it would read and parse the whole run's history on every
selection. `protocol.py`'s `events` filters on `step_id`; the flag is popped
into a local named `step_id`, **not** `step` — binding `step` in `main` makes
it a local for the whole function and the `elif verb == "step"` branch then
raises `UnboundLocalError`.

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
it collides. `orchestrator status --json` with **no run** lists runs.
`state list` was not reusable for this: it takes a store URL and prints a
fixed-width table for a human at a shell.

That listing is what the home screen draws, so it carries everything a row
shows and not just the run's identity: `{slug, run_id, run_status, recipe,
current_step, started_at, ended_at, cost_usd, cost_partial, nodes_done,
nodes_total, archived, stale, last_activity}`. Four things about it are
load-bearing:

- **Both the live and the archived blobs are listed.** `run_store` archives a
  finished run by flipping a flag rather than deleting it, so a listing that
  read only the live ones would empty its own past section. A run that appears
  in both is listed once, as live.
- **Ongoing runs sort first**, then the rest by `ended_at`/`last_activity`
  descending. An ongoing run reports `ended_at: null` on purpose — giving it
  its last attempt's end would sort it among the finished ones.
- **A run with `run_status` still ongoing is demoted to `stale: true`** when
  nothing has touched it (its state doc, step history, or gates) within
  `ORCHESTRATOR_STALE_AFTER_HOURS` (default 24). `run_status` itself is left
  alone — an earlier session's abandoned run keeps saying `active` forever,
  since nothing is left to ever flip it — but a stale row sorts with the past
  section by `last_activity` descending, and the pane's `isOngoingRun` treats
  it as not ongoing. `orchestrator status --json --all` is the escape hatch:
  it keeps every ongoing-status row in the ongoing section (`stale` still
  reported, sort only).
- **`--limit N` caps AFTER sorting** (default 20, `0` means every run), so the
  cap can never drop the ongoing row the list exists to show.

Every number is folded out of the state document in `_run_row`, so listing
twenty runs costs one store read each rather than twenty `status` calls.

### The home screen's two row shapes

`runLineOf` draws the two kinds of run row differently because they answer
different questions: an ongoing run is described by **what it is doing** (its
current step) and how long it has been at it, a finished one by **when it
ran** and how long it took. Both end with the cost.

Width is handled the way the metrics table handles it: the slug absorbs the
overflow first (as `shrinkStepColumn` shrinks the Step cell), and the whole
line is clamped as a last resort — a row wider than its pane wraps, which
pushes every row below it out of alignment, not just itself.

### A run the pane does not drive is read-only

`RunOwnership` is `live`, `elsewhere`, `past` or `stale`. Only `live` — this
session's own driver — gets Approve/Cancel/Retry. A run some other session (or
a headless run) is driving gets `[Home]` and nothing else: the pane's approve
answers the driver loop's own parked promise, so offering it for a loop in
another process would answer a gate that loop is already awaiting. A finished
run gets `[Start again]`, which pre-fills the wizard with its recipe and slug.
A `stale` run — `run_status` still reads ongoing, but nothing has touched it
in `ORCHESTRATOR_STALE_AFTER_HOURS` (`protocol.py` `_is_stale`) — gets
`[Cancel]`/`[Home]`: unlike a finished run it CAN still be cancelled (cancel
only marks the run), and that is the one useful thing left to do with it.

### Button keys carry data the press event does not

`ui.press` reports only `e.element`, the Button's `key` (d.ts:8988), so an
option Button encodes its index in its key (`orchestrator-option-<n>`) and
`pressOf` reads the LABEL back out of the pane model. The `ui.press` matcher
lists every key the row can draw, including all four option keys.

---

## `turn.complete` fires per turn, and only `reason: 'answer'` is an answer

`TurnCompleteReason` (d.ts:8432) is exactly:

```
'answer' | 'aborted' | 'refusal' | 'error'
```

There is **no** `awaiting-input` / `paused` / `interrupted` member. A
permission prompt or a clarifying question does not end a turn in this build,
so a subagent raises at most one `turn.complete` per run of its loop
(`agentId`: "each run of its loop one turn", d.ts:8400).

The driver therefore accepts **only `reason === 'answer'`** (and
`isAborted !== true`, since d.ts:8396 ties the two together) as a step's
result — `isFinalTurn` in `mod/protocol.ts`. The other three all carry a
partial or empty `e.answer`:

| `reason`  | What it means                                           |
| --------- | ------------------------------------------------------- |
| `aborted` | interrupted; whatever text exists is partial            |
| `refusal` | the model refused, no fallback model retried it         |
| `error`   | API error killed the turn; `usage` may be absent (8418) |

A non-final turn is remembered (`state.lastNonFinal`) rather than recorded, so
the step can be abandoned with the engine's own reason instead of the far less
useful "ended without a fenced ```json block".

### The watchdog

A loop that ends on `aborted` / `refusal` / `error` raises its non-final turn
and then **nothing more**, so waiting on the promise alone would hang the
driver forever. `$.clock.every(10s, …)` polls `$.agent.list()` and gives up
when the agent's `AgentInfo.status` (d.ts:104) is no longer `running`
(`completed`, `failed`, `killed`, …). An id the listing does not name at all
is **not** treated as finished: a workflow's own agents carry ids no list
names (d.ts:141), so absence is not evidence of termination.

Note `setInterval` is not part of the mod runtime's surface; `$.clock.every`
is (it is what the pane's refresh ticker uses).

## What zero-duration steps actually were

Run 01a0af3f recorded `duration_ms: 0` on **every** judgment step, including
ones that plainly did hours of real work (`explore` attempt 2 spanned
13:11→20:05). That was never a symptom of a turn resolving early — it is
`record.py` defaulting `started_at` to `now` when the `done` payload omits it,
then deriving `duration_ms` from `ended_at - started_at`.

`orchestrator done` now takes `--started-at ISO8601`, and the driver stamps it
when it spawns the subagent. The stamp must carry a trailing `Z`: record.py
parses it with `fromisoformat` after replacing one.

## Popup vs pane: never raise both

`$.ui.ask` **cannot be retracted**. When the pane was open, a gate raised both
the chat popup and the pane's Approve/Cancel row; the Button won the race and
the dialog stayed on screen, stale, inviting a second answer to a gate that
was already decided.

Policy (`shouldRaisePopup` in `mod/protocol.ts`), applied at all three parked
decisions — gate, retry and `await_input`:

- **Pane open** → do not raise `$.ui.ask` at all. Toast where to press, and
  wait on the pane's Button.
- **Pane closed** → raise the popup as before, racing it against the Button.

Each decision has exactly one code path for acting on the answer
(`approveGate` / `cancelGate`, `finishRetry`, `resumeWith`), so a verb means
one thing whichever surface produced it.

## Telling the main agent not to drive

On run 01a0af3f the main agent read a step report and started running
`orchestrator` itself: the sandbox denied its writes to `~/.orchestrator/state`
and it then "recorded a nominal estimate", i.e. invented a result for a step
the plugin was already driving. A second writer corrupts the run's state.

`DRIVER_GUIDANCE` (one string, `mod/register.ts`) now rides on four surfaces:
the `run` and `status` tool descriptions, the `run` tool's own result text, and
a `prompt.context` block while a run is live.

`prompt.context` (d.ts:3010-3020) fires **once per conversation** and is cached
until `$.ui.invalidate('prompt.context')`, so the driver invalidates it when a
run starts and again when one ends — otherwise the block is computed before any
run exists and would never appear. Blocks are `{ name, text }` (d.ts:5449); the
hook appends one named `orchestratorRun`.
