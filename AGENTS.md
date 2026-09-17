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

No implicit fallback: the engine checkout's `config/` and
`~/.orchestrator/pack/config` were removed (plan phase 3.2) so a run can always
name the pulled, locked pack it came from.

`orchestrator config-path` prints the active root.
`orchestrator config update [pack] [--yes]` re-pulls the locked source and
diffs each step's contract (`version`, `kind`, `tools`, `side_effects`) before
anything is written. Remote pulls require an `[[allow]]` entry in
`~/.orchestrator/trust.toml` (`ORCHESTRATOR_TRUST_ALL=1` bypasses).

Optional: `BACKLOG_URL` / `BACKLOG_TOKEN` / `BACKLOG_PROJECT` for ticket sync
(unset → ticket steps no-op). Cloud/headless: see
`docs/cloud-environment.md` and `docs/protocol-v2.md`.

---

## This repo (engine) layout

```text
orchestrator/
├── orchestrator_next/          # Python package (CLI, dispatch, pack pull)
├── docs/                       # distribution.md, cloud-environment.md, …
├── AGENTS.md                   # this file (CLAUDE.md → symlink)
└── (optional) config/          # present only in some checkouts / tests
```

Workflows that ships for real consumers live in
[workflows](https://github.com/ugudlado/workflows), not in this
wheel.

### Dev CLI

```bash
uv sync --extra dev   # installs engine + dev extras into .venv
python -m orchestrator_next --help
pytest orchestrator_next/tests/ -q
```

### Core verbs

| Command                                                  | Description                                 |
| -------------------------------------------------------- | ------------------------------------------- |
| `orchestrator config pull <git\|path> [pack] [--skills]` | Install pack under `.orchestrator/<pack>/`  |
| `orchestrator start <recipe> <slug> --json`              | Seed a run; returns `run_id` and first step |
| `orchestrator step <run> --json`                         | Next step for the harness to execute        |
| `orchestrator done <run> <step> --out J --usage J`       | Record a judgment step's structured result  |
| `orchestrator approve <run> <token>`                     | Approve a gate and resume                   |
| `orchestrator status <run> --json`                       | Nodes, artifacts, gates, running cost       |
| `orchestrator run --headless <recipe> <slug>`            | Engine drives the model itself              |
| `orchestrator graph <ref>`                               | Mermaid DAG                                 |
| `orchestrator doctor`                                    | Health check                                |

### Headless / cloud

- `ORCHESTRATOR_HEADLESS=1` or `CLAUDE_CODE_REMOTE=true` → state auto-commit;
  push on block/abort.
- Cloud Slack/Claude sessions: `orchestrator run --headless <recipe> <slug>`
  (resume with `orchestrator headless <run>`); see `docs/cloud-environment.md`.

### `step` status enum

`step` always exits 0 and reports what happened in `status` (the v1 exit-code
protocol is gone — see `docs/protocol-v2.md` §3):

| Status      | Meaning                                              |
| ----------- | ---------------------------------------------------- |
| `ready`     | step is dispatchable now (`kind` says judgment/gate) |
| `done`      | run complete                                         |
| `blocked`   | waiting on a gate token                              |
| `needs_you` | needs a decision the engine can't make               |
| `error`     | run failed                                           |

### Rules for agents in this codebase

1. **evidence-based** — verify before claiming done
2. **minimal-diffs** — scope to the task
3. **agent-agnostic** — no hard-coded LLM vendor in schemas/steps
4. Prefer Python for YAML/state logic over new bash

### Prompt / learn loop

Agent steps use `prompt: SKILL.md` **inside the step dir**, and every judgment
contract must declare an `out:` block. Learn proposes
rows → `persist-learnings` appends to that step’s `scenarios/train.jsonl`.
Optional `--skills` only mirrors charters for IDE discovery.

More: `docs/distribution.md`, `README.md`.
