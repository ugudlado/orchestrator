# Fix Plan: Live cost telemetry + repeat_until enforcement

Change-id: `live-telemetry-and-repeat-until-enforcement`
Issues: ISSUE-16 (dispatcher ignores `repeat_until`), ISSUE-17 (`cost_usd` NULL for native agents)
Complexity: **S** (two surgical patches to `record.py` + its parser, plus tests)

---

## ISSUE-16 — Enforce `repeat_until: all_tasks_completed`

### Approaches considered

1. **Parse `repeat_until` from the step contract** (`config/steps/execute-next-task.yaml`); extend `StepContract`; add a predicate registry in `record.py`; re-emit the same step when the predicate is unsatisfied. *Complexity: S. Touches record.py + parser.py + one contract YAML.*
2. **Read `repeat_until` straight from `config/workflows/<workflow>.yaml`** at record time. *Complexity: M. Introduces a new runtime dependency (record.py loads workflow files), breaks the current boundary where only dispatch/resolve touch workflow YAML, and needs workflow-file path resolution.*
3. **Materialize `repeat_until` into `state.yaml.workflow_plan.active[]`** as dict entries `{id: execute-next-task, repeat_until: all_tasks_completed}` in the plan-builder. *Complexity: M. Requires changing the plan-builder (location not in Python; would ripple through tests and existing state files).*

### Selected: Approach 1

**Rationale** — simplicity + locality. The step contract already owns the semantic (`verify.repeat_done_when`). Adding a top-level `repeat_until: all_tasks_completed` key on the contract (next to `id:`, `agent:`) makes the declaration authoritative *at the step* — which is where `_compute_next_step` already looks. No new runtime file reads, no plan-builder changes, no state-file migrations. The workflow-schema annotation in `config/workflows/bugfix.yaml:113` becomes redundant but is left in place as documentation (no behavioural break).

**Predicate vocabulary** — start with exactly one predicate: `all_tasks_completed`. Unknown predicates log a warning and fall through to the current advance-behaviour so new workflows don't silently stall. The registry is a dict `{"all_tasks_completed": _check_all_tasks_completed}` in `record.py`; future predicates append a line. No plugin system, no config, no over-generalisation.

### Affected files

