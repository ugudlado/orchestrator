# Plan: Split Engine / Workflow Config / Model Config

**Status:** proposal — 2026-07-12
**Goal:** three independently-owned layers: engine (code), workflow packs
(install/uninstall like skills), and model routing (one editable file, no
engine or workflow-config knowledge required).

---

## What already exists (don't rebuild)

| Layer           | Today                                                                                                                                                                                                        | Gap                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| Engine          | Installable wheel (`pip install .`), `orchestrator_next/` package, config bundled as package data                                                                                                            | None — done                                                              |
| Workflow config | Single root via `ORCHESTRATOR_CONFIG` (explicit, no fallback): `workflows/*.yaml` + `steps/<id>/` dirs                                                                                                       | No install/uninstall unit; all-or-nothing checkout                       |
| Model config    | `config/models.yaml` (tier → subprocess + model_id, tool invocation templates). Overrides: `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` (JSON env) > `ORCHESTRATOR_MODELS_CONFIG` (file) > config-root `models.yaml` | Editing means touching the workflow-config checkout, or knowing env vars |

The split is therefore **two small changes + one optional repo extraction**,
not a rewrite.

---

## Target architecture

```
┌─────────────────────────────────────────────┐
│ ~/.orchestrator/models.yaml     ← user edits │  Layer 3: model config
├─────────────────────────────────────────────┤
│ $ORCHESTRATOR_CONFIG/                        │
│   workflows/*.yaml   steps/<id>/             │  Layer 2: workflow packs
│   .packs.json  ← install manifest            │  (add/remove per pack)
├─────────────────────────────────────────────┤
│ orchestrator wheel (orchestrator_next/)      │  Layer 1: engine
│   bundles default config as fallback pack    │
└─────────────────────────────────────────────┘
```

Resolution stays **one config root** (no search-path layering — deliberate,
see Rejected below). Packs are installed _into_ the root, exactly how
`npx skills` copies skill dirs into `~/.claude/skills/`.

---

## Phase 1 — user-level `models.yaml` (switch models without touching anything)

Smallest change, biggest payoff. Make the engine consult a well-known user
path automatically:

```
Precedence (highest wins):
  1. ORCHESTRATOR_MODEL_ROUTE_OVERRIDES   (JSON env — per-run)
  2. ORCHESTRATOR_MODELS_CONFIG           (file env — per-run)
  3. ~/.orchestrator/models.yaml          (NEW — user default)
  4. <config_root>/models.yaml            (pack/bundled default)
```

- Change: `model_routes.py::resolve_field` gains one lookup between (2) and
  the routes_yaml file — `Path.home() / ".orchestrator" / "models.yaml"`.
  ~5 lines. Merge is per-tier (same `dict.update` pattern already used).
- `orchestrator doctor` reports which file each tier resolved from.
- New verb: `orchestrator models` — prints effective routing + source file
  per tier, so "why is it using cursor?" is answerable in one command.

Result: anyone switches opus→sonnet or claude→codex by editing one file in
their home dir. No env vars, no checkout, no engine knowledge.

## Phase 2 — workflow packs (install/uninstall like skills)

A **pack** is a directory (local path or git URL):

```
my-pack/
├── pack.yaml            # name, version, description
├── workflows/*.yaml     # schemas this pack provides
└── steps/<id>/          # contract.yaml + prompt.md|script.sh
```

New CLI verbs (one new module, `orchestrator_next/packs.py`):

```bash
orchestrator pack add <path|git-url>    # copy workflows/ + steps/ into config root
orchestrator pack remove <name>         # delete exactly the files it installed
orchestrator pack list                  # name, version, source, file count
```

Mechanics — deliberately dumb:

- **Install = copy** into `$ORCHESTRATOR_CONFIG`, recording every installed
  path in `<config_root>/.packs.json` under the pack name. Git URL → shallow
  clone to temp, copy, discard clone.
