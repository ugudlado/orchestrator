# AGENTS.md

Read [`README.md`](README.md) first for what the engine is and how to call it.

## The design, in one paragraph

The engine is deliberately dumb: `orchestrator next` is a pure function from
(workflow config + the step that just ran + how it went) to the next step, as
JSON. It keeps no state, spawns no processes, calls no models, and reads no
environment beyond an explicit `--config` fallback. Everything else belongs to
the **driver** — an agent plus the [`orchestrate`](https://github.com/ugudlado/skills/blob/main/workflow/orchestrate/SKILL.md) skill (skills repo)
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
docs/                pack-driver-notes.md (belongs upstream in the pack)
```

Four modules, ~1,200 lines. If a change makes that meaningfully bigger, check
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

1. **minimal-diffs** — scope to the task.
2. **agent-agnostic** — no vendor names in the engine, schemas or steps.
3. Prefer Python for YAML/state logic over new bash.
4. **The engine may not guess.** If it cannot know something (how many times a
   step has run, where a worktree is, which model to use), it must not emit a
   number or a path that looks like an answer. A driver will trust it.
5. **Nothing the engine prints may carry the caller's environment.** Payload
   `env` blocks are engine-set only; copying `os.environ` into one printed a
   full set of secrets to stdout before it was caught.

## Shared agent rules

Add rules only with a real reason behind them (an incident or a deliberate decision); delete rules that stop firing.

### Memory

- **agentmemory** is the only memory plane (MCP `agentmemory`, hub `https://memory.curiousbots.in`, config in `~/.agentmemory/hub.env`). Durable knowledge goes there via `remember`; past-work questions go through `recall`/`smart-search` FIRST, before grep-archaeology. Recent observations for the same project/topic are the source of truth unless the code has diverged. No per-agent or per-tool memory silos; harness-local memory holds bootstrap pointers only.
- `memory_save` does NOT derive the project from cwd: pass `project` (the repo's directory name) explicitly; machine-wide knowledge uses no project plus concept tag `global`. Recall project-first, then global.
- Multi-step work keeps a **scratchpad slot** (`memory_slot_*`) keyed by ticket or `<repo>_<branch>` (`[a-z0-9_]`), shared by every coding agent. Keep its Goal / State / Next steps / Key file paths / Dead ends current. Never delete a slot or save it to memory yourself; the user reviews it.

### Orchestration

- **Tier models by work type.** The main session runs the larger model: planning, design, strategy, thinking partner, conflict-laden merges, subtle bugs, and verifying subagent output (run the checks, read the diff) before committing; it edits nothing beyond one-line fixes. Execution — implementation, tests, exploration, mechanical edits — goes to subagents on a smaller model, the smallest for pure lookup. The spawner picks the model per call; skills never pin one. (2026-09-02/03: top-tier tokens burned on edits.)
- **Skills, not bespoke agents — for every agent, subagents included.** Each subagent loads one installed skill (`.agents/skills/`: `explorer`, `designer`, `developer`, `systematic-debugging`, `code-reviewer`, `design-reviewer`); no per-agent definitions or persona prompts. Fix a skill gap at its source (per `skills-lock.json`), not in a prompt — local copies are lost on reinstall. Missing one? `npx skills add`; author new only when none fits. (2026-09-15: per-agent formats aren't portable.)
- **One spawn per dependency chain, not per task.** Independent chains run in parallel.

### Working rules

- **Don't guess.** State assumptions on ambiguous constraints; verify tooling, versions, APIs and test tooling against docs or the lockfile; when unsure, say so and how to check. Run the thing before claiming done.
- Before destructive or scope-expanding changes (removing files from git, changing tracked configs, deleting branches), state the rationale and look for a smaller fix.
- Flag security issues outside the task scope; don't silently fix them.
- Worktrees go in `.worktrees/<name>`, scratch files in `.tmp/` (both gitignored) — not external dirs or `/tmp`, unless the harness provides its own scratchpad.
- This repo is self-sufficient: plugins, skills, MCP servers and rules are declared here (`npx skills add`, `.mcp.json`), not in user-global config.

### Communication

- Short by default, extremely concise when reporting; conclusions first. Don't sugarcoat risks. No cheerleading or filler.
- Disagree when you have reason and state your confidence. If unclear, ask one focused question.
