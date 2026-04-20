# Diagnosis: Live cost telemetry + repeat_until enforcement (ISSUE-16 + ISSUE-17)

<!-- Format contract: contracts/artifact-formats.md § Diagnosis Format Contract -->

---

## ISSUE-16: Dispatcher ignores `repeat_until: all_tasks_completed`

### Symptoms

After recording `execute-next-task` as completed for a single task, `orchestrator next` returns `run-phase-review` (the next step in the phase's `active[]` list) even when `tasks.md` still contains unchecked `- [ ]` items. During the `fix-workflow-issues-2026-04-19` run, the driver had to manually rewrite `next_step.step_id` back to `execute-next-task` after each task batch to prevent autopilot from skipping remaining tasks. In full autopilot mode this would silently mark the workflow complete while tasks remain.

### Reproduction Steps

1. From the worktree root, run:
   ```
   python3 .tmp/repro-issue-16.py
   ```
2. The script builds a minimal `state.yaml` with `active: [execute-next-task, run-phase-review]` and a `tasks.md` with 2 unchecked items.
3. It calls `record()` with `step_id=execute-next-task, status=completed`.
4. Observed failure: `next_step = {"phase": "implement", "step_id": "run-phase-review"}` — skipping past execute-next-task despite unchecked tasks.

Expected output: `next_step.step_id == "execute-next-task"` (repeat).
Actual output: `next_step.step_id == "run-phase-review"` (advance). Exit code 1.

### Expected vs Actual

- **Expected**: `_compute_next_step` in `record.py` checks the step contract's `repeat_until` predicate; when `all_tasks_completed` is not yet satisfied (unchecked tasks remain in `tasks.md`), it re-emits the same step as `next_step`.
- **Actual**: `_compute_next_step` advances by position through `active[]`, treating any step with a `completed` entry in `step_history` as done. Since `execute-next-task` just completed once, it is skipped regardless of remaining tasks.

### Investigation

#### Evidence Gathered

- `config/scripts/orchestrator_next/record.py:25-49` — `_compute_next_step` iterates `active[]` and skips any step_id in the `completed` set. No reference to `repeat_until`, `repeat_done_when`, or `tasks.md`.
- `config/workflows/bugfix.yaml:112-113` — schema declares `repeat_until: all_tasks_completed` on `execute-next-task`.
- `config/steps/execute-next-task.yaml:157,169-170` — contract prose says "This step is typically used with `repeat_until: all_tasks_completed`" and declares `verify.repeat_done_when: ["No unchecked tasks (- [ ]) remain in tasks.md"]`. There is no top-level `repeat_until:` key in the contract YAML.
- `config/scripts/orchestrator_next/parser.py:26-41` — `StepContract` dataclass has no `repeat_until` or `repeat_done_when` field; `_load_contract` does not parse either field.
- `config/scripts/orchestrator_next/dispatch.py:140-147` — `_find_completed_step` checks `status == "completed"` only, with no loop-back logic.
- No existing tests cover `repeat_until` behavior in `config/scripts/orchestrator_next/tests/` (confirmed by grep across all test files).

#### Data Flow Trace

1. Driver calls `orchestrator record` with `step_id=execute-next-task, status=completed`.
2. `record()` in `record.py:52` calls `_compute_next_step(state_raw, "execute-next-task")`.
3. `_compute_next_step` at line 31 builds a `completed` set from `step_history` entries with `status=completed`, then adds `(phase, "execute-next-task")` at line 38.
4. At line 39-48, it iterates `active = ["execute-next-task", "run-phase-review"]`. `execute-next-task` is in `completed` → skipped. `run-phase-review` is not → returned as `next_step`.
5. `tasks.md` is never read. The `repeat_until: all_tasks_completed` annotation in `bugfix.yaml` and `repeat_done_when` in the step contract's `verify:` block are never consulted.

### Root Cause

`_compute_next_step` in `config/scripts/orchestrator_next/record.py:39-48` advances past any step that has a single `completed` entry in `step_history`, with no mechanism to re-emit the same step when a `repeat_until` condition is unsatisfied. The problem is two-layered:

1. **Primary (record.py:39-48)**: The loop never checks for a `repeat_until` annotation on the current step.
2. **Secondary (parser.py:26-41)**: `StepContract` does not parse `verify.repeat_done_when`, so even if `_compute_next_step` tried to inspect the contract, the field would not be available.

The `repeat_until` semantics are defined in the workflow schema (`bugfix.yaml:113`) and documented in the step contract prose (`execute-next-task.yaml:157`), but neither record.py nor dispatch.py consults them at runtime.

Reference: `config/scripts/orchestrator_next/record.py:39-48` (advance loop), `config/scripts/orchestrator_next/parser.py:26-41` (StepContract missing field).

### Impact

#### Severity

high

#### Affected Areas

- All workflows using `repeat_until: all_tasks_completed` (`bugfix.yaml:113`, `feature.yaml:147`, `spike.yaml:52`). Autopilot (`autopilot.yaml:60` uses `all_iterations_completed`) is similarly affected by the same missing check for a different predicate.
- In interactive/driver-guided mode: driver catches this manually. In `auto: true` autopilot mode: workflow silently skips remaining tasks.
- `orchestrator next` in `dispatch.py:268-273` has the same `_find_completed_step` logic but operates on the state that `record.py` already wrote; fixing `record.py` is sufficient.
- No existing tests cover `repeat_until` — fix must add tests to prevent regression.

#### Since When

Introduced with the initial `_compute_next_step` implementation. The `repeat_until` schema annotation was added to workflow files without a corresponding enforcement path in `record.py`.

---

## ISSUE-17: `cost_usd` NULL in `step_events` when `model` absent from agent `<usage>` block

### Symptoms

`orchestrator cost --change-id fix-workflow-issues-2026-04-19` returns `$0.0000` for all steps despite 44K+ tokens recorded in `step_history`. Every row in `step_events.cost_usd` is NULL for native-agent steps (developer, architect, reviewer, etc.) because the Task tool's `<usage>` block does not consistently include `model`, and no code path computes `cost_usd` from tokens when `model` is absent.

### Reproduction Steps

1. From the worktree root, run:
   ```
   python3 .tmp/repro-issue-17.py
   ```
2. The script records an agent step with `input_tokens=22000, output_tokens=5000` but no `model` or `cost_usd` in the usage block.
3. It upserts the resulting `step_history` entry into a DuckDB in-memory database and queries `cost_usd`.
4. Observed failure: `cost_usd = None` despite 27,000 tokens recorded.

Expected: `cost_usd >= 0.141000` (developer = native_sonnet = $3/1M input + $15/1M output).
Actual: `cost_usd = None`. Exit code 1.

### Expected vs Actual

- **Expected**: `record.py` (or `upsert.py`) computes `cost_usd = input_tokens × price.input/1e6 + output_tokens × price.output/1e6` by resolving agent → model via `scripts/routes.yaml` and model → price via `config/pricing.yaml`, when `cost_usd` and `model` are both absent from the payload's `usage` block.
- **Actual**: `record.py` passes `usage` through verbatim; `upsert.py` reads `usage.get("cost_usd")` which returns `None`; no computation is performed anywhere in `config/scripts/orchestrator_next/`.

### Investigation

#### Evidence Gathered

- `config/scripts/orchestrator_next/record.py:177` — `"usage": payload.get("usage") or {}` passes usage through unmodified. No model resolution, no cost computation.
- `config/scripts/orchestrator_next/upsert.py:279,305` — `usage.get("model")` and `usage.get("cost_usd")` are passed directly to the SQL INSERT; NULL when absent from payload.
- `config/scripts/orchestrator_next/upsert.py:249-311` — `upsert_step_event` does not read `pricing.yaml` or `routes.yaml`. No cost computation exists anywhere in `config/scripts/orchestrator_next/`.
- `scripts/routes.yaml:3-9` — agent-to-backend mapping exists: `developer → native_sonnet`, `architect → native_opus`, etc.
- `config/pricing.yaml:29-32` — `claude-sonnet-4-6: {input: 3.00, output: 15.00}` is defined.
- `scripts/routes.yaml` backend naming (`native_sonnet`, `native_opus`) does not directly match `pricing.yaml` keys (`claude-sonnet-4-6`, `claude-opus-4-7`). A translation layer is needed.
- `config/scripts/estimate-cost.sh:41,113` — `pricing.yaml` is consumed by bash scripts for pre-flight estimation, not by `record.py`.
- Grep for cost computation in `config/scripts/orchestrator_next/`: zero hits for `input_tokens.*\*`, `pricing`, or `cost_usd =` assignment (excluding test literals and log strings).

#### Data Flow Trace

1. Driver records an agent step via `orchestrator record state.yaml` with JSON payload containing `usage: {input_tokens: N, output_tokens: M}` but no `model` or `cost_usd` — this is the realistic case when Claude Code's Task tool `<usage>` block omits the model name.
2. `record.py:177` stores `usage` as-is into the history entry written to `state.yaml`.
3. Driver later calls `orchestrator cost --change-id <cid>`, which triggers `upsert.py:upsert_step_event`.
4. `upsert.py:305` executes `INSERT ... cost_usd = usage.get("cost_usd")` → `None` → NULL in DB.
5. `cost_report.py:65` queries `COALESCE(SUM(cost_usd), 0.0)` — with all NULL, returns `$0.0000`.

### Root Cause

No code path in `config/scripts/orchestrator_next/` computes `cost_usd` from tokens and pricing. `record.py` passes `usage` through verbatim (`record.py:177`); `upsert.py` reads `usage.get("cost_usd")` directly (`upsert.py:305`). When the agent's Task tool omits `model` and `cost_usd` from its `<usage>` block — which is the common case for native Claude Code agents — the column is NULL and the cost report shows $0.

The fix requires `record.py` to resolve `agent → model` via `scripts/routes.yaml` and compute `cost_usd` from tokens × `config/pricing.yaml` rates when `cost_usd` is absent from the payload. The `native_sonnet` / `native_opus` backend keys in `routes.yaml` must be mapped to their corresponding model-id keys in `pricing.yaml` (e.g., `native_sonnet → claude-sonnet-4-6`).

Reference: `config/scripts/orchestrator_next/record.py:177` (pass-through), `config/scripts/orchestrator_next/upsert.py:305` (NULL insertion), `scripts/routes.yaml:3-9` (agent→backend mapping, unused by record.py), `config/pricing.yaml:29-32` (price table, unused by record.py).

### Impact

#### Severity

high

#### Affected Areas

- `orchestrator cost --change-id <any>` — returns $0 for all workflows run with native agents (all current agents).
- `step_events.cost_usd` — NULL for every native-agent step since routing switched from LiteLLM proxy to native Claude Code agents.
- Real-time autopilot cost monitoring — non-functional; the live feedback loop cannot show cost without manual post-hoc backfill.
- Cost-based budget gates and anomaly detection in `cost_report.py` — silently pass (zero is never over budget).
- No existing tests in `config/scripts/orchestrator_next/tests/` cover model resolution or cost computation in `record.py`/`upsert.py`.
- Fixing ISSUE-17 must handle the `native_sonnet` → `claude-sonnet-4-6` mapping; a hard-coded lookup table or a new field in `routes.yaml` (e.g., `model_id`) is needed.

#### Since When

Introduced when all agents were switched from LiteLLM proxy routing to native Claude Code (`native_sonnet`, `native_opus`) — the proxy route wrote `cost_usd` via LiteLLM's cost tracking; native routing does not. Specific commit not yet identified (likely within the last 5 days based on issue log).

---

## Proposed Approaches

- **ISSUE-16**: Extend `StepContract` to parse `verify.repeat_done_when` and add a `repeat_until` field; add a `_check_repeat_condition(contract, state_raw)` helper to `record.py` that reads `tasks.md` (path from `state.yaml`) and counts unchecked `- [ ]` lines; call it from `_compute_next_step` before advancing past a step with `all_tasks_completed`.

- **ISSUE-17**: In `record.py`, when `payload.usage.cost_usd` is absent, resolve `payload.agent` → model_id via `routes.yaml` (adding a `model_id` field or a `native_*` → model-id mapping) and compute `cost_usd` from `input_tokens × pricing[model_id].input/1e6 + output_tokens × pricing[model_id].output/1e6` using `config/pricing.yaml`, storing the result back into the `usage` dict before it is written to `state.yaml` and later upserted.

---

## Unresolved Questions

1. **ISSUE-16 — `repeat_until` field location**: The `repeat_until: all_tasks_completed` annotation lives in the workflow schema (`bugfix.yaml:113`), not in the step contract YAML. Should `record.py` read the repeat condition from the workflow schema (more authoritative) or from the step contract's `verify.repeat_done_when` field (closer to the step)? The fix direction depends on which location is treated as the single source of truth.

2. **ISSUE-16 — tasks.md path resolution**: `state.yaml` has a `tasks_path` key in the repro but the real state files may store it differently. What is the canonical field name for the tasks file path in production state.yamls? Checking `fix-workflow-issues-2026-04-19` state suggests the file lives at `<worktree_path>/tasks.md` by convention, but this is not explicitly recorded.

3. **ISSUE-17 — `native_*` → model-id mapping**: `routes.yaml` maps agents to `native_opus` / `native_sonnet` but `pricing.yaml` keys are model IDs (`claude-opus-4-7`, `claude-sonnet-4-6`). Should a translation map be added to `routes.yaml` (e.g., `native_sonnet: claude-sonnet-4-6`) or hardcoded in `record.py`? Which Claude model IDs correspond to `native_opus` and `native_sonnet` as of this date?

4. **ISSUE-17 — cache_read_input_tokens pricing**: `pricing.yaml` has a `cache_read` rate. Should the cost formula include `cache_read_input_tokens × cache_read/1e6` when that field is present in the usage block?


## Linear Ticket

none

<!-- Format contract: contracts/artifact-formats.md § Diagnosis Format Contract -->
