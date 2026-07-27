# Plan: Split into Engine CLI / Config Packs / Agent Config

**Status:** revised 2026-07-15 (v2 — supersedes the 2026-07-12 proposal; Phase 1 of v1 shipped as ORC-118)
**Goal:** three independently-updatable layers:

1. **Engine CLI** — agnostic of models and workflow content. Knows only conventions
   (directory layout, step protocol) and enforces success/failure semantics.
2. **Config packs** — workflow schemas + step directories that follow the convention.
   A pack that violates the convention still fails _safely_: the engine records a
   failed/blocked step, never a hang or silent pass.
3. **Agent config** — the user/machine-owned mapping that binds steps to models:
   alias → subprocess+model_id routing, per-step alias overrides, and subprocess
   invocation templates.

**Backlog:** ORC-118 (user-level model config) — **done, merged**. ORC-119
(workflow packs) — open, covered by Phase P below. Phases A and D need new tickets.

---

## 1. Where we actually are (verified against the tree, 2026-07-15)

| Concern                                          | State                            | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------ | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Engine installable, config-root explicit         | ✅ done                          | `pip install .` wheel bundles `config/`; `ORCHESTRATOR_CONFIG` explicit-only, no cwd fallback (`orchestrator_next/paths.py:35`, `ConfigRootError`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Model alias routing, layered                     | ✅ done (ORC-118)                | `models:` block resolved through 4 layers: `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` (JSON env) > `ORCHESTRATOR_MODELS_CONFIG` (file env) > `~/.orchestrator/models.yaml` > config-root/repo `models.yaml` (`orchestrator_next/model_routes.py:36-52`). `orchestrator models` prints effective routing + per-field source (`models_verb.py`). Repo-level candidates checked first: `<repo>/.orchestrator/config/models.yaml`, `<repo>/config/models.yaml` (`run_loop.py:750-760`).                                                                                                                                                                                                                                                                                        |
| Step success/failure protocol                    | ✅ exists (one narrow exception) | Exit codes (ORC-45): script step exit 0/nonzero; agent step must emit a `COMPLETION:` block — malformed parse or nonzero tool exit both become a retryable `failed` payload, never an abort (`run_loop.py::run_agent_step`, LOCKED policy). This is exactly the "even a non-conforming pack must land on success or failure" guarantee — it already holds. Known exception: a `state_mutating` script step is recorded `completed` _before_ it runs (`run_loop.py:340-345`), so a nonzero exit there leaves a `completed` record and an aborted run (exit 3) rather than a `failed` record. All current such steps are deterministic teardown scripts; the convention doc (Phase A) documents this so pack authors don't put fallible logic behind `state_mutating`. |
| Step→model binding                               | ⚠️ pack-owned                    | `contract.yaml` hardcodes `model: opus\|sonnet\|composer` per step. The _user's machine_ can remap what an alias routes to, but cannot re-tier a step (e.g. run `implement` on sonnet) without editing the pack.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Subprocess invocation templates (`tools:` block) | ⚠️ not layered                   | `_resolve_tool_template` (`run_loop.py:148`) reads the `tools:` block **only from the single resolved models.yaml file** — not through the layer chain. A user adding a new subprocess in `~/.orchestrator/models.yaml` gets the `models:` entry resolved but not its `tools:` template.                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Pack lifecycle (install/uninstall)               | ❌ missing                       | No `packs.py`; config root is an all-or-nothing checkout. ORC-119.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Convention written down                          | ❌ missing                       | The layout + protocol a pack must follow lives in tribal knowledge / CLAUDE.md fragments, not a doc a pack author can target.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `pricing.yaml`                                   | ⚠️ pack territory                | Engine metric concern (consumed by `pricing.py`/`record`), but resolved from the config root — a slim workflow-only pack root silently loses cost accounting (stderr warn, cost 0).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

The split is therefore **three medium changes + one deferred repo extraction**, not a rewrite.

## 2. Target architecture

```
┌──────────────────────────────────────────────────────┐
│ AGENT CONFIG (user/machine owned)                     │
│  ~/.orchestrator/models.yaml                          │
│    models:  alias → route | [fallback chain]   (new)  │
│    tools:   subprocess → invocation template   (new)  │
│  bootstrap: `orchestrator models init`         (new)  │
│  + env overrides for per-run tweaks (already exist)   │
├──────────────────────────────────────────────────────┤
│ CONFIG PACKS (installed into one flat root)           │
│  $ORCHESTRATOR_CONFIG/                                │
│    workflows/*.yaml   steps/<id>/                     │
│    .packs.json  ← install receipts                    │
│  each pack: pack.yaml + workflows/ + steps/           │
├──────────────────────────────────────────────────────┤
│ ENGINE CLI (wheel)                                    │
│  orchestrator_next/ — dispatch, state, record,        │
│  pricing, doctor, pack manager, models verb           │
│  bundles: default pack ("core") + models.example.yaml │
│  + pricing.yaml fallback                              │
└──────────────────────────────────────────────────────┘
```

Design invariants (unchanged from v1, still correct):

- **One flat config root.** Packs are _copied in_ with a receipt, like
  `npx skills` → `~/.claude/skills/`. No search-path layering, no shadowing.
- **Engine dispatch never learns about packs.** The pack manager is a
  file-copier with a manifest; dispatch keeps seeing `workflows/` + `steps/`.
- **Model choice is a machine concern, alias vocabulary is a shared concern.**
  Packs speak in aliases (`opus`, `sonnet`, `composer` — capability tiers);
  agent config decides what an alias means _here_. Re-tiering a step =
  redefining its alias on this machine, not a second step-keyed mapping.
- **No agent-role indirection.** Commit 5338fe0 deliberately replaced
  agent-name routing with model-tier routing; we are not reintroducing a
  role → model layer. Per-step alias override is the flat version of the
  same power.

---

## 3. Phase A — write the pack convention down (`docs/pack-convention.md`)

The contract already exists in code; a pack can't be authored independently
until it's documented. One doc, versioned with a single integer the engine
checks. Contents:

1. **Layout**: `pack.yaml` (name, version, description, `protocol: 1`),
   `workflows/*.yaml`, `steps/<id>/contract.yaml` + payload
   (`prompt.md` for agent steps, `script.sh` for script steps), optional
   `steps/lib/` for shared shell helpers.
2. **contract.yaml keys** (the minimal shape that dispatch reads):
   `id`, `version`, and either `model: <alias>` (agent step — **required**;
   there is no engine default, see Rejected) or `run: script.sh`
   (script step — the parser dispatches on `run:` alone, `parser.py:136`;
   the `kind: script` key seen in existing contracts is decorative and
   ignored). Optional: `state_mutating`, `default_outputs`. Anything else
   is ignored by the engine.
3. **Step protocol** (what the engine guarantees / requires):
   - Script steps: exit 0 = success, nonzero = failure (retry per routing);
     env provided (`REPO_ROOT`, `CHANGE_ID`, `STATE_YAML_PATH`, …, per
     `step_env.py`); the **last line of stdout must be a JSON object** —
     its keys become step outputs (`run_loop.py::_parse_stdout_outputs`).
     Caveat: `state_mutating` steps are recorded `completed` before they
     run, so a nonzero exit there aborts the run (exit 3) instead of
     recording `failed` — don't put fallible logic behind `state_mutating`.
   - Agent steps: prompt is assembled from `prompt.md` + step context; the
     agent must end with a `COMPLETION:` YAML block; malformed/missing
     block or nonzero subprocess exit → retryable `failed`. A pack author
     cannot hang the engine by breaking the format.
   - Exit codes surfaced by `orchestrator run`: 1 complete, 2 blocked, 3 error.
4. **Aliases**: packs must use alias names for `model:`, never concrete
   model ids. The engine refuses dispatch when an alias has no route
   (`run_loop.py:254` already errors) — that error message becomes the
   documented behavior.
5. **Protocol versioning**: `pack.yaml: protocol: 1`. `pack add` (Phase P)
   refuses a pack whose protocol the engine doesn't support. Bump only on a
   breaking change to (2) or (3).

Effort: ~½ day. No code except: `validate-workflow`/`doctor` reference the doc
in error messages. New backlog ticket.

## 4. Phase P — pack manager (ORC-119, mechanics unchanged from v1)

New module `orchestrator_next/packs.py` + CLI verbs:

```bash
orchestrator pack add <path|git-url>   # validate, then copy workflows/+steps/ into config root
orchestrator pack remove <name>        # delete exactly the receipt-listed files
orchestrator pack list                 # name, version, protocol, source, file count
```

Deliberately dumb mechanics (LOCKED from v1 review):

- **Install = copy** into `$ORCHESTRATOR_CONFIG`; every installed path recorded
  in `<config_root>/.packs.json` under the pack name. Git URL → shallow clone
  to temp dir, copy, discard.
- **Validate before copy**: `pack.yaml` parses, `protocol` supported,
  `validate-workflow` passes on each schema, each step dir has a parseable
  contract with `id` matching its dir name, every `model:` value is an alias
  **string** (not a concrete id — lint: warn if it looks like `claude-*`).
- **Conflicts refuse**: step id or workflow name already present → error
  listing collisions. No overwrite, no shadowing. Upgrade = `remove` + `add`.
- **`steps/lib/` rule**: a pack may ship its own `lib/`; depending on another
  pack's lib (including core's) is undocumented and unsupported. Enforce
  later only if it bites.
