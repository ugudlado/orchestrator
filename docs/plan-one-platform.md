# One platform: orchestrator + agentdos

**Recommendation.** Delete agentdos entirely. Keep orchestrator, and shrink it:
collapse two state stores into one, twenty verbs into twelve, eighteen settings
keys into eight, and two UI surfaces into one. Add exactly one thing — a
`decide` step kind — and only because it deletes more than it adds: two
classification code paths, one prose-parsing convention, and a step kind.

Net: **−12,000 agentdos, −1,700 engine modules, −250 inline, +~150.**

Status: proposal. Read-only survey, Sept 2026, orchestrator @ `simplify-v2`.

---

## 1. Minimal core

The smallest engine that still runs a recipe with gates and a decide step.
Everything else is optional or deleted.

| Core module      | Now   | Target | What goes                                    |
| ---------------- | ----- | ------ | -------------------------------------------- |
| `protocol.py`    | 1,812 | ~1,100 | `graph`, `recipes`, `events` verbs; batching |
| `record.py`      | 1,175 | ~700   | two judge criteria dicts, triage retry path  |
| `parser.py`      | 776   | ~650   | `_KIND_ALIASES`, legacy contract keys        |
| `state_store.py` | 686   | ~600   | absorbs run_store's lock table               |
| `execute.py`     | 564   | 564    | —                                            |
| `gates.py`       | 293   | 293    | —                                            |
| `settings.py`    | 431   | ~250   | 10 of 18 keys                                |
| `cli.py`         | 291   | ~200   | 8 verbs                                      |

**Minimal core ≈ 4,400 lines** (from 16,573). It runs `start`, `step`, `done`,
`approve`, `status`, `headless` — a recipe with gates and decide nodes, end to
end, with metrics.

**Outside the core — delete** (1,709 lines): `run_store.py` (259), `graph.py`
(80), `report.py` (469), `publish_scenarios.py` (167), `generate_plan.py`
(205), `init_wizard.py` (265), `reset_step.py` (113), `models_config_cli.py`
(49), `settings_cli.py` (102).

