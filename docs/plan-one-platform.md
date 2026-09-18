# One platform: orchestrator + agentdos

**Recommendation.** Delete agentdos entirely. Shrink the engine from 16,573 py
lines to **~8,650** (48% cut; the ≤6,000 target is not reachable without
deleting working subsystems — see §2), and the Mod from 5,391 ts to **≤3,000**.
Add exactly one thing — a `decide` step kind, ~80 new lines — and only because
it deletes ~250 and removes an external SDK dependency.

**One surface (the Mod), one store, one recipe format, one settings file, one
decision mechanism.** Default on every question below is delete.

Status: proposal. Read-only survey, Sept 2026, orchestrator @ `simplify-v2`.

---

## 1. What each repo is

**orchestrator** (16,573 py + 5,391 ts). LLM-agnostic workflow engine; a CLI,
not a service. Protocol v2: `start` / `step` / `done` / `approve` / `resume` /
`status` / `events` (`cli.py:219-226`). Typed contracts of three kinds —
`exec`, `judgment`, `gate` (`parser.py:27-30`) — with `in:` / `out:` /
`side_effects:` / `pii:` / `validate:` (`parser.py:457-464`). An enum `out:`
may declare `fail_on:`, the only routing primitive (`parser.py:406-423`, routed
`record.py:237-251`). Gates mint a token and park the run `blocked`
(`gates.py:93-133`). `status --json` already emits per-node model, verdict,
attempts, seconds, tokens, `cost_usd` (`protocol.py:1191-1250`).

**agentdos** (~12,000 py). Hosted control plane: FastAPI + HTMX,
SQLite/Postgres, separate HTTP-polling worker. `app.py` alone is 7,485 lines
and 95 routes covering GitHub OAuth, workspaces, Stripe billing, a GitHub App,
a marketplace, MCP OAuth and admin review.

**Duplicated:** run store, run list, step loop with retries, model routing,
config files, verdict router, per-step metrics, graph rendering, a learner.
Two engines, one job. Delete the one that is not the product.

---

## 2. Minimal core

Smallest engine that runs a gated recipe with a decide step.

| Keep                                                                                                                                                                                                       | Now   | Target | Cut from it                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ------ | ------------------------------------------------- |
| `protocol.py`                                                                                                                                                                                              | 1,812 | 1,150  | `graph`/`recipes`/`events` verbs, exec batching   |
| `record.py`                                                                                                                                                                                                | 1,175 | 700    | 2 criteria dicts, judge triage, `_VERDICT_KEYS`   |
| `pack_export.py`                                                                                                                                                                                           | 1,012 | 600    | codex target                                      |
| `headless.py`                                                                                                                                                                                              | 826   | 450    | anthropic backend, `TOOL_DEFS`                    |
| `parser.py`                                                                                                                                                                                                | 776   | 620    | `_KIND_ALIASES`, `pii:`, `validate:`, legacy keys |
| `state_store.py`                                                                                                                                                                                           | 686   | 620    | `learn_results`, `tenant_id`; absorbs run locks   |
| `config_pull.py`                                                                                                                                                                                           | 671   | 600    | `require_signed`                                  |
| `doctor.py`                                                                                                                                                                                                | 567   | 400    | judge check, dropped-feature checks               |
| `execute.py`                                                                                                                                                                                               | 564   | 564    | —                                                 |
| `settings.py`                                                                                                                                                                                              | 431   | 250    | 10 of 18 keys                                     |
| `gates.py`                                                                                                                                                                                                 | 293   | 293    | —                                                 |
| `cli.py`                                                                                                                                                                                                   | 291   | 200    | 8 verbs                                           |
| 13 small modules (`paths`, `trust`, `pricing`, `model_routes`, `artifacts`, `seed`, `step_env`, `readiness`, `worktree_lock`, `validate_workflow`, `workflow_steps`, `generate_plan`, `publish_scenarios`) | 2,727 | 2,200  | fallback chains, stale flags                      |