- **Pack ops require an untracked config root.** In the dev checkout,
  `ORCHESTRATOR_CONFIG` points at the git-tracked `config/` dir; `pack
add/remove` writing `.packs.json` and copying/deleting files there would
  mutate the working tree (and `pack remove core` would delete tracked
  files). Rule: `pack add/remove` refuse to run when the config root is
  inside a git work tree with tracked files at that path (cheap check:
  `git -C <root> ls-files --error-unmatch` on the root or `.packs.json`
  parent) — error tells the user to use a dedicated root, e.g.
  `~/.orchestrator/config`. The checkout's own `config/` stays read-only
  for pack ops; `pack list` still works anywhere.
- Bundled config gets `pack.yaml` (`name: core, protocol: 1`) so the default
  workflows become the pre-installed pack — listable, and installable into a
  user config root via `pack add $(orchestrator config-path)`.

Effort: ~1–2 days. Tests: tmp config root fixtures — add/list/remove
round-trip, collision refusal, invalid-pack refusal, git-URL path mocked to a
local dir.

## 5. Phase D — complete the agent-config layer

Four engine changes. The step→model binding stays in the contract
(`model: <alias>`, required by convention — the per-step `steps:` override
block from the v2 draft was dropped on 2026-07-15, see Rejected). "Define
aliases based on this machine" is the whole job of this phase: detect once
at init (D2), tolerate binary absence at runtime (D3), always show your work
(verb + doctor + logs).

