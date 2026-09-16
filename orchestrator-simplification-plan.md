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

- [ ] Add `start`, `step`, `done --out/--usage`, `status --json` in `cli.py`, backed by existing `dispatch`/`record`.
- [ ] `step` returns the JSON payload above; exit codes collapse to `status`.
- [ ] `step` batches consecutive exec steps.

### 1.2 Contracts get `kind`, `model`, `tools`, `out` schema

- [ ] Extend `StepContract` in `parser.py`; `kind` inferred (`run:` → exec, `prompt:` → judgment) for back-compat during migration.
- [ ] `validate_workflow.py`: reject unknown `kind`, missing `out` on judgment steps.
- [ ] Bump `pack-convention.md` to v2; fix its stale `model: <alias>` line (§2 line 34).

### 1.3 Replace COMPLETION with structured output

- [ ] `done` accepts JSON `--out` validated against `out` schema; `_enforce_required_outputs` checks schema + artifacts.
- [ ] Delete `parse_completion.py` and `_COMPLETION_CONTRACT` once all 23 steps migrate.

### 1.4 Remove all spawning

- [ ] Delete `acp_client.py` (376), `session_cli.py` (155), `buzz_adapter.py`
      (60), `bridge.py` (208, uncommitted — just don't commit it).
- [ ] Delete ACP + argv arms of `run_loop.py`: `invoke_tool`/`_build_argv`/
      `_resolve_tool_template` (lines 152-209), `run_agent_step` (260-357).
- [ ] Delete `usage_adapters.py` — only after ACP is gone (`NormalizedResult`
      consumer removed); usage arrives via `--usage`.
- [ ] Delete `spawn_resume.py` (`run_loop.py:823`).
- [ ] Delete `parse_completion.py` (after 1.3).
- [ ] `models_config_cli.py`: delete tool-template parts only.
- [ ] Keep `model_routes.py` alias resolution (6 call sites incl.
      `doctor.py`) but strip `tools:` templates.
- [ ] `models.yaml` keeps aliases + fallbacks only; `tools:` templates removed.
- [ ] `pricing.py`: keep only if cost-so-far is still wanted; price from `--usage`.
- [ ] Open question: `sessions.py` (600 lines, in-process session runs,
      `research`/`--resume`) — default is **delete** unless a session-mode
      workflow is still wanted.

### 1.5 Headless mode

- [ ] `orchestrator run --headless <recipe> <slug>`: drives `step`/`done` in-process, calls one model API (Anthropic SDK; LiteLLM optional) with `system`/`in`/`out_schema`, provides a minimal built-in tool runner (`fs.*`, `shell.*`, `git.*`).
- [ ] This replaces both `DRIVE.md`'s cloud loop and `sessions.py`'s self-drive.

### 1.6 Preserve parallel-dispatch invariants

- [ ] Every state-write change in Phases 1-3 must keep: CAS (`state_store.py`
      `StateConflictError`), `_record_with_retry`, `worktree_lock.git_lock()`,
      and engine-owned `tasks.yaml` mutation.
- [ ] Cite the measured failure in `worktree_lock.py:5-30`: 8 workers, one
      worktree → 1 of 8 commits landed (7x `.git/index.lock` failures), and 1
      of 8 `tasks.yaml` status updates survived (7 lost updates). Any new
      state-write path must not reintroduce either failure mode.

**Deletes:** `acp_client.py`, `session_cli.py`, `buzz_adapter.py`, `bridge.py`
(uncommitted), `parse_completion.py`, `usage_adapters.py`, `spawn_resume.py`,
ACP + argv arms of `run_loop.py`, `DRIVE.md`.
**Exit:** all 23 steps run end-to-end via `start/step/done` in headless mode; old `next/done` removed.

---

## Phase 2 — Artifacts and state diet

### 2.1 Engine-owned run base path

- [ ] `paths.py`: single resolver `run_dir(slug)`, `artifacts_dir(run)`, `scratch_dir(run)`; honours recipe `artifacts_root` and worktree.
- [ ] Add `.orchestrator/runs/*/scratch/` to gitignore templates.

### 2.2 Named artifacts in contracts

- [ ] `workflows`-repo work (the pack is gitignored here — commit `70aa040`;
      pack tests moved to that repo in `1afc9e3`).
- [ ] Migrate `## Inputs` / `## Outputs` prose in 23 SKILL.md files to `in:`/`out:` in `contract.yaml`.
- [ ] Template `{in.x}` / `{out.y}` into SKILL.md at dispatch; delete `$WORKTREE_ARTIFACT_DIR` / `$CHANGE_ID` references — these are already prose conventions, not env vars.
- [ ] `validate_workflow`: every `in` artifact has an upstream producer.

### 2.3 Record artifacts by hash

- [ ] `record.py`: after judgment/exec, verify declared `out` files exist, run `validate:` scripts, store `{name, path, sha256}` in state.
- [ ] Resume skips steps whose inputs' hashes are unchanged and outputs exist.

### 2.4 State diet

- [ ] `generate_plan.py`: stop copying `rules`, `goal`, `inputs`, `outputs` into nodes. Node = `{id, status, attempts, artifacts[]}`.
- [ ] `state.yaml` gains `run_id` (ULID), `recipe`, `pack_sha`.
- [ ] Archive step moves `artifacts/` only; scratch is discarded.

**Exit:** no path strings in state; no env-var paths in skills; `validate` catches wiring errors.

---

## Phase 3 — Gates, trust, DB

### 3.1 Gates

- [ ] New step kind `gate`: engine renders preview from named artifacts/values, issues a token, sets run `blocked`.
- [ ] `approve <run> <token> [--edits]` resumes; `cancel` aborts. Edits are recorded.
- [ ] `requires: <token>` on steps; `validate` refuses a recipe where a step with `side_effects` containing `write:*` has no preceding gate.
- [ ] Existing `signoff_policy` maps onto gates; blocked-exit-2 semantics removed.

### 3.2 Trust and locking

- [ ] `recipes.lock` in consumer repo: pack URL + commit SHA + per-step contract versions.
- [ ] `~/.orchestrator/trust.toml`: allowed repos/orgs + signing keys. Engine refuses unlisted/unsigned packs.
- [ ] `config pull` writes the lock; `config update` shows a diff of contracts and side effects before bumping.
- [ ] Remove config resolution levels 4–5, the `config` symlink, and `<pack>/<workflow>` disambiguation (lock pins the pack).

### 3.3 Database

**DONE** (already on the branch): SQLite default, Postgres via
`ORCHESTRATOR_STATE_URL`, engine is the single writer, no state lives in git,
`orchestrator state list|show|migrate|project` admin commands exist.

Keep the branch's design rather than reintroducing a relational schema:
whole doc in one JSON column + a derived `step_history` index
(`state_store.py:42-50`). **Drop** the 7-table relational schema
(`runs, steps, gates, events, usage, artifacts, canaries`) and the `v_*`
views below — they duplicate what the JSON-column + index already gives us.

Remaining open work:

- [ ] `tenant_id` column.
- [ ] PII redaction per contract `pii:` field before write.
- [ ] Learn results to DB; reconcile with `plan-learn-rules-and-step-evals.md`'s
      scenarios→`train.jsonl` flow (`pack publish` exports accepted rows).
- [ ] Remove `runs/*.jsonl`, `metrics.md` from the engine repo history going forward.

Config knobs: `ORCHESTRATOR_STATE_URL`, `ORCHESTRATOR_MAX_PARALLEL`.

**Exit:** a write-capable step cannot run without a token; a pack cannot load unless trusted; every run is queryable in SQL.

---

## Phase 4 — Surfaces

### 4.1 Claude Code plugin (Mod API verified NOT to exist, Sept 2026)

Verified against code.claude.com/docs/en/plugins-reference + hooks: no
`modules:` loader, no `tool.register`/`$.agent.spawn`/`$.ui.ask`. Use the
plugin primitives that do exist:

- [ ] `.claude-plugin/plugin.json` + `skills/orchestrate/SKILL.md`: the skill
      drives the loop via Bash — `orchestrator step --json`; on `judgment` →
      Agent tool with `subagent_type: <step_id>`; on `gate` → AskUserQuestion
      (approve / edit / cancel) then `orchestrator approve`; on `needs_you` →
      report and stop.
- [ ] `agents/<step_id>.md` generated from each pack step (SKILL.md body +
      `tools:` allowlist + `model:` alias mapped via models.yaml). Fresh
      context per step = principle 3.
- [ ] `hooks/hooks.json` PreToolUse on write-capable tools (Edit/Write/Bash
      git push, MCP write tools): deny unless the current run's gate token
      is present (`orchestrator status --json` → `gate_token`).
- [ ] Status pane: no native API. `orchestrator status` printed by the skill
      after each step; optional monitor of the run's events file.
- [ ] Usage: subagent result carries usage; skill passes it to `done --usage`.

### 4.2 Codex plugin

- [ ] `.codex-plugin/plugin.json` + `.agents/plugins/marketplace.json`.
- [ ] `skills/orchestrator/SKILL.md`: drive the loop via shell; delegate judgment steps to `agents/<step>.md`.
- [ ] Alias table in Codex config; enforcement is CLI-only here (documented).

### 4.3 Generator

- [ ] `orchestrator pack --target claude|codex` emits plugin dirs from the pack. No hand-maintained manifests.

### 4.4 Cleanup

- [ ] Move personal `.claude/skills/*` (20 dirs) out of the engine repo. **Done** (uncommitted on `feat/acp-server`).
- [ ] Remove `DRIVE.md`, `install.sh`, npm shim (`package.json`, `bin/`), `pnpm-lock.yaml`, `Makefile` targets tied to them; `uv tool install` is the install.

**Exit:** `orchestrator feature orc-1` runs from Claude Code with a side pane and native approvals; same recipe runs from Codex and from cron.

---

## Phase 5 — Repo split and release

- [ ] `orchestrator` — engine only (Python, ~3k lines, `uv tool install`).
- [ ] `workflows` — recipes + step skills + scenarios; signed tags; `recipes.lock` example.
- [ ] `orchestrator-mod` / `orchestrator-codex` — generated artifacts, published from `workflows` CI via `pack`.
- [ ] Canary job in `workflows` CI: run exec-step fixtures against live tools nightly; write to `canaries`.
- [ ] README rewrite: install, trust a pack, run from Claude Code / Codex / headless, reporting.

---

## Keep as-is

`dispatch.py` (resume, retry storm guard), `readiness.py` (single status mutator), `record.py` routing (`on_failure`, `max_retries`, rework re-entry), `validate_workflow.py`, `graph.py`, the step-dir convention, learn → scenarios idea, the June-2026 cleanup invariants, `state_store.py`, `run_store.py`, `dispatch.dispatch_batch`, `worktree_lock.py`, await_input routing.

## Delete (by end)

`acp_client.py`, `session_cli.py`, `buzz_adapter.py`, `bridge.py` (never committed), `sessions.py` (tentative — see 1.4 open question), `parse_completion.py`, `usage_adapters.py`, `spawn_resume.py`, `models_config_cli.py` tool-template parts (not the whole file), ACP + argv arms of `run_loop.py`, `DRIVE.md`, `install.sh`, `bin/orchestrator`, `package.json`, `pnpm-lock.yaml`, `config` symlink, config ladder levels 4–5, `runs/*.jsonl`, `metrics.md`, `.claude/skills/*` (moved).

## Risks

- **Mod API** — does not exist (verified Sept 2026); plugin+skill+hooks is the only path. Structured subagent output must be parsed from the agent's final message, not a schema.
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