**Total: ~8,650 py** (from 16,573, a 48% cut) **+ ≤3,000 ts** (from 5,391, by
dropping the Mod's duplicate metrics folding and width tiers beyond two).

I was asked to aim for ≤6,000 py and cannot honestly claim it. Module-by-module
targets sum to 8,650, and the gap is structural: `protocol.py` and `record.py`
alone are 1,850 after cuts, and `pack_export.py` survives only because a live
pack step calls it (§3). Reaching 6,000 would mean deleting the pack-export and
headless subsystems outright — a real option, but it removes working features
no evidence says are unwanted, so I am not recommending it. The 8,650 figure is
what the enumerated deletions actually produce.

**Optional, off by default:** TypeSafe as a `decide` backend (§4). Optional
means _not a dependency_.

---

## 3. Engine delete list

Every item evaluated and decided. Default was delete.

| Item                                                         | Decision                 | Reason                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------ | ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_store.py` (259)                                         | **DELETE**               | Second SQLite layer: `run_blobs`/`run_locks` (`run_store.py:63-68`) beside `state_store`'s `runs`/`step_history` (`state_store.py:275-315`). 8 call sites (`protocol.py:62,104,655,1388`, `record.py:889-896`, `report.py:310`, `doctor.py:388`). Fold locks in. |
| headless `anthropic` backend + `TOOL_DEFS`                   | **DELETE**               | Second execution path (`headless.py:230,507-567`) duplicating claude-cli. Drops `anthropic>=0.40` (`pyproject.toml:20,29`). ~350 lines.                                                                                                                          |
| `judge.py` (63) + `typesafe-sdk`                             | **DELETE as dependency** | `decide` runs on the step's own model via `--json-schema`, already passed by the CLI (`headless.py:347`). Drops `typesafe-sdk` (`pyproject.toml:23,30`). Keep a 20-line adapter as an _optional_ backend only on request.                                        |
| `report.py` (469)                                            | **DELETE**               | `status --json` + `jq` covers it; `node_metrics` folds the same history (`protocol.py:1110`).                                                                                                                                                                    |
| `graph.py` (80)                                              | **DELETE**               | Mermaid nobody reads in a terminal.                                                                                                                                                                                                                              |
| `publish_scenarios.py` (167)                                 | **KEEP**                 | Live consumer: `.orchestrator/workflows/steps/persist-learnings/script.sh:18` calls `pack publish-scenarios`. The only candidate that survived.                                                                                                                  |
| `learn_results` table                                        | **DELETE**               | Only `publish_scenarios` reads it (`state_store.py:644`); it can read `step_history`. Removes a table.                                                                                                                                                           |
| `reset-step` + `reset_step.py` (113)                         | **DELETE**               | `cancel` + `start` reaches the same state.                                                                                                                                                                                                                       |
| `state list\|show\|migrate\|project` (~100, `cli.py:92-170`) | **DELETE**               | Admin verbs over a store `sqlite3` already opens.                                                                                                                                                                                                                |
| `trust.require_signed`                                       | **DELETE**               | Stub: zero non-test readers.                                                                                                                                                                                                                                     |
| `pii:` contract key                                          | **DELETE**               | **Zero of 28 live contracts declare it.** Unused across 3 dataclasses (`parser.py:59,80,103`) plus `redact.py` (82).                                                                                                                                             |
| `validate:` contract key                                     | **DELETE**               | **Zero of 28 live contracts declare it.** Shell-out-after-artifacts nothing uses.                                                                                                                                                                                |
| `tenant_id`                                                  | **DELETE**               | Multi-tenancy for a single-user CLI (`state_store.py:290,313,332,343-348`). Drops a column, an index, a settings key.                                                                                                                                            |
| models.yaml fallback chains                                  | **DELETE**               | One id per alias; chain-walking is `model_routes.py:63-144`.                                                                                                                                                                                                     |
| `pack_export` codex target                                   | **DELETE**               | `pack_export.py:701-747`, ~400 lines for a second IDE format. Claude target stays.                                                                                                                                                                               |
| `run.stale_after_hours`, `--all` flags                       | **DELETE**               | Staleness heuristics over a list you can read.                                                                                                                                                                                                                   |
| `skills/orchestrate` SKILL.md driver                         | **DELETE**               | Mod is the only surface (§5); a second driver is a second protocol implementation to keep in sync.                                                                                                                                                               |
| 10 of 18 settings keys                                       | **DELETE**               | Keep `state.url`, `run.max_parallel`, `headless.backend`, `headless.step_budget_usd`, `trust.allow`, `trust.trust_all`, `models.config`, `backlog.url`.                                                                                                          |
| 8 of 20 verbs                                                | **DELETE**               | Keep `start step done status approve cancel resume headless doctor validate pack state`. Cut `run` (alias erroring without `--headless`, `cli.py:250-256`), `recipes`, `events`, `graph`, `report`, `reset-step`.                                                |

Also fix: README says `step_budget_usd = 0.50` (`README.md:196`), code says
`0.0` (`settings.py:83`).

---

## 4. The one addition: `decide`

TypeSafe is **orchestrator's, not agentdos's** — grepping agentdos returns zero
hits. `judge.py` wraps the SDK (`judge.py:17-63`) with two call sites, both
**post-hoc triage, never routing**: `record.py:609-650` classifies why a step
abandoned; `record.py:776-800` back-fills `issue["kind"]`. agentdos does the
_routing_ by prose: stdout scanned for literal `VERDICT: PASS` / `VERDICT:
FAIL` (`core/runner.py:136-137,220-227`, case-sensitive) plus `ROUTE:`
(`core/runner.py:230-239`). A typed classifier used only for triage, and
routing by string-matching prose.

```yaml
id: review-design
kind: decide
prompt: SKILL.md
out:
  verdict:
    type: enum
    values: [approve, needs_work]
    fail_on: [needs_work]
    min_confidence: 0.7
```

`decide` is a judgment step whose `out` is one enum plus confidence. Routing on
`fail_on:` already exists (`record.py:237-251`) and is untouched.

**New code, ≤80 lines:** add `decide` to `VALID_KINDS` / `_resolve_kind` (~15,
`parser.py:27-30,430`); emit the enum as a JSON schema for the CLI's existing
`--json-schema` flag (~25, `headless.py:347`); validate the returned value
against `values:` (~20); one confidence branch parking the run through existing
gate machinery (~20).

**Deletes ~250:** `_ABANDON_TRIAGE_CRITERIA` and its retry path
(`record.py:600-650`), `_ISSUE_KIND_CRITERIA` and `_classify_issues`
(`record.py:770-800`), `_VERDICT_KEYS` (`protocol.py:1017`), `judge.py` (63),
and the `typesafe-sdk` dependency. `judgment` folds into `decide`, so kinds
stay at three. No `route:` map: it reintroduces graph edges and cycle
detection; `fail_on` + `on_failure` suffices.

Net **−170 lines and one fewer external dependency.**

---

## 5. Surfaces: one

**The Claude Code Mod, alone.** Not CLI-plus-Mod-plus-Desktop, and not the
`skills/orchestrate` driver.

**Desktop/MCP is a later phase, only if a non-Claude-Code user appears.** MCP
Apps (SEP-1865) reached Final on 2026-01-26 with Claude desktop support
([SEP](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp),
[announcement](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/)),
packaged as `.mcpb`
([docs](https://claude.com/docs/connectors/custom/desktop-extensions)) — but
`.mcpb` is Team/Enterprise only, and a second surface means a second run-table
renderer and approve path against an early-access API that "may change without
notice" (`docs/claude-mod-api-notes.md:3`). If it ever lands: six tools over
existing verbs plus one `ui://orchestrator/runs` App, not a pane port.

---

## 6. Pain points

| Pain point                           | Evidence                                                                                                                                            | Answer                                                          |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Abstraction overhead; 1–2 week curve | [langchain](https://www.langchain.com/resources/ai-agent-frameworks), [firecrawl](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks) | A recipe is a flat list. No graph DSL.                          |
| Opaque prompts                       | [dev.to](https://dev.to/suifeng023/crewai-vs-langgraph-which-llm-agent-framework-should-you-use-in-2026-3h4n)                                       | The prompt is a file in the pack, versioned in the repo.        |
| Config sprawl                        | [datastackx](https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb)                                   | One recipe format, one TOML, eight keys.                        |
| Non-determinism                      | [mlflow](https://mlflow.org/articles/what-is-agent-observability-a-2026-developer-guide/)                                                           | `decide` bounds the model to one declared enum value.           |
| Silent failure                       | [openhands](https://www.openhands.dev/blog/ai-agent-observability)                                                                                  | `fail_on:` makes a bad verdict a run failure (`record.py:211`). |
| Cost visibility                      | [mlflow](https://mlflow.org/articles/top-llm-observability-tools-in-2026-a-pro-guide/)                                                              | Per-node `cost_usd` already in `status --json`.                 |
| Human-in-the-loop                    | [mlflow](https://mlflow.org/articles/what-is-agent-observability-a-2026-developer-guide/)                                                           | Gates exist; `min_confidence` reuses them.                      |
| ~15% instrument anything             | [moderndata101](https://www.moderndata101.com/blogs/top-trends-of-enterprise-ai-observability)                                                      | Not opt-in: `step_history` is also the resume mechanism.        |

**Not addressed, deliberately:** durable multi-machine execution, and replay of
a run whose code changed underneath it.

---

## 7. Runs, logs, metrics

The schema is what `status --json` emits today (`protocol.py:1213-1245`) and
needs **no new fields**: per step `id`, `phase`, `kind`, `status`, `attempts`,
`model`, `verdict`, `seconds`, token counts, `cost_usd`, `cost_partial`,
`artifacts`; per run nodes, totals, gate state. agentdos's per-step log dict is
the same shape down to the key names (`tests/test_metrics.py:37-56`), so
nothing migrates.

**Adopt one derived field:** `passed_first_try` (`core/learner.py:660-666`) —
one boolean over existing history, and the number that says a recipe is badly
written rather than merely slow. Drop `routed_back`: `attempts` reports it.

agentdos deleted its own aggregate metrics page for Langfuse
(`tests/test_metrics.py:1-8`). Don't rebuild it; tracing stays external.

---

## 8. Migration plan

| #   | Phase                                                                                                                                                                                                                                        | Exit check                                                                                                                                               |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Safe deletions.** Archive agentdos. Delete `report.py`, `graph.py`, `reset_step.py`, `state` admin verbs, anthropic backend, codex target, `pii:`, `validate:`, `tenant_id`, `require_signed`, fallback chains, 10 settings keys, 8 verbs. | Tests green; `--help` lists 12 verbs; `anthropic` gone from `pyproject.toml`; engine ≤11,000 lines.                                                      |
| 2   | **`decide` + verdict cleanup.** Add the kind (~80 lines); delete both criteria dicts, `_VERDICT_KEYS`, `judge.py`.                                                                                                                           | A `decide` contract routes both ways; low confidence parks the run; all 28 existing contracts load unchanged; `typesafe-sdk` gone from `pyproject.toml`. |
| 3   | **Store collapse.** Fold `run_locks` into `state_store`; delete `run_store.py` and `learn_results`.                                                                                                                                          | One DB file, three tables; `publish-scenarios` still works off `step_history`; engine ≤8,650 lines.                                                      |
| 4   | **Authoring UX.** `orchestrator new step --kind decide`; `validate --json` in the Mod.                                                                                                                                                       | A scaffolded recipe runs unedited; a bad `fail_on:` value fails `validate` with a file:line message.                                                     |
| —   | _Later, only on demand:_ Desktop/MCP surface.                                                                                                                                                                                                | A non-Claude-Code user asks.                                                                                                                             |

Phase 1 is pure subtraction and lands most of the value. Only phase 2 adds code.

**agentdos delete list — the whole repo.** Notably `app.py` (7,485),
`worker.py` (903), `core/learner.py` (1,261), `core/harnesses.py` (1,229),
`db.py` (1,098), `inbound.py` (821), `billing.py` (468), plus
`core/{compile,engine,runner,router,github_app,marketplace}.py`, `registry/`,
`templates/`, `Dockerfile`, `railway.toml`, `vercel.json`, `api/index.py`.

---

## 9. Risks and open questions

- **Deleting agentdos removes multi-machine execution and billing.** Nothing
  replaces the HTTP worker pool or prepaid wallet. Confirm before phase 1; the
  one irreversible step.
- **`pii:` and `validate:` are declared-but-unused, not wrong.** Zero of 28
  contracts use them, but they were built for a compliance case that may be
  coming. Deleting is right today; say so out loud.
- **Dropping the anthropic backend bets on `claude -p`.** If the CLI is absent
  in CI, headless has no fallback.
- **`--json-schema` must actually constrain output.** Phase 2 rests on it
  (`headless.py:347`); verify the CLI enforces the enum before deleting
  `judge.py`.
- **Confidence is uncalibrated.** The `0.7` at `record.py:634` is a guess.
- **`report.py` deletion assumes `status --json` + `jq` suffices.** Verify no
  consumer parses `report --all`.
- **Cutting `tenant_id` assumes single-tenant forever.** A door closing.
- **The Mod becomes a single point of failure** against an early-access API,
  and `skills/orchestrate` was the fallback being deleted alongside it.

---

[SEP-1865](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp) ·
[MCP Apps](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/) ·
[Desktop extensions](https://claude.com/docs/connectors/custom/desktop-extensions) ·
[LangChain](https://www.langchain.com/resources/ai-agent-frameworks) ·
[CrewAI vs LangGraph](https://dev.to/suifeng023/crewai-vs-langgraph-which-llm-agent-framework-should-you-use-in-2026-3h4n) ·
[Open source frameworks](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks) ·
[Airflow/Prefect/Dagster](https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb) ·
[Agent observability](https://mlflow.org/articles/what-is-agent-observability-a-2026-developer-guide/) ·
[LLM observability](https://mlflow.org/articles/top-llm-observability-tools-in-2026-a-pro-guide/) ·
[Observability at scale](https://www.openhands.dev/blog/ai-agent-observability) ·
[Enterprise trends](https://www.moderndata101.com/blogs/top-trends-of-enterprise-ai-observability)