- `config/scripts/orchestrator_next/parser.py` — add `repeat_until: str | None = None` to `StepContract`; read `data.get("repeat_until")` in `_load_contract`.
- `config/scripts/orchestrator_next/record.py`:
  - Add helper `_resolve_tasks_md(state_raw) -> Path | None` — prefers `state_raw["tasks_path"]` (test override), else `<worktree_path>/spec/changes/<change_id>/tasks.md`.
  - Add helper `_check_all_tasks_completed(state_raw) -> bool` — returns True when the resolved tasks.md has zero `- [ ]` lines (or file missing → True, so the loop doesn't wedge in test fixtures without tasks.md).
  - Add predicate registry `_REPEAT_PREDICATES = {"all_tasks_completed": _check_all_tasks_completed}`.
  - Modify `_compute_next_step`: before marking `just_completed_step_id` as completed, look up its contract; if `contract.repeat_until` names a known predicate and the predicate returns False, return `{"phase": phase, "step_id": just_completed_step_id}` (re-emit).
- `config/steps/execute-next-task.yaml` — add top-level `repeat_until: all_tasks_completed` key.

### Risk assessment

- **Other callers of `_compute_next_step`**: only `record.record()` calls it. `dispatch.py:_find_completed_step` is read-only and uses the already-written `next_step`. Once record.py re-emits correctly, dispatch sees the correct value. No dispatch changes needed.
- **Contracts without `repeat_until`**: `StepContract.repeat_until` defaults to None; `_compute_next_step` skips the predicate check entirely. No behaviour change for the ~30 other step contracts.
- **Missing tasks.md** (fresh workflow before tasks.md is written): `_check_all_tasks_completed` returns True (no unchecked lines) → step advances. This matches current behaviour; no regression.
- **Unknown predicate name in some future contract**: log a single warning to stderr and advance. Fail-open, not fail-closed — avoids stalling on a typo.
- **Contract loading failure** inside `_compute_next_step`: already handled — the existing `load_contract_for_step` try/except pattern in `record()` wraps contract loads; we reuse that and treat "no contract" as "no repeat_until".

### Minimal-fix check

Smallest change that honours `repeat_until`:
- 1 new optional field on a dataclass.
- 1 new dict literal (`_REPEAT_PREDICATES`).
- 2 small helpers (`_resolve_tasks_md`, `_check_all_tasks_completed`) totalling ~20 lines.
- 1 contract YAML edit (add one line).
- 1 `if` block inside `_compute_next_step`.

No refactor of `_compute_next_step`, no changes to `parser.load_state`, no plan-builder changes, no new runtime dependencies.

---

## ISSUE-17 — Compute `cost_usd` when `model` is absent from the agent usage block

### Approaches considered

1. **Compute `cost_usd` in `record.py`** when absent: resolve `payload.agent` → model_id via a new `backend → model_id` table in `scripts/routes.yaml`, resolve model → price via `config/pricing.yaml`, write the computed value back into the `usage` dict before history write. *Complexity: S. One helper + one call site in `record.py`. All telemetry (state.yaml + DB) gets live cost.*
2. **Compute `cost_usd` in `upsert.py`** at DB-insert time. *Complexity: S. But state.yaml itself still holds NULL cost — divergence between sources of truth. Also, `step_events` is the DB; the YAML is the durable log read by humans and CLI tools.*
3. **Hardcode `native_sonnet` / `native_opus` → model_id in `record.py`**. *Complexity: XS. But now pricing data location becomes asymmetric — backend list lives in code, model list in YAML. Adds drift risk when a new `native_*` backend appears.*

### Selected: Approach 1

**Rationale** — compute at the write boundary (`record.py`) so state.yaml and step_events agree. Keep the backend→model mapping in data (`routes.yaml`) alongside the agent→backend mapping already there. This is the same "single source of truth" rationale that put pricing in `pricing.yaml`.

**Mapping location** — add a sibling `backends:` section to `scripts/routes.yaml`:

```yaml
backends:
  native_opus:   claude-opus-4-7
  native_sonnet: claude-sonnet-4-6
```

This is the minimum needed for the existing two backends. Future backends append one line. `qwen`, `qwen-local`, `klm-local` already have `.model` fields in the `models:` block — no mapping entry needed for those (resolver falls through to `models.<backend>.model`).

**Cache tokens** — include `cache_read_input_tokens × pricing.cache_read / 1e6` when both are present (`pricing.yaml` already has `cache_read` rates). Unknown-model fallback uses `pricing.default`. Missing tokens default to 0. Missing `agent` → skip computation (leave `cost_usd` unset).

### Affected files

- `scripts/routes.yaml` — add top-level `backends:` block mapping `native_opus` / `native_sonnet` to the current model-ids.
- `config/scripts/orchestrator_next/record.py`:
  - Add `_load_routes()` and `_load_pricing()` helpers with `@functools.lru_cache` (one read per process).
  - Add `_compute_cost_usd(agent, usage) -> float | None` — returns None if any required datum is missing; logs a single warning to stderr on unresolved agent or missing price entry.
  - Before writing the `entry` in `record()`, if `payload.agent` is present and `usage.get("cost_usd")` is falsy, call `_compute_cost_usd` and, when it returns a value, also set `usage["model"] = <resolved model_id>` and `usage["cost_usd"] = <computed>`.
- No changes to `upsert.py` — it already reads `usage.model` and `usage.cost_usd`; once record.py populates them, the DB path is unchanged.

### Risk assessment

- **Double-count when `cost_usd` is already present** (e.g. LiteLLM proxy path): guarded by the `usage.get("cost_usd")` truthy check. LiteLLM values pass through untouched.
- **Unresolvable agent** (e.g. `inline`): `_compute_cost_usd` returns None; no write; `cost_usd` stays NULL as today. Matches current behaviour — no regression.
- **Price table drift**: pricing.yaml is the single source. The fallback `default` entry (opus-tier) prevents silent zero when a new model id shows up — it slightly over-reports rather than under-reporting.
- **LRU cache across test runs**: use `functools.lru_cache(maxsize=1)` on parameterless loaders reading from a fixed path (`ORCHESTRATOR_HOME`). Tests can `_load_routes.cache_clear()` if needed. No test interference in normal runs.
- **Consumers of `step_events.model`**: `cost_report.py` groups/filters by model — populating it for native agents increases fidelity, does not break schema.

### Minimal-fix check

Smallest change that closes the gap:
- 1 new data block in an existing YAML (`routes.yaml.backends`).
- 2 cached loaders + 1 compute helper (~25 lines).
- 1 call site in `record()` (4 lines).

No upsert.py change, no DB schema change, no cost_report.py change, no new dependency, no refactor of existing cost code in `estimate-cost.sh` / `compute-swe-metrics.sh` (those already read `pricing.yaml` directly and are out of scope).

---

## Cross-cutting: test surface

Both issues have dedicated repros (`.tmp/repro-issue-16.py`, `.tmp/repro-issue-17.py`). The fix tasks add pytest regression tests that mirror these repros inside `config/scripts/orchestrator_next/tests/`. The repros themselves are kept as end-to-end smoke scripts and are re-run in the verification task; per TDD rule the pytest tests are written first and must fail before the fix lands.

## Non-goals

- No refactor of `_compute_next_step` control flow beyond the new branch.
- No migration of historical `step_events.cost_usd` NULL rows — only prospective records are fixed. (A backfill would be a separate change.)
- No new predicate beyond `all_tasks_completed`. Autopilot's `all_iterations_completed` is mentioned in the diagnosis as affected by the same missing machinery but is explicitly out of scope — it does not block the current reported failure and would double the surface area of this fix.