- **Uninstall = delete** the manifest-listed files. No dangling state.
- **Conflicts refuse**: a step id or workflow name already present (from
  another pack or the base config) → error listing collisions. No silent
  overwrite, no layering/shadowing rules to debug.
- **Upgrade = remove + add.** No versioned dependency resolution.
- Validation on install: run the existing `validate-workflow` + doctor
  contract checks against the incoming pack before copying anything.

Engine dispatch is **unchanged** — it still sees one flat config root. That
is the whole trick: the pack manager is a file-copier with a receipt, not a
resolution layer.

Follow-up: the bundled config gets a `pack.yaml` (`name: core`) so the
default workflows are just the pre-installed pack, listable and removable
like any other.

## Phase 3 — repo split (only when there's a second consumer)

Extract when a workflow pack needs its own release cadence or an external
user, not before:

| Repo                     | Contents                                                                                 | Artifact                              |
| ------------------------ | ---------------------------------------------------------------------------------------- | ------------------------------------- |
| `orchestrator` (engine)  | `orchestrator_next/`, `bin/`, engine tests, a minimal bootstrap pack (script steps only) | wheel on pip / `uv tool install`      |
| `orchestrator-workflows` | today's `config/workflows` + `config/steps` + workflow tests, as one or more packs       | git repo consumed by `pack add <url>` |
| user model config        | `~/.orchestrator/models.yaml`                                                            | dotfile — not a repo                  |

Setup for a new machine becomes:

```bash
uv tool install git+.../orchestrator
export ORCHESTRATOR_CONFIG=~/.orchestrator/config   # empty dir is valid
orchestrator pack add https://github.com/<you>/orchestrator-workflows
cp $(orchestrator config-path)/models.example.yaml ~/.orchestrator/models.yaml
```

Migration order inside this phase: move `config/` history via
`git filter-repo`, keep `pricing.yaml` with the engine (decided — pricing is
an engine metric concern, referenced by `record`/`pricing.py`, not workflow
content),
CI in the workflows repo runs `orchestrator validate-workflow` against the
released engine.

---

## Rejected alternatives

- **Search-path / layered config roots** (`ORCHESTRATOR_CONFIG` as
  colon-separated list, packs shadowing each other): flexible, but every
  "which contract actually ran?" question becomes a resolution-order debug
  session. Copy-with-manifest gives one flat truth on disk. Revisit only if
  in-place pack editing becomes a real workflow.
- **Real package manager** (registry, semver ranges, lockfile): no second
  consumer exists. Git URLs + `pack.yaml` version string cover it.
- **Splitting repos first** (Phase 3 before 1–2): repo boundaries without the
  pack mechanics just adds submodule/env pain. The packs + user models.yaml
  work is identical whether it's one repo or three — do it in-repo, extract
  later. (Consistent with the earlier "two-repo split deferred" decision.)
- **models.yaml per pack**: model routing is a _user/machine_ concern (which
  binaries are installed, whose API keys), not a workflow concern. One user
  file, engine-wide.

## Risks

- **Pack `steps/lib/` sharing**: several steps source `config/steps/lib/`.
  A pack depending on core's lib couples packs silently. Rule: a pack ships
  its own lib or depends only on `core`. Enforce in install validation later
  if it actually bites.
- **Manifest drift**: user hand-edits a pack-installed file, then
  `pack remove` deletes it. Acceptable — same behavior as skills; the pack
  source is the backup.
- **Bundled-config wheel staleness** after Phase 3: the engine's bootstrap
  pack must stay minimal (script steps only) or it re-grows the coupling.

## Sequencing & effort

| Phase                                        | Effort              | Unblocks                                     |
| -------------------------------------------- | ------------------- | -------------------------------------------- |
| 1 — user models.yaml + `orchestrator models` | ~½ day              | "anyone switches models by editing one file" |
| 2 — `pack add/remove/list` + manifest        | ~1–2 days           | install/uninstall workflow configs           |
| 3 — repo extraction                          | ~1 day, when needed | independent release cadence                  |

Phases 1 and 2 are independent and can ship as separate tickets.
