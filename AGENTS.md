# AGENTS.md

Guidance for agents working in this repo and for **using the orchestrator CLI**
in any consumer repo.

---

## What this repo is

**orchestrator** — config-driven, LLM-agnostic workflow engine. The wheel ships
the engine only. Workflows, steps, and step-owned charters come from a pack
pulled into the **consumer** repo under `.orchestrator/<pack>/`.

---

## Consumer setup (any git repo)

```bash
# 1. Install the CLI
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. Pull a workflow pack (git URL or local path)
orchestrator config pull https://github.com/ugudlado/workflows.git workflows
# optional IDE export of step charters:
# orchestrator config pull … mypack --skills

# Base role prompts for skill frontmatter `extends:` — once per machine
git clone --depth 1 https://github.com/ugudlado/prompt-packs.git ~/.orchestrator/pack

# 3. Verify
orchestrator doctor

# 4. Run
orchestrator feature TICKET-1
# if the name exists in more than one pack:
orchestrator mypack/feature TICKET-1
```

### Layout after `config pull`

```text
<repo>/
  .orchestrator/
    mypack/                         # pack name = 2nd arg (or source basename)
      workflows/feature.yaml
      steps/<id>/
        contract.yaml               # prompt: SKILL.md  |  run: script.sh
        SKILL.md                    # agent steps: charter lives here
        metrics.md
        scenarios/{train,dev,holdout}.jsonl
      lib/
      models.yaml
      config-lock.yaml
    <ticket-slug>/                  # runtime state (not a pack)
      *_mypack_feature_state.yaml
  skills/                           # only if you passed --skills (symlinks → steps)
```

**Only pack convention:** `.orchestrator/<pack>/workflows/<workflow>.yaml`
(e.g. `.orchestrator/workflows/workflows/feature.yaml` when the pack is named
`workflows`). Do not put workflow YAML files directly under `.orchestrator/`.

### Naming workflows on the CLI

| Situation                            | Command                                |
| ------------------------------------ | -------------------------------------- |
| `feature` exists in exactly one pack | `orchestrator feature TICKET-1`        |
| Same name in `mypack` and `mypack1`  | `orchestrator mypack/feature TICKET-1` |
| Qualify graph the same way           | `orchestrator graph mypack/feature`    |

State stores `config_pack` so `next` / `done` keep using that pack.

### Config resolution (engine)

First hit wins:

1. `ORCHESTRATOR_CONFIG` (explicit pack root)
2. Exactly one `.orchestrator/<pack>/` with `workflows/`
3. Multiple packs → must use `<pack>/<workflow>` (or set `ORCHESTRATOR_CONFIG`)
4. Engine checkout `config/` (dev)
5. `~/.orchestrator/pack/config` (legacy global)

`orchestrator config-path` prints the active root.

Optional: `BACKLOG_URL` / `BACKLOG_TOKEN` / `BACKLOG_PROJECT` for ticket sync
(unset → ticket steps no-op). Cloud/headless: see `DRIVE.md` and
`docs/cloud-environment.md`.

---

## This repo (engine) layout

```text
orchestrator/
├── bin/orchestrator
├── orchestrator_next/          # Python package (CLI, dispatch, pack pull)
├── docs/                       # distribution.md, cloud-environment.md, …
├── DRIVE.md                    # Claude Code cloud driver loop
├── AGENTS.md                   # this file (CLAUDE.md → symlink)
└── (optional) config/          # present only in some checkouts / tests
```

Workflows that ships for real consumers live in
[workflows](https://github.com/ugudlado/workflows), not in this
wheel.

### Dev CLI

```bash
uv sync --extra dev   # installs engine + dev/redis extras into .venv
python -m orchestrator_next --help
pytest orchestrator_next/tests/ -q
```

### Core verbs

| Command                                                  | Description                                  |
| -------------------------------------------------------- | -------------------------------------------- |
| `orchestrator config pull <git\|path> [pack] [--skills]` | Install pack under `.orchestrator/<pack>/`   |
| `orchestrator <workflow> <ticket>`                       | Run workflow (`feature` or `mypack/feature`) |
| `orchestrator run <ticket> --schema <ref>`               | Same, explicit schema ref                    |
| `orchestrator next <state.yaml>`                         | Dispatch next step                           |
| `orchestrator done <state.yaml>`                         | Record step result (JSON on stdin)           |
| `orchestrator graph <ref>`                               | Mermaid DAG                                  |
| `orchestrator doctor`                                    | Health check                                 |
| `orchestrator complete <ticket>`                         | Teardown workflow                            |

### Headless / cloud

- `ORCHESTRATOR_HEADLESS=1` or `CLAUDE_CODE_REMOTE=true` → state auto-commit;
  push on block/abort.
- Cloud Slack/Claude sessions: follow `DRIVE.md` (`--seed-only` + `next`/`done`).

### Exit codes (`next` / drive protocol)

| Code                         | Meaning                                       |
| ---------------------------- | --------------------------------------------- |
| `0` + JSON with model        | Agent step — execute instruction, then `done` |
| `0` + script JSON / no agent | Script already ran — loop                     |
| `1`                          | Workflow complete                             |
| `2`                          | Blocked (signoff)                             |
| `3`                          | Error                                         |

### Rules for agents in this codebase

1. **evidence-based** — verify before claiming done
2. **minimal-diffs** — scope to the task
3. **agent-agnostic** — no hard-coded LLM vendor in schemas/steps
4. Prefer Python for YAML/state logic over new bash

### Prompt / learn loop

Agent steps use `prompt: SKILL.md` **inside the step dir**. Learn proposes
rows → `persist-learnings` appends to that step’s `scenarios/train.jsonl`.
Optional `--skills` only mirrors charters for IDE discovery.

More: `docs/distribution.md`, `README.md`.
