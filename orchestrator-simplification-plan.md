# Orchestrator Simplification Plan

**Repo:** `ugudlado/orchestrator` (engine) + `ugudlado/workflows` (pack)
**Goal:** One driver, structured I/O, engine-owned artifacts, DB-backed metrics, Claude Mod + Codex plugin as surfaces. Target 9,597 → ~3,500 lines (engine `.py` excl. tests).

---

## Prior decisions (Sept 2026)

- Plan was written against `main`; branch `feat/acp-server` is 25 commits ahead
  (9,597 engine lines, 540 tests). Work continues on a new branch cut from
  `feat/acp-server` HEAD, not from `main`.
- ACP transport (`acp_client.py`, `session_cli.py`, ACP arm of
  `run_loop.py:314-334`), buzz relay (`buzz_adapter.py`, verify-completion
  node, `ORCHESTRATOR_ROSTER`), and uncommitted `bridge.py` are **dead** —
  deleted in Phase 1. Reason: ACP is an editor-observation protocol; the CLI
  harness doesn't need it; buzz sits on top of ACP. Principle 1 (harness
  executes, engine never spawns) stands.
- **Keep** from the branch: `state_store.py` (SQLite default, Postgres via
  `ORCHESTRATOR_STATE_URL`, optimistic CAS `StateConflictError`),
  `run_store.py` (run_blobs/run_locks, one shared db),
  `dispatch.dispatch_batch` + `ORCHESTRATOR_MAX_PARALLEL`,
  `worktree_lock.py`, `record.apply_task_updates`, `_record_with_retry`,
  await_input routing, `--ticket-id`, reset-step fix.
- Redis was added then deleted (commit `0d818a5`) — settled, SQLite only.
- Prior docs: `docs/plan-acp-simplify.md`, `docs/plan-redis-state.md`,
  `docs/plan-learn-rules-and-step-evals.md`, `docs/pack-convention.md`.
  `plan-redis-state`'s "artifact layout is pack-owned, engine never
  prescribes" decision is **overruled** by this plan's principle 4 (engine
  owns run base path, named artifacts).

---

## Principles (decided)

