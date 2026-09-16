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
gate: `$.ui.ask` → `orchestrator approve`; `needs_you`: report, stop.
