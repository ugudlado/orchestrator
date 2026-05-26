---
feature-id: orc-18
linear-ticket: HL-294
---

# Discovery Brief: Unified /doctor — deep health check + workflow graph validator + dispatch hardening

## Feature Summary

ORC-18 consolidates three previously separate efforts (ORC-13 workflow graph validation, ORC-14 dispatch hardening, ORC-28 surface unification) into a single unified `/doctor` health check. Today the orchestrator has three independent validation surfaces — a 6-line `make doctor` existence check, a `config/scripts/orchestrator_next/doctor.py` module shipped under the previously completed `orchestrator-doctor` feature (April 2026 archive) covering 7 structural checks, and `make lint-contracts` / `make stale`. None of them validates the *graph* — that every step referenced by a schema resolves to a contract, every contract's agent resolves to a `.md`, every `flags_read` entry is declared in `schema_defaults`, and every template path in a step contract exists. None catches the documented `make setup from worktree` symlink gotcha. And the dispatch loop reads contracts and agent files without file-not-found guards, so structural drift surfaces as a stack trace mid-run instead of an actionable doctor report. ORC-18 expands the existing `orchestrator doctor` subcommand into a comprehensive validator, exposes it as a user-invokable `/doctor` slash command, and adds the missing dispatch-loop guards that make doctor output actually actionable.

## Personas & Actors

- **Operator / developer running `make doctor` or `/doctor`** — wants a single command that surfaces structural drift before a workflow run blows up. Today they discover drift via a dispatch crash.
- **Autopilot / CI run** — needs a non-zero exit code on FAIL to gate runs; needs WARN-but-zero-exit semantics for soft drift.
- **Dispatch loop (`config/scripts/orchestrator_next/dispatch.py`)** — currently calls `load_contract_for_step` and reads agent files without explicit FileNotFoundError guards; needs to fail fast with a doctor-pointing message instead of a raw exception.
- **Workflow author adding a new schema/step/agent/template** — needs immediate feedback when a reference fails to resolve, not at first dispatch.

## Use Cases

### Happy Path

UC-1: User-invoked /doctor — operator runs `/doctor` after a rebase or environment change; receives a consolidated PASS/WARN/FAIL table covering existence, symlinks, ORCHESTRATOR_HOME, orphaned state, schema/agent/flag/template graphs, and exits 0.

UC-2: make doctor parity — operator runs `make doctor`; the Makefile target shells out to `orchestrator doctor` (or its successor entry point) and produces the same consolidated report; the legacy 6-line shell version is replaced.

UC-3: Schema graph validation — author adds a step ID to `config/workflows/feature.yaml` but forgets to create `config/steps/<id>/contract.yaml`; running `/doctor` reports `[FAIL] schema graph: feature.yaml references <id> which has no contract`.

UC-4: Agent graph validation — author renames `discoverer` to `discovery-agent` in a contract but does not rename `agents/discoverer.md`; `/doctor` reports the missing agent .md.

UC-5: Flag graph validation — author adds `flags_read: [new_flag]` to a contract but does not add `new_flag` to the schema's `flags` defaults; `/doctor` reports `[FAIL] flag graph: new_flag in <step> contract not declared in <schema>`.

UC-6: Template graph validation — author references `config/templates/feature/<name>.md` from a step contract that doesn't exist; `/doctor` reports the missing template.

UC-7: ORCHESTRATOR_HOME drift detection — operator ran `make setup` from a worktree (the documented gotcha in `spec/project.yaml`); `/doctor` reports `[FAIL] ORCHESTRATOR_HOME points to <worktree-path>, expected main repo root <path>`.

UC-8: Dispatch-loop file-not-found guard — `dispatch.py` attempts to read a deleted step contract or agent file; instead of stack-tracing, it raises a clear `ContractDispatchError` with the file path and a hint to run `/doctor`.

### Error & Edge Cases

UC-E1: WARN-only run — symlinks valid, all graphs resolve, but one orphaned state.yaml exists for a deleted worktree; report lists the orphan, exits 0 (warnings only, no FAIL).

UC-E2: FAIL on missing referenced step contract — schema references step ID with no contract directory or legacy flat YAML; doctor exits non-zero and report names the schema and step.

UC-E3: Dispatch race vs doctor — a workflow_plan was frozen at init time pointing at a step that has since been deleted from the schema; dispatch already has a fallback path (`StepContract(id=..., agent="inline", run=None, ...)`) in `dispatch.py:444-453`. /doctor must report this as a workflow-plan consistency WARN, not double-fail.

UC-E4: Optional/conditional steps — schemas use `"step if flag"` syntax; doctor must split on " if " when resolving step IDs (the existing `check_workflow_plans` already does this — must be carried into new graph checks).

UC-E5: ORCHESTRATOR_HOME unset — runs from CI or fresh shell where the env var is not exported; doctor should print a clear error pointing at `make setup` rather than KeyError-ing (the existing `_doctor_main` returns 3 — preserve this behavior).

UC-E6: Repo overrides under `.orchestrator/` — when an override exists at `$REPO_ROOT/.orchestrator/steps/<id>/contract.yaml`, doctor must treat it as the authoritative contract for graph resolution (override-aware), not only check global config.