**Optional — keep, do not extend:** `headless.py`, `doctor.py`,
`config_pull.py`, `pack_export.py` (1,012 — `config_pull.py:399-409` and
`doctor.py:464` import it; shrink with the pack, don't delete), `pricing.py`,
`model_routes.py`, `trust.py`, `redact.py`, `artifacts.py`.

---

## 2. What each repo is

**orchestrator** (16,573 py lines). LLM-agnostic workflow engine; a CLI, not a
service. Protocol v2: `start` / `step` / `done` / `approve` / `resume` /
`status` / `events` (`cli.py:219-226`, impls `protocol.py:428-1642`). Typed
contracts of three kinds — `exec`, `judgment`, `gate` (`parser.py:27-30`) —
with `in:` / `out:` / `side_effects:` / `pii:` / `validate:`
(`parser.py:457-464`). An enum `out:` may declare `fail_on:`, the only routing
primitive (`parser.py:406-423`, routed `record.py:237-251`). Gates mint a token
and park the run `blocked` (`gates.py:93-133`). `status --json` already emits
per-node model, verdict, attempts, seconds, tokens, `cost_usd`
(`protocol.py:1191-1250`). Surfaces: CLI plus a 5,391-line TypeScript Mod.

**agentdos** (~12,000 py lines). Hosted control plane: FastAPI + HTMX,
SQLite/Postgres, a separate HTTP-polling worker. Outline → compiler → graph →
worker → evaluator → learner. `app.py` alone is 7,485 lines and 95 routes
covering GitHub OAuth, workspaces, Stripe billing, a GitHub App, a marketplace,
MCP OAuth and admin review.

**Duplicated:** run store, run list, step loop with retries, model routing,
config files, verdict router, per-step metrics, graph rendering, a learner.

---

## 3. The one addition: `decide`

### What exists today

The TypeSafe integration is **orchestrator's, not agentdos's** — grepping
agentdos for `typesafe` returns zero hits. `judge.py` (63 lines) wraps the SDK:
`enabled()` needs `ORCHESTRATOR_JUDGE != "off"`, `TYPESAFE_API_KEY` and
`typesafe_sdk` importable (`judge.py:17-28`); `ask()` returns `None` on every
failure, the entire fallback switch (`judge.py:51-63`). Two call sites, both
**post-hoc triage, never routing**: `record.py:609-650` classifies why a step
abandoned, resetting the node when `transient` clears 0.7 confidence;
`record.py:776-800` back-fills `issue["kind"]`.

agentdos does the _routing_ job with prose parsing: stdout scanned for literal
`VERDICT: PASS` / `VERDICT: FAIL` (`core/runner.py:136-137`, `220-227`,
case-sensitive) plus `ROUTE: <target>` (`core/runner.py:230-239`), routed at
`core/compile.py:314-330`.

So: a typed classifier used only for triage, and routing by string-matching
prose. Two mechanisms, neither doing the other's job.

### The contract

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

**Routing rule** in `record.py`, in order:

1. Value in `fail_on:` and confidence sufficient → existing failure path,
   identical to `record.py:237-251`. Existing packs are unaffected.
2. Confidence below `min_confidence` → park `blocked` via `gates.py`. Low
   confidence _is_ the human-in-the-loop trigger; no new mechanism.
3. Otherwise advance.

Backend is `judge.ask()` when enabled, else the step's own model returning one
JSON object constrained to the declared `values:`. Because `values:` is
declared, the engine validates rather than trusts.

**No `route:` map.** I proposed one in an earlier draft and am cutting it: it
reintroduces arbitrary graph edges, needs cycle detection, and `fail_on:` plus
`on_failure` already covers the real case. A recipe stays a list.

### What `decide` deletes

- `_ABANDON_TRIAGE_CRITERIA` and its retry path (`record.py:600-650`)
- `_ISSUE_KIND_CRITERIA` and `_classify_issues` (`record.py:770-800`)
- `_VERDICT_KEYS` prose fallback (`protocol.py:1017`)
- the `judgment` kind — `decide` replaces it, so kinds stay at three
- in agentdos: `_parse_verdict`, `_parse_route_to`, the whole `VERDICT:` /
  `ROUTE:` convention

Roughly **−250 lines for +150.**

---

## 4. Pain points

| Pain point                                                 | Evidence                                                                                                                                            | Answer                                                             |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Abstraction overhead; 1–2 week learning curve              | [langchain](https://www.langchain.com/resources/ai-agent-frameworks), [firecrawl](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks) | A recipe is a flat list. No graph DSL; `route:` cut to keep it so. |
| Opaque prompts; can't see what reached the LLM             | [dev.to](https://dev.to/suifeng023/crewai-vs-langgraph-which-llm-agent-framework-should-you-use-in-2026-3h4n)                                       | The prompt is a file in the pack, versioned in the repo.           |
| Config sprawl: code-first vs YAML                          | [datastackx](https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb)                                   | One recipe format, one TOML, eight keys.                           |
| Non-determinism; one happy-path trace insufficient         | [mlflow](https://mlflow.org/articles/what-is-agent-observability-a-2026-developer-guide/)                                                           | `decide` bounds the model to one declared enum value.              |
| Silent failure; success reported despite failed validation | [openhands](https://www.openhands.dev/blog/ai-agent-observability)                                                                                  | `fail_on:` makes a bad verdict a run failure (`record.py:211`).    |
| Cost visibility / FinOps                                   | [mlflow](https://mlflow.org/articles/top-llm-observability-tools-in-2026-a-pro-guide/)                                                              | Per-node `cost_usd` already in `status --json`.                    |
| Human-in-the-loop UX                                       | [mlflow](https://mlflow.org/articles/what-is-agent-observability-a-2026-developer-guide/)                                                           | Gates exist; `min_confidence` reuses them.                         |
| ~15% of deployments instrument anything                    | [moderndata101](https://www.moderndata101.com/blogs/top-trends-of-enterprise-ai-observability)                                                      | Not opt-in: `step_history` is also the resume mechanism.           |

**Not addressed, deliberately:** durable multi-machine execution, and replay of
a run whose code changed underneath it.

---

## 5. One of everything

**One store.** Two SQLite layers exist today: `state_store.py` with `runs` /
`step_history` / `learn_results` (`state_store.py:275-329`) and `run_store.py`
with `run_blobs` / `run_locks` (`run_store.py:63-68`). Both are live
(`protocol.py:62,104,655,1388`, `record.py:889-896`, `report.py:310`,
`doctor.py:388`). **Delete `run_store.py`;** move the lock table into
`state_store.py`. Five tables become four, two layers one.

**One surface: the Claude Code Mod.** Drop the Desktop MCP server from the
plan. MCP Apps (SEP-1865) reached Final on 2026-01-26 with Claude desktop
support ([SEP](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp),
[announcement](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/)),
and `.mcpb` is the documented packaging
([docs](https://claude.com/docs/connectors/custom/desktop-extensions)) — but
`.mcpb` is Team/Enterprise only, and a second surface means a second run-table
renderer, a second approve path and a second thing to maintain against an
early-access API that "may change without notice"
(`docs/claude-mod-api-notes.md:3`). The Mod already does Home, run view and
logs. **One surface suffices.** Revisit only if approvals off-laptop turn out
to matter; sketch in §8.

**One settings file, eight keys.** Of the 18 in `settings.py:66-104`, keep
`state.url`, `run.max_parallel`, `headless.backend`, `headless.step_budget_usd`,
`trust.allow`, `trust.trust_all`, `models.config`, `backlog.url`. Delete
`state.backend` (inferable from the URL scheme), `state.tenant`,
`run.stale_after_hours`, `run.disable_worktree_lock`, `headless.claude_bin`,
`backlog.project`, `backlog.token_env`, `trust.require_signed`,
`models.route_overrides`. Also fix: README says `step_budget_usd = 0.50`
(`README.md:196`), code says `0.0`.

**Twelve verbs.** Keep `start`, `step`, `done`, `status`, `approve`, `cancel`,
`resume`, `headless`, `doctor`, `validate`, `pack`, `state`. Delete `run`
(an alias that errors without `--headless`, `cli.py:250-256`), `recipes`,
`events` (fold into `status`), `graph` (`graph.py`, 80 lines of Mermaid),
`report` (`report.py`, 469 lines — `status --json` plus `jq`), `reset-step`
(`resume` covers it), and `validate-workflow` → rename `validate`.

**One recipe format.** Already true. Keep it that way.

---

## 6. Runs, logs, metrics

The schema is what `status --json` emits today (`protocol.py:1213-1245`) and
needs **no new fields**. Per step: `id`, `phase`, `kind`, `status`, `attempts`,
`model`, `verdict`, `seconds`, token counts, `cost_usd`, `cost_partial`,
`artifacts`. Per run: nodes, usage totals, gate state. agentdos's per-step log
dict is the same shape down to the key names (`tests/test_metrics.py:37-56`),
so nothing migrates.

**Adopt one derived field, not two.** `passed_first_try` — was the step's first
verdict a pass (`core/learner.py:660-666`). One boolean over existing history,
and the number that says a recipe is badly written rather than merely slow.
**Drop `routed_back`**: with `route:` cut it only counts `fail_on:` re-queues,
which `attempts` already reports.

agentdos deleted its own aggregate metrics page in favour of Langfuse
(`tests/test_metrics.py:1-8`). Don't rebuild it; tracing stays external.

---

## 7. Migration plan

| #   | Phase                                                                                                         | Exit check                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 1   | **Delete agentdos.** Archive the repo. Nothing is ported in this phase.                                       | No workload depends on it; `git log` shows the archive commit.                                     |
| 2   | **Collapse the stores.** Move `run_locks` into `state_store.py`, delete `run_store.py` and its 8 call sites.  | Test suite green; `run_blobs` no longer exists; one DB file.                                       |
| 3   | **Cut verbs and keys.** 20 verbs → 12, 18 settings keys → 8, delete `graph.py` / `report.py` / the CLI shims. | `orchestrator --help` lists 12; `settings.py` SCHEMA has 8 entries.                                |
| 4   | **`decide` kind.** Add it, fold `judgment` into it, delete both criteria dicts and the prose fallback.        | A `decide` contract routes both ways; low confidence parks the run; existing packs load unchanged. |
| 5   | **`passed_first_try`.** Derive in `status --json`; show on Mod Home.                                          | Reported correctly for a run with one retried step.                                                |

Value lands in phase 1: the largest deletion is also the easiest. Phases 2–3
are pure subtraction with no design risk. Only phase 4 adds code.

### Delete list

**agentdos — the whole repo.** Notably `app.py` (7,485), `worker.py` (903),
`core/learner.py` (1,261), `core/harnesses.py` (1,229), `db.py` (1,098),
`inbound.py` (821), `billing.py` (468), plus `core/{compile,engine,runner,
router,github_app,marketplace}.py`, `registry/`, `templates/`, `Dockerfile`,
`railway.toml`, `vercel.json`, `api/index.py`. Keep `core/learner.py:660-666`
as reference for one boolean, then drop it.

**orchestrator:** the nine modules in §1 (1,709 lines); `_KIND_ALIASES`
(`parser.py:35`); `_VERDICT_KEYS` (`protocol.py:1017`);
`_ABANDON_TRIAGE_CRITERIA` (`record.py:600-608`); `_ISSUE_KIND_CRITERIA`
(`record.py:770-779`); ten settings keys; eight verbs; the `config init` alias.

---

## 8. Risks and open questions

- **Deleting agentdos removes multi-machine execution and billing.** Nothing
  replaces the HTTP worker pool or prepaid wallet. Confirm no workload needs
  either before phase 1; this is the one irreversible step.
- **No Desktop surface means no off-laptop approvals.** If that matters, the
  minimal answer is six MCP tools (`runs`, `status`, `events`, `start`,
  `approve`, `recipes`) plus one `ui://orchestrator/runs` App — not a pane port.
- **`report.py` deletion assumes `status --json` plus `jq` suffices.** Verify no
  consumer parses `report --all` first.
- **Confidence is uncalibrated.** The `0.7` at `record.py:634` is a guess; a
  `min_confidence` gating escalation needs evidence before it defaults.
- **TypeSafe as silent fallback.** If `decide` falls back to a plain model, two
  runs of one recipe may route differently. Decide whether `backend: typesafe`
  is ever a hard requirement.
- **Unverified:** whether any consumer pack sets `ORCHESTRATOR_JUDGE` or has
  `typesafe_sdk` installed, so the judge's real hit rate is unknown.
- **The Mod stays 5,391 lines of TypeScript** against an early-access API, and
  is now the _only_ surface — higher single-point risk, lower total cost.
- **Cutting `state.tenant` assumes single-tenant forever.** A door closing.

---

[SEP-1865](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp) ·
[MCP Apps](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/) ·
[Desktop extensions](https://claude.com/docs/connectors/custom/desktop-extensions) ·
[mcpb](https://github.com/modelcontextprotocol/mcpb) ·
[LangChain frameworks](https://www.langchain.com/resources/ai-agent-frameworks) ·
[CrewAI vs LangGraph](https://dev.to/suifeng023/crewai-vs-langgraph-which-llm-agent-framework-should-you-use-in-2026-3h4n) ·
[Open source frameworks](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks) ·
[Airflow/Prefect/Dagster](https://dev.to/datastackx/airflow-vs-prefect-vs-dagster-picking-the-right-orchestrator-in-2026-1ifb) ·
[Agent observability](https://mlflow.org/articles/what-is-agent-observability-a-2026-developer-guide/) ·
[LLM observability tools](https://mlflow.org/articles/top-llm-observability-tools-in-2026-a-pro-guide/) ·
[Observability at scale](https://www.openhands.dev/blog/ai-agent-observability) ·
[Enterprise trends](https://www.moderndata101.com/blogs/top-trends-of-enterprise-ai-observability)
