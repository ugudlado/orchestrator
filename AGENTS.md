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