**D1 — layer the `tools:` block.** Move `tools:` lookup into
`model_routes.py` using the existing `_layer_chain` (same precedence as
`models:`; same merge rule as D3 — the highest layer that names a tool owns
that tool's entire entry, no cross-layer field merge). `_resolve_tool_template`
(`run_loop.py:148`) delegates to it. Result: a user can route an alias to a
new subprocess _and_ define how to invoke it, entirely in
`~/.orchestrator/models.yaml`. ~25 lines + tests.

**D2 — `orchestrator models init` (machine bootstrap).** Scans PATH
(`shutil.which`) for the binaries named by the layered `tools:` blocks —
seeded from the engine-bundled `models.example.yaml` when no layer defines
`tools:` yet, which is the normal fresh-machine case (`claude`,
`cursor-agent`, `codex`, `pi`) — then writes
`~/.orchestrator/models.yaml` binding each core alias to the best available
route — e.g. `composer` → cursor when `cursor-agent` is present, else a
chain with claude next (see D3) — and prints each choice with the reason.
Refuses to overwrite an existing file without `--force`. Detection happens
once, at init; the output is a plain file the user reads and edits, not
runtime magic. Doctor's existing subprocess-on-PATH check (RULE 3,
`doctor.py:156`) catches later drift. ~40 lines + tests.

**D3 — runtime fallback chains.** An alias in any `models:` layer may be a
_list_ of candidate routes instead of a single route:

```yaml
models:
  composer:
    - { subprocess: cursor, model_id: composer-2.5 }
    - { subprocess: claude, model_id: claude-sonnet-4-6 }
```

