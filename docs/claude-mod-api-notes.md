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

| Need            | API                                                                                                                                                            | Notes                                                                                                                                                                          |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Custom tool     | `$.tool.register({name, description, inputSchema})` (ToolSpec d.ts:8262)                                                                                       | call from `session.start`; serve via `on('tool.call', e.tool === 'mcp__<plugin>__<name>' ? {result} : next(e))`                                                                |
| Spawn subagent  | `$.agent.spawn({prompt, subagentType?, model?, name?, cwd?, description?})` → `{agentId, model} \| {deny}` (d.ts:191-317, 2405)                                | NO system/tools/schema/maxTurns fields. Isolation = generated `agents/<step>.md` (frontmatter: model, tools) selected by `subagentType`. Resolves when started, not when done. |
| Subagent result | `on('turn.complete', ($,e,next) => …)` keyed by `e.agentId` (d.ts:8383)                                                                                        | `e.answer: string`, `e.usage?: TurnUsage`, `e.reason`, `e.durationMs`                                                                                                          |
| Gate prompt     | `$.ui.ask(question, {options: [...], header?})` → chosen label or free text (d.ts:1908)                                                                        | rejects in `-p` headless                                                                                                                                                       |
| Deny writes     | `on('tool.call', ($,e,next) => …{deny: reason})` (d.ts:2849, 7916)                                                                                             | `e.tool` discriminates; `e.agentId` present in subagent                                                                                                                        |
| Preflight perm  | `$.tool.check({tool,input})` → `{decision}` (d.ts:2301)                                                                                                        |                                                                                                                                                                                |
| Subprocess      | `$.process.run(argv, {cwd, env, stdin, timeoutMs})` → `{exitCode, stdout, stderr}` (d.ts:2595, 5385)                                                           | timeout default 30s, max 10min                                                                                                                                                 |
| State           | `$.store.get/set/delete/keys` (d.ts:2486)                                                                                                                      | JSON, 4 MiB                                                                                                                                                                    |
| Status line     | `$.ui.status(text)`; toast `$.ui.toast`; pane `$.ui.open({id,title})` + `on('ui.render', {component:'Pane'})` returning Box/Text/Button from `$.ui.resolve(e)` | see mods/diff                                                                                                                                                                  |
| Files           | `$.fs.read/write/list/exists/stat` (d.ts:2430)                                                                                                                 | text only, cwd-relative                                                                                                                                                        |
| Env             | `$.env.get('NAME')` literal names only (d.ts:2632)                                                                                                             |                                                                                                                                                                                |
| Slash cmd       | `on('command.register', …)`, `on('command.run', …)`                                                                                                            |                                                                                                                                                                                |

## Events for the driver loop

`session.start` (register tool here) → `tool.call` (our tool) → loop:
`$.process.run(['orchestrator','step',run,'--json'])` → judgment: `$.agent.spawn`
→ await `turn.complete[agentId]` → parse JSON block from `answer` →
`$.process.run(['orchestrator','done',run,step,'--out',…,'--usage',…])`;
gate: `$.ui.ask` → `orchestrator approve`; `needs_you` with `payload.ask`
(await_input): `$.ui.ask` → `orchestrator resume <run> "<answer>"`, loop;
`needs_you` with no `ask`: report, stop.

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

**Per-node cost is not in `status --json`.** `protocol.py`'s `status`
projects only `id/phase/kind/status/attempts` onto each node; the model and
`cost_usd` live in `step_history[].usage`, which only `orchestrator events
<run> --json` returns raw. The pane therefore makes both calls and folds the
event rows by `step_id` (last attempt wins the model, costs sum).
`record.py` stamps `cost_partial` when a model has no pricing row, which is
what the footer's `(partial)` reports.

**The gate buttons and the approval dialog answer the same gate.** `runGate`
races `$.ui.ask` against a promise the pane's `onPress` resolves, so a press
and a dialog answer run one code path. The dialog that loses the race can
still reject later (the surface tearing it down), so its rejection is
swallowed once a press has won — otherwise it reads as an unattended run and
would leave the gate standing after the person already approved it.