## Scope

### In Scope

- Expand `config/scripts/orchestrator_next/doctor.py` with the missing checks: symlink validity, ORCHESTRATOR_HOME path match, schema→step graph, contract→agent graph (already partial), contract→flag graph, contract→template graph, orphaned-state-vs-worktree detection.
- Make all new graph checks override-aware (resolve against `$REPO_ROOT/.orchestrator/` first, then `$ORCHESTRATOR_HOME/config/`).
- Replace the 6-line `make doctor` recipe with `orchestrator doctor` so a single source of truth produces the report.
- Add a user-invokable `/doctor` slash command (skill) that runs `orchestrator doctor` and surfaces the same output.
- Add file-not-found guards to dispatch loop reads (step contract, agent definition) with messages pointing at `/doctor`. ACTUAL READS: `dispatch.py:443 load_contract_for_step` (already has a fallback) and any agent-file reads downstream; survey during design to enumerate.
- Tighten state.yaml writes to a write-after-verify pattern (write to temp, fsync, rename) — see ORC-14 absorbed scope and AC-9.
- Exit code semantics: 0 on all PASS or WARN-only, non-zero on any FAIL (current module returns 1 for warn / 2 for fail — ticket AC-11 says zero on WARN-only, so semantics need a small change OR a CLI flag — surface in OQ-1).
- Per-category `[OK] / [WARN] / [FAIL]` output preserved.

### Out of Scope

- `--fix` auto-remediation — was explicitly excluded from the prior `orchestrator-doctor` design and ORC-18 does not re-introduce it.
- Column-level DuckDB schema validation (table-presence only, per the prior design).
- JSON or colored output modes — keep the existing flat 3-column text table.
- Multi-repo fleet scanning — single-repo invocation only.
- Removing `make stale` or `make lint-contracts` — those remain as focused tools; `/doctor` may *call* them internally but does not delete them.
- Modifying the existing seven checks' core logic beyond making them override-aware (no rewriting `check_state_valid`, etc.).
- Re-implementing the `orchestrator-doctor` April archive — it already shipped; this builds on top.

## UI Direction

N/A — no UI components. Output is plain text on stdout (terminal table).

## Key Decisions

- **Build-or-reuse decision: extend, don't rebuild.** The `orchestrator-doctor` archive already shipped a 7-check module (`config/scripts/orchestrator_next/doctor.py`, 239 lines). ORC-18 adds checks to the existing module and rewires `make doctor` to call it — no new module, no rewrite. Rationale: the existing module's `CheckResult` namedtuple + flat-function pattern is exactly the shape needed for the new checks; introducing a registry now (Approach 2 from the prior design) is still over-engineered.
- **Override-awareness is required.** New graph checks must resolve against `$REPO_ROOT/.orchestrator/` first (per `CLAUDE.md § Repo overrides`). The existing checks predate the override system and may need a small adjustment — confirm in design.
- **Slash command is a skill, not a new binary.** `/doctor` is a `skills/doctor/SKILL.md` that shells out to `orchestrator doctor` — same pattern as `skills/telemetry/SKILL.md`. No new CLI verb.
- **Dispatch hardening is the smallest possible diff.** Add `try/except FileNotFoundError` at the two confirmed read sites (`load_contract_for_step`, agent-file resolution) and raise `ContractDispatchError` with a `/doctor` hint. Do not refactor the dispatcher.
- **Exit code semantics:** ticket AC-11 says zero exit on WARN-only. Current module returns 1 for WARN. Align module to AC-11; document the change in design.md.

## Open Questions

- OQ-1: Should the exit-code change (WARN → 0 instead of current 1) be a breaking change or behind a `--strict` flag for CI? Ticket AC-11 implies the former.
- OQ-2: Where exactly does the dispatch loop read agent files? `dispatch.py:443` reads the contract; the agent .md itself is read by the agent-spawn machinery further downstream — design needs to enumerate every read site to apply guards uniformly.
- OQ-3: Should `make doctor` *replace* the existing 6-line Makefile body with a shell-out to `orchestrator doctor`, or keep a minimal fallback for environments where the Python module fails to import? Recommend: replace fully, and let import failure surface as exit 3 (matches existing behavior).
- OQ-4: Should `/doctor` slash command accept an optional `--db PATH` / `--orch-home PATH` to override env vars, or is env-only sufficient? Recommend: env-only for v1.
- OQ-5: How does ORC-18 interact with worktree-resident config? When running from `~/code/feature_worktrees/orc-18` (this worktree), should ORCHESTRATOR_HOME be checked against `~/code/orchestrator` (main repo) or against `~/.config/orchestrator` (the install location)? The gotcha in `project.yaml` and the Makefile default (`HOME/.config/orchestrator`) suggest the latter, but the symlink path resolves to the former — design needs to pin this.

### T-1 Survey (dispatch reads + state.yaml write-after-verify)

Survey date: 2026-05-26. Read-only; sources: `config/scripts/orchestrator_next/dispatch.py`, `parser.py`, `resolver.py`, `readiness.py`, `record.py`.

