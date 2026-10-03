# AGENTS.md

Guidance for agents working in this repo. For what the engine _is_ and how to
call it, read [`README.md`](README.md) first.

## The design, in one paragraph

The engine is deliberately dumb: `orchestrator next` is a pure function from
(workflow config + the step that just ran + how it went) to the next step, as
JSON. It keeps no state, spawns no processes, calls no models, and reads no
environment beyond an explicit `--config` fallback. Everything else belongs to
the **driver** — an agent plus [`skills/drive/SKILL.md`](skills/drive/SKILL.md)
— which owns run history, attempt counts, gate approvals, the worktree, and
model choice. Packs (workflows, step contracts, charters, scripts) are
installed by a separate tool and handed in with `--config`; the engine never
fetches one. When a decision could live in the engine or the driver, it lives
in the driver unless the engine is the only thing that knows the answer — the
contract's `fail_on`, for instance, stays here, because a driver that forgets
it would advance past a rejected review.

## Layout

```text
orchestrator_next/
  cli.py             arg parsing, the single `next` verb, JSON output
  nextstep.py        the engine: routing, payloads, stdout protocol, out validation
  parser.py          step contracts (contract.yaml → typed dataclass)
  workflow_steps.py  workflow step-entry normalization
  tests/
skills/drive/        the driver skill — the other half of the contract
docs/                pack-driver-notes.md (belongs upstream in the pack)
```

Five modules, ~1,250 lines. If a change makes that meaningfully bigger, check
whether it belongs in the driver instead.

## Dev

```bash
uv sync --extra dev
python -m orchestrator_next --help
pytest orchestrator_next/tests/ -q
```

Tests need a pack. They use the checkout's own `.orchestrator/workflows/` when
present (gitignored — it is installed, not vendored) and skip otherwise.
Pre-commit runs ruff, pytest and vulture; don't bypass it.

## Rules for agents in this codebase

1. **evidence-based** — verify before claiming done. Run the thing.
2. **minimal-diffs** — scope to the task.
3. **agent-agnostic** — no vendor names in the engine, schemas or steps.
4. Prefer Python for YAML/state logic over new bash.

Two more that this engine earned the hard way:

5. **The engine may not guess.** If it cannot know something (how many times a
   step has run, where a worktree is, which model to use), it must not emit a
   number or a path that looks like an answer. A driver will trust it.
6. **Nothing the engine prints may carry the caller's environment.** Payload
   `env` blocks are engine-set only; copying `os.environ` into one printed a
   full set of secrets to stdout before it was caught.

## Pack installation

Lives in the workflows repo, not here. A pack is a directory; point `--config`
at it. Its layout and the driver contract are in `README.md` and
[`docs/pack-driver-notes.md`](docs/pack-driver-notes.md).

<!-- cc-profile:agents:start (generated) -->

## Shared agent rules

Every rule here traces to a real incident. Add rules only with an incident behind them; delete rules that stop firing.

### Memory

- The memory plane is **agentmemory** (hub `http://localhost:3111`, MCP `agentmemory`, skills `/remember` `/recall` `/handoff` `/recap` `/scratchpad`). Durable knowledge — decisions, gotchas, how-things-work, cross-session state — goes there via `remember`; past-work questions go through `recall`/`smart-search` FIRST, before grep-archaeology or any per-tool memory. Do not create new per-agent or per-tool memory silos.
- Active multi-step work uses **scratchpad slots** (`memory_slot_*`, keyed by ticket or branch) so Cursor, Claude Code, and Codex share WIP state. Promote durable learnings with `memory_save`/`memory_lesson_save`, then delete the slot when done.
- **Scoping**: `memory_save` does NOT derive the project from cwd — pass `project` explicitly (the repo's directory name, e.g. `paperclip-factory`) for project-specific memory; machine-wide knowledge uses no project plus concept tag `global`. Recall project-first, then global/unfiltered.
- When continuing implementation after a prior agent session, treat recent agentmemory observations for the same project/topic as the default source of truth unless the code has since diverged.

### Orchestration

- The main agent plans, scopes, integrates, and resolves decisions; subagents execute concrete edits. Use the smaller/cheaper model for execution, the strongest model for conflict-laden merges, cross-cutting refactors, and subtle debugging.
- **Route work by task type to the matching skill, not a bespoke per-agent file.** Skills are portable across coding agents (Claude Code, Cursor, Codex); per-harness subagent tool/MCP scoping is not. Use: `explorer` for investigating a bug, tracing code, or read-only impact analysis; `designer` for planning an approach or breaking work into tasks; `developer` for implementing a scoped change from a plan/brief; `code-reviewer` for reviewing a diff; `design-reviewer` for reviewing a design/plan before implementation. (2026-09-15, playr session: Cursor/Codex agent formats can't express tool restriction.)

### Working rules

- When a constraint has ambiguous units or type (length limit, field type, API shape), state the assumption explicitly before building — don't guess.
- Before any destructive or scope-expanding change (removing files from git, changing tracked configs, deleting branches), state the rationale and confirm there isn't a smaller fix.
- For tooling, library versions, or external APIs, verify instead of answering from memory — prefer context7 docs, else web search.
- Before speccing tests, confirm the test tooling actually exists in the project (check package.json / lockfile) — don't assume a library is installed.
- If you notice a security issue outside the task scope, flag it — don't silently fix it.
- When you don't know something, say "I'm not sure about X" and propose how to verify it — never guess an answer.
- Create worktrees under `.worktrees/<name>` inside the repo (gitignored), not an external directory.
- Put repo-related scratch files in `.tmp/` inside the repo (gitignored), not `/tmp`. Doesn't override a harness's own per-session scratchpad.
- This repo is self-sufficient: plugins, skills, MCP servers and these rules are declared here, not in user-global config. Need something new? Add it to this repo (`cc-profile`, `npx skills add`, `.mcp.json`).

### Communication

- Short responses by default; extremely concise when reporting — sacrifice grammar for concision. Conclusions first, reasoning after. Don't sugarcoat technical risks.
- No cheerleading, no filler, no "great question".
- Disagree when you have good reason; state your confidence.
- If something is unclear, ask one focused question — not five.

<!-- cc-profile:agents:end -->

<!-- cc-profile:claude:start (generated) -->

## Claude Code specifics

- **Fable is the architect, not the builder.** When the main session runs Fable (or any Mythos-class model), it plans, designs, reviews, orchestrates, and resolves decisions; it does not write or edit code itself beyond trivial one-line fixes and config nudges. Every implementation, refactor, test-writing, exploration, and mechanical-edit task goes to a subagent (Agent tool): `model: "sonnet"` by default; `model: "opus"` only for judgment-heavy work. Fable verifies the subagent's result (run the checks, read the diff) and commits. (2026-09-02, loop-design session burning Fable tokens; 2026-09-03, narada restructure confirming the split.)
- The harness's file memory (`memory/` + MEMORY.md) is for session-bootstrap pointers only. Full memory content belongs in agentmemory.

<!-- cc-profile:claude:end -->