Resolution (in `model_routes.py`): pick the first candidate whose
subprocess's `binary` (per layered `tools:`, D1) is on PATH; no candidate
available → the existing no-route error (exit 4, `run_loop.py:256-257`).

**Layer-merge rule (LOCKED): the highest-precedence layer that names an
alias wins it wholesale — list or scalar — and lower layers are ignored for
that alias.** No element-wise list merging, and no cross-layer _field_
merging either. This is a semantic change to today's resolver, not an
addition: `resolve_field` currently accumulates fields across layers with
`entry.update(...)` (`model_routes.py:49-51`) — which, fed a list, silently
unpacks a 2-key candidate into garbage (`dict.update([{a:1,b:2}])` →
`{a: b}`) and crashes on any other arity; `resolve_all_with_source`'s
`if fld in tier_data` (`model_routes.py:69-74`) is also dict-shaped and
would render every chained alias as an empty route. **Both functions get
rewritten** to branch scalar-vs-list per alias with wholesale-wins
semantics. Back-compat delta, stated explicitly: a scalar alias defined in
one layer still works unchanged, but _partial_ cross-layer field-merge (home
file sets only `model_id`, inherits `subprocess` from config root) stops
working — the higher layer must state the full route. That partial merge is
undocumented, untested, and almost certainly unused; wholesale-wins is the
same "one owner per alias, debuggable" logic that rejected element-merging.

Guard rails, because silent tier degradation is the known failure mode of
fallbacks:

- the dispatch log line (already prints `model=<model_id>`) additionally
  prints `(fallback #N for <alias>)` when a non-first candidate was chosen;
- recorded `usage`/pricing always see the **concrete** model_id, never the
  alias: cursor/codex adapters take it from `route_model`
  (`usage_adapters.py:96`, `:143`); claude/pi report their own concrete
  model in their JSON output (`modelUsage`) and ignore `route_model` — so
  cost accounting is exact regardless of which candidate ran;
- `orchestrator models` prints the full chain per alias and marks the
  candidate active on this machine;
- `doctor` WARNs whenever any alias is currently resolving to a fallback
  (first candidate unavailable).

~120 lines (resolver rewrite + verb rendering) + tests: chain resolution,
wholesale-wins across layers (list-over-scalar, scalar-over-list,
list-over-list), back-compat single-layer scalar, exit-4 when chain
exhausted, and a regression test pinning the no-partial-field-merge change.

**D4 — pricing + doctor follow the split.**

- `pricing.py` falls back to `bundled_config_root()/pricing.yaml` when the
  config root has none — cost accounting survives a workflow-only pack root.
  (`pricing.yaml` stays engine-owned; decided in v1.)
- `doctor` RULE for `models.yaml` presence loosens: WARN only if **no layer**
  (user home, env, config root, repo) yields a `models:` block; report which
  layers were checked. `doctor` also validates that every alias referenced by
  installed contracts resolves to at least one available route — that check
  is what makes "update packs and agent config independently" safe in practice.

Effort: ~2 days total (the D3 resolver rewrite is the bulk). New backlog ticket (or fold D1–D4 into one ticket
"agent-config completion"). Order inside the phase: D1 → D3 → D2 (init
writes chains, so chain support must exist first); D4 anytime.

## 6. Phase R — repo extraction (unchanged, still deferred)

Trigger: a second consumer or a genuinely different release cadence — not before.

| Repo                     | Contents                                                                                                                      | Artifact                              |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `orchestrator` (engine)  | `orchestrator_next/`, `bin/`, engine tests, minimal bootstrap pack (script steps only), `pricing.yaml`, `models.example.yaml` | wheel (`uv tool install`)             |
| `orchestrator-workflows` | today's `config/workflows` + `config/steps` + workflow tests, as pack(s)                                                      | git repo consumed by `pack add <url>` |
| agent config             | `~/.orchestrator/models.yaml`                                                                                                 | dotfile — not a repo                  |

New-machine setup after extraction:

```bash
uv tool install git+.../orchestrator
export ORCHESTRATOR_CONFIG=~/.orchestrator/config    # empty dir is valid
orchestrator pack add https://github.com/<you>/orchestrator-workflows
orchestrator models init    # detect installed binaries, write ~/.orchestrator/models.yaml
```

