# Distributing the orchestrator CLI & workflow configs

How to make the engine + workflows usable by other people and other repos, today, plus the shortlist of improvements that would make it painless.

## What already works

Two repos: [`orchestrator`](https://github.com/ugudlado/orchestrator) (engine, installs as a wheel) and [`prompt-packs`](https://github.com/ugudlado/prompt-packs) (workflows/steps/skills + base role packs + their tests) — one `git clone` per machine, updated with `git pull`, exactly like the CLI install itself. Engine-owned data (pricing rates, models seed) ships inside the wheel at `orchestrator_next/data/`.

## Steps: another person / another repo, today

```bash
# 1. Install the CLI (uv keeps it isolated; pipx also works)
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. Pull workflow-config into the repo (steps own SKILL.md + scenarios)
orchestrator config pull https://github.com/ugudlado/workflow-config.git mypack
# or: orchestrator config pull /path/to/workflow-config mypack --skills

# Base role prompts for extends: — once per machine
git clone --depth 1 https://github.com/ugudlado/prompt-packs.git ~/.orchestrator/pack

# 3. Sanity check (run inside the target repo — no per-repo setup file needed;
#    conventions live in CLAUDE.md/AGENTS.md/README, ticketing is env-driven)
orchestrator doctor

# 4. Run (layout: .orchestrator/mypack/workflows/feature.yaml)
orchestrator feature TICKET-1
# multiple packs: orchestrator mypack/feature TICKET-1
```

Config resolution (first hit wins, repo→global): `ORCHESTRATOR_CONFIG` env → exactly one `<repo>/.orchestrator/<pack>/` (legacy flat `.orchestrator/` / `.orchestrator/config/` still accepted) → dev-checkout `config/` → `~/.orchestrator/pack/config` (downloaded pack). Multiple packs under `.orchestrator/` with no env → hard error: pass `orchestrator <pack>/<workflow> <id>` or set `ORCHESTRATOR_CONFIG`. No hit → hard error with the `config pull` one-liner. `doctor` reports which source resolved. Each pulled pack carries `workflows/`, `steps/` (agent steps include `SKILL.md`), and `models.yaml`.

Per-repo customization without forking the engine:

- **Prompts**: agent steps carry their charter as `prompt: SKILL.md` inside the step dir. `orchestrator config pull … --skills` optionally symlinks those into `<repo>/skills/<name>/` for IDE discovery. Absolute prompt paths are rejected.
- **Quality gates & verify commands**: gate thresholds are step-owned (vendor the pack to change them); review/QA steps discover the repo's test/lint commands from its own docs and manifests. Repos should also carry commit-time verification (pre-commit/husky/biome) — doctor WARNs when none is present, so breakage is caught at commit, not only at the QA gate.
- **Ticketing**: env-driven — `BACKLOG_URL`+`BACKLOG_TOKEN` present means backlog; unset means ticket steps skip cleanly. The engine and doctor have zero ticketing logic; workflow scripts own it, so new backends are a script change.
- **Headless/CI**: `ORCHESTRATOR_HEADLESS=1` + `ORCHESTRATOR_NOTIFY_CMD` — state auto-commits to the branch, blocks notify via any shell command.

Upgrades: `uv tool upgrade orchestrator` (or reinstall from git). Config ships with the wheel, so engine+config always match.

## Model routing

No model setup is required to start: step contracts reference aliases (`model: sonnet|opus|haiku|composer`), and the pack's `config/models.yaml` resolves each alias to `{model_id, tool}` plus a `tools:` invocation template per agent CLI (`claude`, `codex`, `cursor-agent`, `pi`). Overrides follow the same repo→global rule as everything else — highest wins, wholesale per alias:

1. per-run CLI override (`orchestrator run <id> model.<alias>.<field>=…`)
2. repo/explicit config root `models.yaml` (vendored pack or `ORCHESTRATOR_CONFIG`)
3. `~/.orchestrator/models.yaml` (machine-level)
4. pack/checkout `config/models.yaml` (floor)

Example — route everything through claude on a machine without cursor-agent:

```yaml
# ~/.orchestrator/models.yaml
models:
  composer: { model_id: claude-sonnet-4-6, tool: claude }
```

Per-run: `orchestrator run <id> model.composer.model_id=<id>`.

The real prerequisite is the **agent CLI binaries themselves**: pack defaults need `claude` on PATH, and the implement step routes to `cursor-agent`. That's the one model-related thing an installer must have or override.

## Friction points & improvements (ranked)

1. **Zero per-repo setup** — DONE by deletion: `spec/project.yaml` and `orchestrator init` are gone. Any git repo is runnable; conventions come from the repo's own docs, thresholds are step-owned, ticketing is env-driven.
2. **No env var, no bundle** — DONE: the wheel ships the engine only; the pack is downloaded like the CLI, resolution is repo→global, and the packless error message contains the exact fix.
3. **Tagged releases** — cut `v0.x` git tags so installs are pinnable (`uv tool install git+...@v0.3`) and upgrades are deliberate. No PyPI needed until there's an external audience; git tags are free.
4. **`doctor` as the onboarding contract** — make `orchestrator doctor` verify exactly the setup steps above (config resolvable, git repo, commit-time verification present). Every future "setup didn't work" report becomes a missing doctor check.
5. **Doctor: verify agent CLI binaries** — resolve every alias used by the installed contracts through the model-routing layers and check each `tool` binary is on PATH. Today a missing `cursor-agent` fails mid-workflow at the implement step instead of at `doctor` time; the fix message should show the `~/.orchestrator/models.yaml` reroute example.
6. **README quickstart** — the four-command block above belongs at the top of the repo README for people who don't have this doc.
7. **Per-repo workflow overrides** — pull another pack into `<repo>/.orchestrator/<pack>/` (`orchestrator config pull … <pack>`). A repo can hold multiple packs side by side; disambiguate runs with `<pack>/<workflow>`. Whole-pack only, deliberately — per-step overlay merging (the old config-repo-split plan) stays dead until a real consumer needs partial overrides.

## Orca integration

Orca already pulls tickets from the backlog (`runtime-backlog-client.ts`). The cheapest useful integration keeps the boundary at the CLI — orca shells out, orchestrator stays UI-agnostic (matches the agent-agnostic rule):

1. **Run from a ticket**: a "Run workflow" action on an orca ticket spawns `orchestrator run <id> --schema <s>` in a PTY (orca already has terminal panes) with `ORCHESTRATOR_CONFIG` set. Ticket state changes flow through the backlog both already talk to — no new protocol.
2. **Progress without polling**: `ORCHESTRATOR_HEADLESS=1` + `ORCHESTRATOR_NOTIFY_CMD` pointed at a small curl to an orca endpoint gives orca block/complete events as JSON. Both hooks exist today.
3. **Later, if needed**: read `.orchestrator/<slug>/*_state.yaml` for a step-by-step progress view. It's YAML on disk; no API required.

Skipped: an embedded/library integration (importing `orchestrator_next` into orca's runtime) — crosses the process boundary for no benefit while the CLI + notify hook covers launch, progress, and completion.