#### `dispatch.py` — step-contract load sites

| Site | Mechanism | File-not-found behavior today |
|------|-----------|-------------------------------|
| `dispatch.py:389` | `load_contract_for_step(step_id, state_yaml_path)` on **resume** (`in_progress` entry) | `except FileNotFoundError` → synthetic `StepContract(id=…, agent=last.agent or "inline", run=None, …)` (lines 390–398) |
| `dispatch.py:443` | `load_contract_for_step(next_step_id, state_yaml_path)` on **fresh** DAG selection | Same fallback to inline-only contract (lines 444–454) |
| `dispatch.py:419` | `readiness.repeat_until_redispatch(state, state_yaml_path)` | Delegates to `readiness.py:142` (see below); not guarded in `dispatch.py` itself |

No literal `agents/` path in `dispatch.py`. Agent resolution happens indirectly via `_resolve_allowed_tools` → `resolver.load_agent_tools` (`dispatch.py:243`).

#### Downstream filesystem reads (via `load_contract_for_step` → `parser._load_contract`)

All contract reads funnel through `parser.load_contract_for_step` / `_load_contract`. Relevant `open()` sites:

| File:line | Target | Notes |
|-----------|--------|-------|
| `parser.py:265` | `state_yaml_path` | `_contract_lookup_id` — reads workflow_plan to resolve `step_contract:` indirection for task nodes |
| `parser.py:304` | `<search_dir>/<lookup_id>/contract.yaml` | Directory-form contract |
| `parser.py:320` | `<contract_dir>/prompt.md` | Agent-kind only; missing file → `ContractError`, not `FileNotFoundError` |
| `parser.py:345` | `<search_dir>/<lookup_id>.yaml` | Legacy flat-file contract |
| `parser.py:334` | `os.path.isfile(run)` | Script payload existence check (no `open` until dispatch executes `run:`) |

Search dirs come from `_contract_search_dirs(state_yaml_path)` (repo `.orchestrator/steps/` then `$ORCHESTRATOR_HOME/config/steps/`).

#### Agent `.md` reads on the dispatch path

| File:line | Target | Caller | Behavior on missing file |
|-----------|--------|--------|---------------------------|
| `resolver.py:38–42` | `$ORCHESTRATOR_HOME/agents/<name>.md`, then `~/.claude/agents/<name>.md` | `dispatch._resolve_allowed_tools` (`dispatch.py:243`) | Returns `None`; stderr warning if `allowed_tools` set on contract (lines 246–251). **No** `ContractDispatchError`, **no** `/doctor` hint. |
| `parser.py:320` | `prompt.md` (step-local, not `agents/*.md`) | `_load_contract` for `kind: agent` | `ContractError` if missing |

Standalone `agents/<role>.md` files are **not** read by `dispatch.py` for instruction text — agent steps get `instruction` from `prompt.md` in the step directory. The `agents/*.md` read is **tools frontmatter only** via `resolver.py`.

#### `readiness.py` — contract read from dispatch call chain

| File:line | Mechanism | Behavior |
|-----------|-----------|----------|
| `readiness.py:142` | `load_contract_for_step(node_id, state_yaml_path)` inside `repeat_until_redispatch` | `except (FileNotFoundError, ContractError): repeat_until = None` — silently skips repeat semantics |

#### `dispatch.py` — `state.yaml` writes (not via `record.py`)

| File:line | Operation | Write-after-verify? |
|-----------|-----------|---------------------|
| `dispatch.py:289–303` | `_persist_node_status` — read bytes, mutate in memory, `yaml.safe_dump` to path, re-parse; on `yaml.YAMLError` restore `pre_bytes` | **Yes** (post-write parse + restore). **No** `tempfile` / `os.replace` atomic rename. |

#### `record.py` — `state.yaml` atomicity (AC-9 write-after-verify clause)

Verified with `grep -n 'os.replace\|os.rename\|tempfile' config/scripts/orchestrator_next/record.py` → **no matches**.

| File:line | Pattern | Atomic rename? |
|-----------|---------|----------------|
| `record.py:1490–1491` | Read `pre_write_bytes` before mutate | — |
| `record.py:1651–1652` | `open(path, "w")` + `yaml.safe_dump` | **No** — overwrites in place |
| `record.py:1654–1661` | Post-write `yaml.safe_load`; on failure restore `pre_write_bytes` | Verify-after-write **yes**; crash between truncate and dump can still corrupt (no temp+rename) |

Additional contract loads in `record.py` (not `dispatch.py`, but same `load_contract_for_step` primitive): `1385`, `1289`, `1320` — all catch `(FileNotFoundError, ContractError)` and degrade gracefully.

**Conclusion for AC-9 / T-5:** Pre/post YAML parse with byte restore satisfies *verify-after-write* for `record.py` and `_persist_node_status`. Neither uses atomic temp+rename. **Follow-up for T-5:** add explicit `ContractDispatchError` + `/doctor` hints at guarded read sites; consider unifying state writers on `tempfile` + `os.replace` if true crash-safe atomicity is required (optional hardening beyond current verify-restore).