Migration mechanics: `git filter-repo` for `config/` history; workflows-repo CI
runs `orchestrator validate-workflow` + `pack add --dry-run` against the
released engine wheel.

## 7. Rejected alternatives (carried from v1 + new)

- **Layered/search-path config roots** — every "which contract ran?" becomes a
  resolution-order debug session. One flat root + receipts.
- **Agent-role indirection** (contract says `agent: reviewer`, config maps
  role → model): reverts commit 5338fe0's deliberate simplification;
  machine-level alias redefinition gives the same control with one fewer
  vocabulary.
- **Real package manager** (registry, semver ranges, lockfile): no second
  consumer. Git URL + `pack.yaml` version string.
- **models.yaml per pack**: model routing is a machine concern (installed
  binaries, API keys), not workflow content. One user file, engine-wide.
- **Engine `default_model:` so contracts may omit `model:`** (v2 draft
  "D3", cut on review): dispatch gates agent steps on the contract's
  `model:` being present (`dispatch.py:183` sets `action["model"]`;
  `run_loop.py:483` gates on it — a `None` model falls into the no-op branch
  at `run_loop.py:505-506` and the loop spins). Making omission work means
  resolving defaults _at dispatch time_, teaching the pack-unaware dispatcher
  about agent config. Not worth it: requiring `model: <alias>` in every agent
  contract is a one-line convention rule.
- **Per-step `steps:` override block** (v2 draft "D2", dropped 2026-07-15
  by owner decision): a second mapping keyed by step id adds a namespace
  that can orphan on step renames and splits "what tier does this step run
  at" across two places. Re-tiering is done by redefining the alias for
  this machine (or editing the pack); the contract stays the single place a
  step's tier is stated. Revisit only if per-step control (not per-tier)
  becomes a real recurring need.
- **Splitting repos first**: boundaries without mechanics = submodule/env
  pain. All of A/P/D work identically in-repo; extract later.

## 8. Risks

- **Alias vocabulary drift**: a pack invents alias `gpt5-turbo` that no agent
  config maps. Caught at dispatch (exit 4) and by the Phase D4 doctor check;
  `pack add` lint warns on non-core aliases. Acceptable — the alias set is
  small and documented in the convention doc.
- **Manifest drift**: user hand-edits a pack-installed file, `pack remove`
  deletes it. Same behavior as skills; pack source is the backup. Documented,
  not prevented.
- **Protocol version discipline**: the `protocol` integer only helps if bumps
  actually happen. Mitigation: the convention doc lists exactly which changes
  are breaking; review checklist item in the engine repo.
- **Bundled "core" pack staleness after Phase R**: keep the engine's bootstrap
  pack minimal (script steps only) or coupling regrows.
- **Fallback chains degrade tier silently**: `composer` falling back to a
  sonnet route changes output quality with no hard failure. Mitigations are
  built into D3 (per-dispatch fallback log line, `orchestrator models`
  active-candidate marker, doctor WARN while any alias runs on fallback,
  concrete model_id in metrics/pricing). The residual risk — work quietly
  running a tier down until someone reads the WARN — is accepted; it's the
  point of chains. If it bites, the escape hatch is removing the chain
  (single-route aliases fail loudly, exit 4).

## 9. Sequencing, tickets, effort

| Phase                               | Ticket        | Effort    | Depends on          | Unblocks                                                          |
| ----------------------------------- | ------------- | --------- | ------------------- | ----------------------------------------------------------------- |
| A — convention doc                  | new           | ~½ day    | —                   | independent pack authoring; P's validation checklist              |
| P — pack add/remove/list            | ORC-119       | ~1–2 days | A (checklist)       | install/uninstall workflow configs                                |
| D — agent-config completion (D1–D4) | new           | ~2 days   | — (parallel to A/P) | machine-local alias definitions: init bootstrap + fallback chains |
| R — repo extraction                 | new, deferred | ~1 day    | A + P               | independent release cadence                                       |

A and D are independent and can land in either order; P consumes A's
checklist. Nothing here breaks an existing setup: current checkouts keep
resolving `config/models.yaml` as the lowest layer, contracts keep their
`model:` aliases, and all existing env overrides keep working.
