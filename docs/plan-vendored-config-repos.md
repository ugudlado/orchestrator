# Plan: Config Packs as Repos, Vendored + Committed into Consuming Repos

**Status:** draft 2026-07-15
**Builds on:** `docs/plan-config-repo-split.md` (Phases A/D/P shipped 2026-07-15) and
`docs/pack-convention.md` (protocol 1).

## Goal

1. Config packs live in **their own git repos** — installable via
   `orchestrator pack add <git-url>` (already works).
2. On install into a project, the pack is copied into the **consuming repo
   itself** and **committed** — so each repo's workflows/steps/models evolve
   with that repo over time.
3. The pack repo is a **seed, not a dependency**: you install it to start
   somewhere; after that the vendored copy is the truth for this repo.
   Re-installing from upstream is always possible but overwrites local
   evolution (documented, not merged).
4. Workflow schemas, step directories, and `models.yaml` live **together** in
   the repo's config dir.

## What already works (verified against the tree)

| Need                           | State                                                                                                     |
| ------------------------------ | --------------------------------------------------------------------------------------------------------- |
| Pack repo format               | ✅ `pack.yaml` + `workflows/` + `steps/` (protocol 1); the checkout's `config/` is already a valid pack   |
| Install from git URL           | ✅ `pack add <url>` shallow-clones, validates, copies, writes `.packs.json` receipt (`packs.py`)          |
| Per-repo model config          | ✅ `<repo>/.orchestrator/config/models.yaml` is already the top repo-level layer (`run_loop.py:750`)      |
| Repo-local **workflow** config | ❌ `config_root()` reads only `ORCHESTRATOR_CONFIG` / `ORCHESTRATOR_HOME` (`paths.py:35`) — no repo layer |
| Install into a tracked dir     | ❌ `pack add/remove` refuse git-tracked roots (`packs.py:241-262`) — the opposite of "commit it"          |

So this is **two small engine changes + one repo extraction**, not a new system.

## Design decisions

- **Vendored copy is the truth.** Once installed and committed, the repo's
  config evolves in place (edit prompts, tune workflows, adjust models.yaml)
  like any other file in the repo. No sync tooling, no upstream tracking, no
  three-way merge. The receipt records `source` + `commit` sha so you can
  always diff against upstream by hand.
- **Upgrade = overwrite, explicitly.** `pack add --force <url>` replaces the
  vendored files (git shows you exactly what changed — that's the merge tool).
  We do not build update machinery; git already is one.
- **One canonical repo location:** `<repo>/.orchestrator/config/`. It's where
  the models.yaml repo layer already looks, and `.orchestrator/<slug>/` state
  dirs are siblings. The dir is _committed_ (unlike state dirs, which stay
  gitignored — the existing `.gitignore` pattern must exempt `config/`).
- **Fallback chain for the config root** (first hit wins, no layering/merging
  between them — same flat-root invariant as the split plan):
  1. `ORCHESTRATOR_CONFIG` env (explicit override, unchanged)
  2. `<repo>/.orchestrator/config/` **if it contains `workflows/`** (new)
  3. hard error, same as today (bundled config still requires explicit opt-in)

## Phase V1 — repo-local config root (~½ day)

`paths.py::config_root()` grows an optional `repo_root` parameter; call sites
that know the repo (`run_loop.py`, dispatch entry) pass it. Resolution order
as above. `orchestrator doctor` and `orchestrator models` print which root won
and why.

Tests: env-set wins over repo dir; repo dir wins over nothing; repo dir
without `workflows/` is skipped; error message unchanged when neither exists.

## Phase V2 — vendored install (~½ day)

`orchestrator pack add <src> --into <repo>` (or auto: `--repo` flag already
exists on `run`):

- Target dir: `<repo>/.orchestrator/config/`. Created if missing.
- The tracked-root guard (`_assert_untracked_config_root`) is **bypassed for
  this mode only** — vendoring into a tracked path is the point. The guard
  stays for the plain `pack add` (protecting the dev checkout's `config/`).
- Copies `workflows/` + `steps/`, writes `.packs.json` receipt with
  `source` (URL/path) and `commit` (sha of the shallow clone's HEAD).
- Also copies the pack's `models.yaml` **if the pack ships one and the target
  has none** — never overwrites an existing repo models.yaml. (Model routing
  stays a machine/repo concern; a pack-shipped models.yaml is a starter, and
  the existing 4-layer resolution already lets `~/.orchestrator/models.yaml`
  override it per machine.)
- Prints the follow-up: `git add .orchestrator/config && git commit`.
  The engine does **not** commit for you.
- `pack remove --into <repo>` mirrors it (deletes receipt-listed files).

Tests: vendored add round-trip in a tmp git repo; guard still refuses plain
add on tracked root; models.yaml copied only when absent; `--force` overwrite.

## Phase V3 — extract the pack repo (~½ day, was "Phase R")

The trigger condition from the split plan ("a second consumer") is now met:
every consuming repo is a consumer.

- `git filter-repo` the checkout's `config/` history → new repo
  `orchestrator-workflows` (it already has `pack.yaml`, so it's a valid pack
  as-is).
- Engine keeps: bundled minimal bootstrap pack, `models.example.yaml`,
  `pricing.yaml` (engine-owned, per split plan D4).
- Pack repo CI: `orchestrator validate-workflow` on each schema against the
  released engine wheel.

New-repo setup end state:

```bash
uv tool install git+.../orchestrator
cd ~/code/myproject
orchestrator pack add https://github.com/<you>/orchestrator-workflows --into .
git add .orchestrator/config && git commit -m "seed orchestrator config"
orchestrator run PROJ-1 --schema feature   # config root auto-resolves from the repo
```

From then on the repo's config evolves in its own commits, reviewed like code.

## Tradeoffs (stated, accepted)

- **Divergence is the feature and the cost.** N repos → N slowly-diverging
  config copies. Improvements don't propagate automatically; you cherry-pick
  the good ones back to the pack repo when they generalize. If propagation
  pain ever dominates, the escape hatch is pointing `ORCHESTRATOR_CONFIG` at
  a shared root again — nothing here removes that mode.
- **Repo bloat:** ~21 step dirs + schemas ≈ small text; negligible.
- **Secret hygiene:** repo-committed `models.yaml` must hold aliases/routes
  only, never API keys (keys live in env / `~/.orchestrator/`). `pack add`
  already lints for concrete model ids; add the same warn-on-suspicious-keys
  check to the vendored copy path.

## Not doing (YAGNI)

- Upstream sync / update-merge tooling — git diff against the receipt's
  recorded sha covers it.
- Multi-pack layering inside a repo — one flat vendored root, same invariant
  as the split plan.
- Registry/semver — git URL + `pack.yaml` version string, unchanged.
- Auto-commit on install — the user reviews and commits; the engine printing
  the command is enough.

## Sequencing

| Phase | What                                  | Effort | Depends on           |
| ----- | ------------------------------------- | ------ | -------------------- |
| V1    | repo-local config root                | ~½ day | —                    |
| V2    | vendored `pack add --into`            | ~½ day | V1                   |
| V3    | extract `orchestrator-workflows` repo | ~½ day | V2 (proves the flow) |

V1+V2 are fully useful before V3 ships — `pack add $(orchestrator config-path)
--into <repo>` vendors the bundled pack today, so any repo can start
immediately without waiting for the extraction.