1. **Harness executes, CLI enforces.** The engine never spawns vendor CLIs. It computes the next step, validates inputs/outputs, and records. The harness (Claude Mod, Codex plugin, or the CLI's own headless mode) runs the model.
2. **Steps are skills with contracts.** Each step dir = `contract.yaml` + `SKILL.md` (judgment) or `script.*` (exec) + `scenarios/`. Contracts are typed: `in`, `out`, `tools`, `side_effects` (model alias lives in `models.yaml step_models:`).
3. **Judgment steps run as isolated subagents.** Fresh context per step: only that step's SKILL.md, resolved inputs, allowed tools, output schema.
4. **Artifacts are named, not pathed.** Steps reference `{in.x}` / `{out.y}`; the engine owns the run base path and resolves names to paths.
5. **Writes need a gate token.** Signoff is a resumable `gate` step, not a blocking exit code.
6. **Run data lives in a DB, not git.** SQLite default, Postgres via `ORCHESTRATOR_STATE_URL` (done on branch). Packs stay in git; runs, usage, events, learn results do not.
7. **Aliases, not vendors. DONE (models.yaml step_models).** Contracts say `strong|standard|fast|code`; the harness (or `~/.orchestrator/models.yaml` headless) maps to real models and fallback chains. `parser.py:237-241` already rejects `model:` in contracts; `model_routes.resolve_step_alias` already implements the alias mapping via `step_models:` in `models.yaml`.

---

## Target architecture

```
Claude Code process
 ├─ Mod (TypeScript)
 │    ├─ tool.register("orchestrator.run")   ← only tool the main agent sees
 │    ├─ $.agent.spawn per judgment step     ← SKILL.md + in + tools + out_schema
 │    ├─ $.ui.ask for gates                  ← approve / edit / cancel
 │    ├─ tool.call wrapper                   ← blocks writes without gate token
 │    └─ ui.render side pane                 ← from `orchestrator status --json`
 └─ $.process.run → `orchestrator` CLI
        └─ engine: dispatch, validate, record, artifacts, gates, idempotency
              └─ SQLite / Postgres (run doc as JSON column + step_history index)

Codex plugin: same CLI via skill + agents/ mirror (no hooks; enforcement is CLI-side)
Headless (cron/CI): CLI calls model API directly with the same payload
```

### CLI protocol (replaces `next`/`done` + exit codes)

```
orchestrator start <recipe> <slug> --inputs '{...}' --json   → {run_id, slug, next}
orchestrator step  <run>  --json      → {status, kind, step_id, payload}
orchestrator done  <run> <step_id> --out '{...}' --usage '{...}'
orchestrator approve <run> <token> [--edits '{...}']
orchestrator cancel  <run>
orchestrator status  <run> --json     → nodes, attempts, artifacts, cost
orchestrator events  <run> --since <ts> --json
orchestrator validate <recipe>        → wiring, in/out, gates-before-writes
orchestrator doctor / graph / pack --target claude|codex
```

`status` values: `ready | running | done | blocked | needs_you | error`
`kind` values: `exec | judgment | gate`

`step` executes every consecutive exec step internally and returns only at a judgment or gate (minimises subprocess spawns from the Mod).

### Step payload (judgment)

```json
{
  "status": "ready",
  "kind": "judgment",
  "step_id": "design",
  "model": "strong",
  "max_turns": 40,
  "tools": ["fs.read", "fs.write", "shell.test"],
  "system": "<SKILL.md with {in.*}/{out.*} templated + learnings>",
  "in": { "discovery": "/abs/path/discovery.md" },
  "out": { "design": "/abs/path/design.md", "tasks": "/abs/path/tasks.yaml" },
  "out_schema": { "complexity": { "enum": ["XS", "S", "M", "L", "XL"] } },
  "cwd": "/abs/worktree"
}
```

### Contract shape

```yaml
id: design
version: 3
kind: judgment            # exec | judgment | gate
max_turns: 40
tools: [fs.read, fs.write, shell.test]
side_effects: []          # e.g. [write:git, write:ticket]
in:
  discovery: {artifact: discovery.md}
  ticket:    {artifact: ticket-context.md, optional: true}
out:
  design:    {artifact: design.md}
  tasks:     {artifact: tasks.yaml, validate: validate-tasks-yaml.sh}
  complexity:{type: enum, values: [XS,S,M,L,XL]}
```

No `model:` key in the contract — the alias lives in `models.yaml` under
`step_models:` (`parser.py:237-241` rejects `model:` in contracts;
`model_routes.resolve_step_alias` already implements principle 7).

This bumps `docs/pack-convention.md` to protocol v2 (v1 says `kind` is
decorative and ignores unknown keys; v2 makes `kind`, `in`, `out`, `tools`,
`side_effects`, `max_turns` load-bearing).

### Recipe shape

```yaml
name: feature
version: 4
artifacts_root: spec/changes/{slug} # default .orchestrator/runs/{slug}/artifacts
inputs: { ticket: { type: string } }
steps:
  - check-rerun
  - create-worktree
  - load-ticket-context
  - explore
  - design
  - { id: design-review, on_failure: design }
  - { gate: design-signoff, show: [design, tasks], approve_as: impl_token }
  - { id: implement, on_failure: design, requires: impl_token }
  - { id: code-review, on_failure: implement, max_retries: 8 }
  - learn
  - workflow-report
```

### Run layout

```
<repo>/.orchestrator/runs/<slug>/
  state.yaml          # run_id (ULID), recipe, sha, nodes[{id,status,attempts}], history
  artifacts/          # declared outputs (or artifacts_root if set)
  scratch/            # gitignored: prompts, stdout, tmp
```

---

## Phase 0 — Freeze and baseline (½ day)

- [x] Tag current `main` as `v0-two-driver`.
- [x] Record test count (538) and line count (9,597) as the baseline.
- [x] Write `docs/protocol-v2.md` from the sections above so every phase has a spec to point at.
- [x] Decide: `orchestrator run` (self-drive) is deprecated in favour of harness-driven + headless. Announce in README.
- [x] Tests must run via `.venv/bin/python -m pytest` — bare `pytest` picks homebrew python without dev extras and fakes 22 failures; pin in Makefile.
- [x] Commit working tree of `feat/acp-server` as WIP (20 `.claude/skills` deletions = Phase 4.4 done; `bridge.py` NOT committed — drop it).
- [x] gitignore `.pnpm-store/`.

**Exit:** protocol doc merged; nothing else changes.

---

## Phase 1 — One driver, structured I/O

Biggest win; removes the two-path design.

### 1.1 New verbs alongside old

- [x] Add `start`, `step`, `done --out/--usage`, `status --json` in `cli.py`, backed by existing `dispatch`/`record`. (c7b120d: `protocol.py`, 664 lines)
- [x] `step` returns the JSON payload above; exit codes collapse to `status`. (c7b120d)
- [x] `step` batches consecutive exec steps. (c7b120d commit message: "step batches exec steps and returns at judgment/gate")

### 1.2 Contracts get `kind`, `model`, `tools`, `out` schema

- [x] Extend `StepContract` in `parser.py`; `kind` inferred (`agent:`/`script:` accepted as v1 aliases) for back-compat during migration. (c7b120d)
- [x] `validate_workflow.py`: reject unknown `kind`, missing `out` on judgment steps. (c7b120d)
- [x] Bump `pack-convention.md` to v2. (c7b120d, extended in 9126c50)

### 1.3 Replace COMPLETION with structured output

- [x] `done` accepts JSON `--out` validated against `out` schema; artifact existence + enum checks. (c7b120d)
- [ ] Delete `parse_completion.py` and `_COMPLETION_CONTRACT` once all 23 steps migrate. **Open**: pack migrated all 28 steps to `out:` on `workflows/protocol-v2` (4ee763d: "COMPLETION blocks are gone"), but `parse_completion.py` is still live: run_loop.py imports it for the legacy COMPLETION path taken by any step without `out:`. Delete once every consumer pack is on v2.

### 1.4 Remove all spawning

- [x] Delete `acp_client.py` (376), `session_cli.py` (155), `buzz_adapter.py`
      (60), `bridge.py` (208, uncommitted — never committed). (cb1236c)
- [x] Delete ACP + argv arms of `run_loop.py`. (cb1236c: run_loop.py 510-line diff; engine 9,384 → 7,609 lines)
- [x] Delete `usage_adapters.py`. (cb1236c)
- [x] Delete `spawn_resume.py`. (cb1236c)
- [ ] Delete `parse_completion.py` (after 1.3). Still imported by run_loop.py for legacy steps.
- [x] `models_config_cli.py`: tool-template parts deleted. (cb1236c doctor.py/model_routes.py trims; bd06288 on pack side stripped `tools:` block from models.yaml as dead)
- [x] Keep `model_routes.py` alias resolution but strip `tools:` templates. (cb1236c + bd06288)
- [x] `models.yaml` keeps aliases + fallbacks only. (bd06288, pack repo)
- [x] `pricing.py`: kept, prices from `--usage`. (present in orchestrator_next/, c7b120d wired `--usage` through `done`)
- [x] `sessions.py` — resolved: **deleted**, not kept. (cb1236c, 600 lines)

### 1.5 Headless mode

- [x] `orchestrator run --headless <recipe> <slug>` / `orchestrator headless`: drives `step`/`done` in-process, Anthropic SDK, built-in `fs`/`shell`/`git` tool runner. (c7b120d: `headless.py`, 439 lines; gates support added d0f947d `--auto-approve`; credential fail-fast 838c4b0)
- [x] `DRIVE.md` deleted. (c7b120d)
- [ ] **Never run against the live Anthropic API** — no `ANTHROPIC_API_KEY` was available in this session; verified only against mocked tests (`test_headless.py`, 285 lines). Live-API smoke test is open.

### 1.6 Preserve parallel-dispatch invariants

- [x] CAS (`state_store.py` `StateConflictError`), `_record_with_retry`, `worktree_lock.git_lock()`, engine-owned `tasks.yaml` mutation all still present through Phases 1-3; additionally **92e3e32** found and fixed a real CAS bug introduced during this work (batch claim token read after the ready-set decision instead of before it — let two dispatchers claim the same batch, reproduced 204/300 in a 2-thread stress test) before it could regress the invariant.
- [x] `worktree_lock.py:5-30` failure mode re-covered by `test_parallel_fake_runner.py` (added cb1236c, extended 92e3e32).

**Deletes:** `acp_client.py`, `session_cli.py`, `buzz_adapter.py`, `bridge.py`
(uncommitted), `usage_adapters.py`, `spawn_resume.py`, `sessions.py`,
ACP + argv arms of `run_loop.py`, `DRIVE.md` — all done.
`parse_completion.py` still present, imported by run_loop.py for steps without `out:` (open).
**Exit:** all 28 pack steps run end-to-end via `start/step/done` structured `out:` on `workflows/protocol-v2` (4ee763d). Old `next`/`done`/`run` verbs are **not removed** — still live in `cli.py` (`_core_verbs`, `orchestrator_next/cli.py:344-378`) as a deprecated back-compat path alongside v2; `next`/`done` dispatch by argument shape (`_is_v2_done`).

---

## Phase 2 — Artifacts and state diet

### 2.1 Engine-owned run base path

- [x] `paths.py`: `run_dir`/`artifacts_dir`/`scratch_dir`, worktree-aware, honours recipe `artifacts_root`; `run_id` is uuid7 (plan said ULID — equivalent time-sortable choice), plus `pack_sha`. (c69cbf9, 108 lines)
- [x] `.orchestrator/runs/*/scratch/` gitignored, engine + pulled packs. (c69cbf9)

### 2.2 Named artifacts in contracts

- [x] `workflows`-repo work landed on branch `protocol-v2` (commit `799db52`: typed contracts, all 28 steps — pack has grown from 23 to 28 steps since the plan was written).
- [x] Migrated `## Inputs` / `## Outputs` prose to `in:`/`out:` in every `contract.yaml`. (799db52)
- [x] `{in.x}` / `{out.y}` templated into SKILL.md at dispatch (c69cbf9, engine side); `$WORKTREE_ARTIFACT_DIR`/`$CHANGE_ID`/`$WORKFLOW_STATE_DIR` references deleted from charters. (4ee763d, pack side)
- [x] `validate_workflow`: every `in` artifact has an upstream producer — caught a real wiring bug doing it (`patch.yaml` fed `discovery.md` into `design` with no producer, fixed in 6b1495e).

### 2.3 Record artifacts by hash

- [x] `artifacts.py`: sha256 on completion, `validate:` script runner, `node_is_unchanged` predicate. (c69cbf9, 190 lines)
- [x] Resume skips steps whose inputs are unchanged and outputs exist, through both serial and batch dispatch via the same CAS save. (c69cbf9; `test_dispatch_skip_unchanged.py`, 241 lines)

### 2.4 State diet

- [x] `generate_plan.py`: gate entries promoted as `kind: gate` nodes rather than copying full step config (d0f947d); node shape carries id/status/attempts/artifacts per c69cbf9's artifacts.py integration.
- [x] `state.yaml` gains `run_id`, `recipe`, `pack_sha`. (c69cbf9)
- [x] Scratch discarded on finalize. (c69cbf9)

**Exit:** met. No path strings in state; no env-var paths in skills; `validate-workflow` catches wiring errors (proven on a real bug, not just designed to).

---

## Phase 3 — Gates, trust, DB

### 3.1 Gates

- [x] New step kind `gate`: engine renders preview from named artifacts/values, issues a token, sets run `blocked`. (d0f947d, `gates.py` 171 lines)
- [x] `approve <run> <token> [--edits]` resumes; `cancel` aborts; edits recorded. (d0f947d)
- [x] `requires: <token>` on steps refused (`needs_you`, dispatch exit 4) until approved; `validate-workflow` refuses a `write:*` side-effect step with no preceding gate. (d0f947d)
- [x] `signoff_policy` deprecated — warned and ignored, no live readers; blocked-exit-2 semantics removed. (d0f947d)
- [x] Follow-up: `write:workspace` side effect (run-infrastructure git writes — worktree create/remove, state archive) made exempt from the gate requirement, since nothing reviewable exists yet at that point. (4fda1e4 engine-side; 41d8135 pack-side reclassification of create-worktree/remove-worktree/archive-completed-change)
- [x] `validate-workflow --json`. (4fda1e4)

### 3.2 Trust and locking

- [x] `recipes.lock` (this repo's plan called it `recipes.lock`; shipped as `config-lock.yaml` per `config pull` — same content: pack URL, commit SHA, pack_sha256, per-step contract versions). (9126c50)
- [x] `~/.orchestrator/trust.toml` `[[allow]]` globs; unlisted remote refused; `require_signed` via `git verify-tag`/`verify-commit`; `ORCHESTRATOR_TRUST_ALL=1` dev bypass. (9126c50, `trust.py` 188 lines)
- [x] `config pull` writes the lock; `config update` diffs contracts/side-effects, `--yes` applies. (9126c50)
- [x] Config resolution levels 4–5 removed; `<pack>/<workflow>` disambiguation kept (lock pins the pack per-repo, disambiguation is still needed when >1 pack is installed). Note: the `config` symlink (`config -> .orchestrator/workflows`) is **still present** in this checkout — not removed.

### 3.3 Database

**DONE** (already on the branch): SQLite default, Postgres via
`ORCHESTRATOR_STATE_URL`, engine is the single writer, no state lives in git,
`orchestrator state list|show|migrate|project` admin commands exist.

Kept the branch's design rather than reintroducing a relational schema — the
7-table schema (`runs, steps, gates, events, usage, artifacts, canaries`) and
`v_*` views were **not built**; this was intentional per the plan, confirmed
still the design in 9126c50.

- [x] `tenant_id` column + migration, `ORCHESTRATOR_TENANT` env. (9126c50)
- [x] PII redaction per contract `pii:` field before write. (9126c50 `redact.py` scaffold; c9786e0 wires the call site into `record.py` — scans outputs/out/in/edits/evidence, artifacts keep name/path/sha256, opt-in per contract)
- [x] Learn results: `learn_results` table (9126c50); `publish_scenarios.py` / `pack publish-scenarios <pack> --step <id>` exports accepted rows to `scenarios/train.jsonl` (9126c50 engine side; 277bb6a pack side wires `persist-learnings` to call it — no-op until the engine verb lands, landed same day).
- [x] `runs/*.jsonl`, `metrics.md` — confirmed absent from current engine tree (no metrics.duckdb references either); superseded design already noted in memory.

Config knobs: `ORCHESTRATOR_STATE_URL`, `ORCHESTRATOR_MAX_PARALLEL`, `ORCHESTRATOR_TENANT`, `ORCHESTRATOR_TRUST_ALL`.

**Exit:** met. A write-capable step cannot run without a gate token (validate-workflow enforces it structurally). A pack cannot load unless trusted (`trust.toml` allow-list + optional signing). Every run is queryable in SQL (JSON-column + step_history index, not a relational schema — accepted deviation).

---

## Phase 4 — Surfaces

### 4.1 Claude Mod (early access — VERIFIED real, Sept 2026)

Verified: github.com/anthropics/claude-code/tree/main/mods; loads locally with
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir`. API notes:
`docs/claude-mod-api-notes.md`. Constraint: `$.agent.spawn` takes only
prompt/subagentType/model/cwd — no system prompt, tools, schema, maxTurns.

- [x] `hooks/hooks.json` → `modules: ["./register.ts"]`; `.claude-plugin/plugin.json`. (11a98a6, generated by `pack_export.py`)
- [x] `session.start` registers `mcp__<plugin>__run`/`status` (shipped as MCP tool registration, not bare `tool.register({name:"run"})` — functionally the plan's intent). (ca1ca42, `mod/register.ts` 521 lines)
- [x] Loop: `$.process.run(orchestrator step --json)`; on `judgment` → `$.agent.spawn`, await `turn.complete` by `agentId`, parse trailing JSON block from `answer`, `done --out --usage`; on `gate` → `$.ui.ask`. (ca1ca42, `mod/protocol.ts` 198 lines)
- [x] `agents/<step_id>.md` generated per pack step (SKILL.md + `tools:` + `model:` frontmatter). (11a98a6, Phase 4.3)
- [x] `on('tool.call')` deny on write-capable tools unless the run's gate token is present. (ca1ca42) **Gap found**: `MultiEdit` is not gated — it isn't in the engine's tool union, so it's simply unlisted rather than denied (open item, not yet fixed).
- [x] `$.ui.status` one-liner per step. (ca1ca42)
- [x] Fallback: `skills/orchestrate/SKILL.md` stub generated for when function hooks are off. (11a98a6)
- [x] tsc clean; loads with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`. (ca1ca42)
- [x] First live Mod run against a real repo (`.tmp/mod-e2e-*.log`), which surfaced two real bugs, both fixed same day: subagent type wasn't namespaced (bare step id case-folded onto builtin `Explore`, hard-failed for `design`), and `start` minted a second `run_id` instead of resuming an existing live slug for the same slug; also hardened so only a literal `approve` answer from `$.ui.ask` counts as consent — a dismissed prompt or `-p` mode now leaves the run standing instead of silently cancelling it. (c8f8b52)
- [ ] **Open, not yet fixed**: the same live run hit the Mod's 10-second `tool.call` hook budget — driving the full step loop synchronously inside a single tool call doesn't fit that budget. c8f8b52's commit message flags "redesign next" (async redesign in progress per current status, not yet committed on this branch).

### 4.2 Codex plugin

- [x] `.codex-plugin/plugin.json` + marketplace listing. (11a98a6, "Codex target mirrors it")
- [x] `skills/orchestrator/SKILL.md` drives the loop via shell, delegates judgment steps to `agents/<step>.md`. (11a98a6)
- [ ] Alias table / marketplace.json format **unverified** — 11a98a6's own commit message says "marketplace.json shape unverified (README TODO)"; still open.

### 4.3 Generator

- [x] `orchestrator pack --target claude|codex` emits plugin dirs from the pack, idempotent via `.generated-manifest.json`, no hand-maintained manifests. (11a98a6, `pack_export.py` 570 lines; hardened ca1ca42 — step_id regex, write/delete containment under out_dir, symlinks unlinked not followed, yaml.safe_dump for frontmatter; types-resolution order fixed 46f4862)

### 4.4 Cleanup

- [x] Personal `.claude/skills/*` (20 dirs) moved out of the engine repo.
- [x] `DRIVE.md`, `install.sh`, `package.json`, `bin/orchestrator`, `pnpm-lock.yaml` all deleted; confirmed absent from the current tree. `pyproject.toml [project.scripts]` provides the console script; `uv tool install` is the only install path. (547343b)

**Exit:** met for Claude Code (Mod prototype ran live against a real repo, modulo the two open items above — hook-budget redesign and MultiEdit gating gap). Codex plugin generated but marketplace.json format unverified.

---

## Phase 5 — Repo split and release

- [x] `orchestrator` — engine only, repo split already true (was true before this plan started — `workflows` has been a separate repo since commit `70aa040`/pre-simplification). Line count target of ~3k **not met**: engine is 12,058 lines now (grew, didn't shrink — see Status below), `uv tool install` is the install path (547343b).
- [x] `workflows` — recipes + step skills + scenarios, protocol v2 landed on branch `protocol-v2` (7 commits, not merged to `main`).
- [x] Generated Claude/Codex plugin artifacts published from `workflows` CI via `pack --target`, not as separate `orchestrator-mod`/`orchestrator-codex` repos — CI builds and attaches them as release assets on `v*` tags instead. (89f7de2 `publish-plugins.yml`)
- [x] Canary job: `canary.yml` + `canary/run.sh`, nightly, runs every `run:` (exec) step contract against a throwaway git fixture; `ticket-*` and `persist-learnings` skipped (need a live backlog/registry backend). (89f7de2) Note: this canary is a CI job against fixtures, not the DB `canaries` table from the (rejected) relational schema in 3.3 — no live-tool nightly run against the `canaries` table exists because that table was never built.
- [x] README rewrite covering install/trust/pull/run from Claude Code+Codex+headless+reporting. (547343b engine side; 89f7de2 pack side)
- [ ] **Both branches unmerged**: `simplify-v2` (this repo) and `protocol-v2` (workflows repo) are both still feature branches, not merged to their respective `main`s. Pack CI (`ci.yml` validate/pytest/shell, `publish-plugins.yml`, `canary.yml`) exists only on the unmerged `protocol-v2` branch. Pack's own pytest run has 18 pre-existing failures against stale flat-file-layout assertions (documented in 89f7de2's own commit message as non-blocking).

---

## Keep as-is

`dispatch.py` (resume, retry storm guard), `readiness.py` (single status mutator), `record.py` routing (`on_failure`, `max_retries`, rework re-entry), `validate_workflow.py`, `graph.py`, the step-dir convention, learn → scenarios idea, the June-2026 cleanup invariants, `state_store.py`, `run_store.py`, `dispatch.dispatch_batch`, `worktree_lock.py`, await_input routing.

## Delete (by end)

Status as of this checkout (`ls orchestrator_next/*.py`, `ls DRIVE.md install.sh bin/ package.json pnpm-lock.yaml`, `ls -la config`):

- **Gone**: `acp_client.py`, `session_cli.py`, `buzz_adapter.py`, `bridge.py` (never committed), `sessions.py`, `usage_adapters.py`, `spawn_resume.py`, ACP + argv arms of `run_loop.py`, `DRIVE.md`, `install.sh`, `bin/orchestrator`, `package.json`, `pnpm-lock.yaml`, `.claude/skills/*` (moved). `models_config_cli.py` tool-template parts removed (file itself kept, as planned).
- **Still present, planned for deletion**: `parse_completion.py` (legacy COMPLETION path, still imported by run_loop.py); `config` symlink (`config -> .orchestrator/workflows`, still resolves — config ladder levels 4-5 were removed but this legacy symlink itself wasn't deleted).
- **Not applicable / superseded**: `runs/*.jsonl`, `metrics.md` — confirmed absent, no dedicated deletion commit needed (already gone before this phase, per `[[project_live_dashboard]]` memory: no DuckDB metrics store exists).

---

## Status (2026-09-17)

| Metric                                               | Baseline (Phase 0) | Now                                                                                             |
| ---------------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------- |
| Engine lines (`orchestrator_next/*.py`, excl. tests) | 9,597              | 12,058                                                                                          |
| Test count                                           | 538                | 649 passed, 1 skipped                                                                           |
| Commit range                                         | `v0-two-driver`    | `v0-two-driver..HEAD` (16 commits, this repo) + `main..protocol-v2` (7 commits, workflows repo) |

Line count moved **up, not down** relative to the plan's ~3,500-line target.
The net effect of Phase 1.4's deletions (9,384 → 7,609) was reversed and then
some by Phase 1's new protocol/headless code (+1,383 in c7b120d alone) plus
gates, trust, redaction, pack export, and the Mod TypeScript surface added in
Phases 2-4. The simplification's _shape_ (one driver, structured I/O,
engine-owned artifacts, DB-backed state, harness-driven execution) is
delivered; the line-count goal was not, because Phases 3-4 added real surface
area (gates, trust/lock, Mod, pack export) that wasn't present in the
9,597-line baseline it's being compared against.

Six open items carried forward:

1. **Phase 1.3/1.4** — `parse_completion.py` still present and unreferenced; safe to delete now. Old `next`/`done`/`run` self-drive verbs also still live in `cli.py` (deprecated, not deleted).
2. **Phase 1.5** — headless mode never run against the live Anthropic API in this session (no credential available); verified only via mocked tests.
3. **Phase 3.3** — relational schema/views intentionally dropped (by design, not a shortfall); tenant_id, redaction, and learn_results are done.
4. **Phase 4.1** — first live Mod run hit the Claude Code Mod's 10-second `tool.call` hook budget; an async redesign is in progress but not yet committed on this branch. `MultiEdit` is not covered by the write-gate because it isn't in the engine's tool union.
5. **Phase 4.2** — Codex `marketplace.json` format is unverified against the real Codex plugin loader.
6. **Phase 5** — repo split was already true going in. Pack CI (validate/publish/canary) landed on `workflows/protocol-v2`, not merged. Both `simplify-v2` (this repo) and `protocol-v2` (workflows repo) remain unmerged feature branches. Pack's pytest suite carries 18 pre-existing failures against a stale flat-file layout, called out as non-blocking in the CI commit itself.

## Risks

- **Mod API drift** — early access, gated by `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`; may change without notice. Mitigation: skill+Agent-tool fallback stays first-class. Structured subagent output is parsed from `turn.complete.answer`, not a schema.
- **Contract migration of 23 steps** — mechanical but wide. Mitigation: `kind` inference and a `--legacy-completion` flag during Phase 1–2 so steps migrate one at a time.
- **Gate fatigue** — measured via `v_gate_stats`; tune which steps gate per recipe, not globally.
- **Headless tool runner scope creep** — cap at `fs`, `shell`, `git`; anything else is an exec skill.
- **Transport deletion breaks 22 ACP tests + parallel e2e tests** — rewrite parallel tests against headless/fake harness before deleting `acp_client.py`.

## Branch hygiene before Phase 0

- `run_loop.py:822` bare `except Exception: pass` around spawn-resume — goes away with `spawn_resume.py` deletion.
- `parser.py:335` dead `data.get("model")` thread — remove.
- `DRIVE.md` deleted in 1.5.

## Order of work (suggested)

1. Cut branch from `feat/acp-server` HEAD, commit WIP, drop `bridge.py`, delete ACP/buzz (Phase 1.4 transport half) **first** so the tree is honest.
2. Phase 0 → 1.1–1.3 (new verbs + structured output) on a branch, old verbs intact.
3. Migrate 3 steps (explore, design, design-review) end-to-end; run headless.
4. Phase 2 on those 3 steps; then batch-migrate the rest.
5. Phase 1.4–1.5 remaining deletes.
6. Phase 3.1 gates → 3.3 DB → 3.2 trust.
7. Phase 4 Mod prototype under the flag; Codex plugin.
8. Phase 5 split.
