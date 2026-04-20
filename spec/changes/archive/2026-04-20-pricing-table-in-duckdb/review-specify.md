# Phase Review: specify — pricing-table-in-duckdb

**Phase**: specify
**Reviewer**: reviewer agent (claude-sonnet-4-6)
**Date**: 2026-04-20
**Verdict**: REJECTED

---

## Scores

| Dimension | Score | Notes |
|---|---|---|
| Locked-decision compliance | 10/10 | All four locked decisions honoured |
| Multi-level metrics invariant (NFR-5 + AC-9) | 10/10 | No step_events columns added; pricing is schema-agnostic |
| SQL field-name drift | 10/10 | Design SQL references only pricing columns; step_events columns verified clean |
| Test coverage completeness | 7/10 | FR numbering drift breaks traceability; bash 3.2 scenario absent |
| Spec/AC traceability | 7/10 | FR and AC references in tasks.md contain systematic numbering errors |
| Scope hygiene | 10/10 | No phase 2–5 leakage found |
| Call-site coverage | 5/10 | Design names one caller; three exist; T-6 file list is stale/incomplete |
| Clarity and implementability | 8/10 | Pseudocode and data-flow diagrams are clear; call-site gap creates implementation ambiguity |
| Risk coverage | 8/10 | Bash 3.2 risk not documented as test scenario; otherwise well-covered |

**Overall: 7.5/10**

---

## Verification Results

### Locked-Decision Compliance

All four driver-locked decisions are respected:

- **Approach A (custom runner + schema_migrations + standalone ingestion)**: design.md § Approach A and all related sections correctly implement this. `_run_migrations` is a sibling in `upsert.py`, no new module.
- **No new `orchestrator` CLI subcommands**: NFR-6, design § Non-Goals, and spec § Alternatives Considered (Alternative 3) all explicitly prohibit this. `estimate-cost.sh` shells out to `duckdb -json` directly. Clean.
- **`estimate-cost.sh` rewrites to query DuckDB directly**: FR-6 and design §8 implement this correctly. AWK block replaced with `duckdb -readonly -json` + `python3 -c` one-liner.
- **Schema migrations ≠ data ingestion, separate standalone ingestion script**: FR-5, `scripts/ingest-pricing.py`, and the "Recurring price updates" decision in discovery § Key Decisions all honour this. The migration runner is DDL-only; `ingest-pricing.py` handles rate rows.

### Multi-Level Metrics Invariant (NFR-5 + AC-9)

Verified clean:
- Live `_DDL_STEP_EVENTS` in `upsert.py` (grepped): columns are `repo_root, change_id, phase, step_id, attempt, agent_name, agent_id, status, schema_name, started_at, ended_at, duration_ms, model, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, cost_usd, turns, tool_calls_json, artifacts_json, escalation_json, upserted_at`. No new columns proposed in design.
- The `pricing` DDL (design §2) contains `model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd, is_local, effective_from`. No `step_id`, `phase`, `feature`, or `driver` columns.
- AC-9 is covered by T-3 scenario (e): "DESCRIBE step_events is unchanged from pre-migration baseline."

### SQL Field-Name Drift

Design SQL sketches reference `pricing` table columns only. No queries against `step_events` columns in the pricing lookup path. Design §3 states explicitly: "step_events live-schema (verified against `_DDL_STEP_EVENTS`): columns include `model VARCHAR`, `cost_usd DOUBLE`, `started_at TIMESTAMP`, `ended_at TIMESTAMP`." These match the live DDL. No field-name drift.

---

## Findings

### CRITICAL — F-1: Signature change leaves two callers in `bin/orchestrator` unaddressed, and the primary record.py caller has no DB connection

**Location**: `design.md §4`, `tasks.md T-6`

**What's wrong**: The design states `_compute_cost_usd(db, agent, usage, *, now=None)` with "The caller in `record.main()` already holds the open DB connection; it's passed through." There are **three** actual callers, not one:

1. `config/scripts/orchestrator_next/record.py:394` — inside `record()` function. This function has **no DuckDB import and no DB connection in scope**. There is no `import duckdb` anywhere in `record.py`. The design says the connection is "passed through" from `record.main()` but `record.main()` does not open a DB connection either — it only calls `record(state_yaml_path, payload)`. The DB connection exists nowhere in the `record.py` call stack.

2. `bin/orchestrator:337` in `_ingest_driver_main` — the cost computation at line 337 calls `_compute_cost_usd("driver-loop", usage)` **before** `db = duckdb.connect(db_path)` at line 344. Even if the DB connection were threaded back, the ordering would need to change.

3. `bin/orchestrator:473` in `_ingest_subagents_main` — `db` is in scope here. This is the only caller that would work with the new signature as-is.

T-6 task says "Files: `bin/orchestrator` (modify call site at line 562 area)" — line 562 is a `upsert_step_event` call, not a `_compute_cost_usd` call. The two actual call sites (lines 337, 473) are not listed.

**Why it matters**: The signature change is the central mechanism of this entire feature. If `record.py` has no DB connection to pass, either: (a) `record()` must be taught to open a DB connection (material scope addition not in spec/design/tasks), or (b) the design's proposed signature is wrong and a different DB-acquisition strategy is needed (e.g., open a DB inside `_compute_cost_usd` or `_lookup_price` using a path from env). Neither variant is documented. T-6 as written will fail at implementation because the developer will hit an unresolvable dependency.

**Fix required** (all three artifacts must change atomically):
- **spec.md**: Update FR-3 and the Impact section. The signature change impacts more callers than stated. Clarify whether `record()` opens its own DB connection or `_compute_cost_usd` opens one internally.
- **design.md §4**: Choose one of: (a) `_compute_cost_usd` opens its own short-lived connection using `METRICS_DB` env var (matches `bin/orchestrator` pattern), (b) `record()` is extended to open a DB and pass it through (adds schema to function surface), or (c) split `_lookup_price` out of `_compute_cost_usd` so `_lookup_price(db, ...)` is called only from sites that have a connection. Document the chosen approach and the DB-acquisition path.
- **tasks.md T-6**: Replace "line 562 area" with the actual caller lines (337, 473 in `bin/orchestrator`; 394 in `record.py`). Add a step to handle the `record.py` DB-connection gap.

---

### MAJOR — F-2: FR and AC numbering in tasks.md is systematically off by one from spec.md

**Location**: `tasks.md`, multiple tasks

**What's wrong**:

| Task | tasks.md cites | Actual spec content |
|---|---|---|
| T-7 "Why" | FR-5 | FR-5 is `ingest-pricing.py`; `_load_pricing_for_model` is FR-4 |
| T-8 "Why" | FR-5 | Same error |
| T-11 "Why" | FR-7 | FR-7 is the deletion gate; `ingest-pricing.py` is FR-5 |
| T-12 "Why" | FR-7 | Same error |
| T-14 "Why" | FR-8 | FR-8 does not exist in spec.md (spec has FR-1 through FR-7) |

**Why it matters**: Traceability is a first-class review criterion for specify-phase artifacts. When a developer implements T-7 and cites FR-5, they will look up "ingest-pricing.py" requirements when they should be reading "_load_pricing_for_model" requirements. During implementation-phase reviews, mismatched FR citations cause confusion about whether coverage is complete. T-14 citing a non-existent FR-8 will fail future automated traceability checks.

**Fix required**: Correct the FR citations in T-7, T-8, T-11, T-12 to match spec.md (FR-4, FR-4, FR-5, FR-5 respectively). Remove the FR-8 reference from T-14 and replace with FR-7 (the deletion gate) and AC-8.

---

### MAJOR — F-3: `_load_pricing` import from `record.py` in `cost_report.py` is not scrubbed in any task

**Location**: `cost_report.py:71`, `tasks.md T-6, T-8`

**What's wrong**: The live `cost_report.py` imports `_load_pricing` from `record.py` at line 71: `from orchestrator_next.record import _load_pricing`. T-6 deletes `_load_pricing` from `record.py`. T-8 rewrites `_load_pricing_for_model` to use SQL. But neither task explicitly calls out removing the `_load_pricing` import from `cost_report.py`. After T-6 applies, `cost_report.py` will have a dead import that raises `ImportError` at runtime whenever `_load_pricing_for_model` runs its `try/except` block — which would silently degrade to the fallback dict on every lookup, not just on legacy-DB paths.

**Fix required**: Add to T-8's file list and Verify section: "scrub `from orchestrator_next.record import _load_pricing` from `cost_report.py`; `rg '_load_pricing\b' cost_report.py` returns zero hits."

---

### MINOR — F-4: T-9 and T-10 have no bash 3.2 (macOS default) test scenario

**Location**: `tasks.md T-9, T-10`

**What's wrong**: The `preview-route` step in `state.yaml` already failed with "bash 3.2 vs 4+ associative arrays" — this is a documented real-world failure on the same machine. The design's `lookup_pricing` replacement uses `[[ -f ]]`, `local`, `${model//\'/\'\'}`, and `duckdb -readonly` — none of these require bash 4+, which is correct. But T-9 scenarios do not include "runs cleanly under bash 3.2 (`bash --version` < 4)" as an explicit scenario, leaving no test evidence that the rewrite avoids the same bash-version incompatibility that killed the current script.

**Fix required**: Add one scenario to T-9: "(d) run `bash -c 'source config/scripts/estimate-cost.sh && lookup_pricing claude-sonnet-4-6'` under `/bin/bash` (which is bash 3.2 on macOS) → exits 0, no associative-array or `[[` errors". This is low-effort and documents that the bash 3.2 regression is permanently covered.

---

### MINOR — F-5: `ingest-pricing.py` calls `ensure_schema(db)` — design does not verify `ensure_schema` import path

**Location**: `design.md §7`, `tasks.md T-12`

**What's wrong**: `scripts/ingest-pricing.py` is in `scripts/` not `config/scripts/orchestrator_next/`. The design says it calls `ensure_schema(db)` but does not document the import path. The existing pattern for scripts in `scripts/inline/` is to call `orchestrator_next` modules via `sys.path` manipulation. T-12 does not include a Verify step for "import works from the `scripts/` location."

**Fix required**: Add to design §7 a one-line note on the import path (e.g., `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "config", "scripts"))`) or confirm that the `scripts/` runner already has `orchestrator_next` on its Python path. Add to T-12 Verify: "`python scripts/ingest-pricing.py --help` runs without ImportError."

---

## AC Coverage Matrix

| AC | Task covering it | Status |
|---|---|---|
| AC-1 | T-1(b), T-3(a)(b), T-12(a) | Covered |
| AC-2 | T-1(c), T-3(a) | Covered |
| AC-3 | T-5 (test_record_cost_compute.py) | Covered |
| AC-4 | T-5 (unknown model scenario) | Covered |
| AC-5 | T-9(b) | Covered |
| AC-6 | T-7(c) | Covered — note: spec labels this AC-6 but task cites it as AC-6; correct |
| AC-7 | T-11(a)(b) | Covered — but tasks.md cites FR-7 instead of FR-5 (F-2) |
| AC-8 | T-13, T-14, T-15 | Covered |
| AC-9 | T-3(e) | Covered |

All ACs have at least one covering task. Coverage is complete; numbering errors (F-2) are documentation drift, not coverage gaps.

## TDD Order Check

Every implementation task (GREEN) has a preceding test task (RED):
- T-1 (RED) → T-2 (GREEN) ✓
- T-3 (RED) → T-4 (GREEN) ✓
- T-5 (RED) → T-6 (GREEN) ✓
- T-7 (RED) → T-8 (GREEN) ✓
- T-9 (RED) → T-10 (GREEN) ✓
- T-11 (RED) → T-12 (GREEN) ✓

TDD order is correct throughout.

## Scope Hygiene

No phase 2–5 content found. `done` rename, report views, CLI retirement — absent. The `schema_migrations` tracking table is intentionally introduced in phase 1 as the foundation for phases 2–5, which is correct per the parent effort's description. The runner itself is phase 1 scope.

---

## Required Changes for Re-Review

The architect must address the following before re-review can approve:

1. **[CRITICAL — F-1]** Resolve the DB-connection gap for `_compute_cost_usd` callers. Choose a DB-acquisition strategy for `record.py` (which has no DuckDB import or connection). Update spec.md FR-3 and Impact section, design.md §4 with the chosen approach and explicit caller inventory, and tasks.md T-6 with the correct file/line references for all three callers.

2. **[MAJOR — F-2]** Fix FR citations in tasks.md: T-7 and T-8 → FR-4; T-11 and T-12 → FR-5; T-14 → FR-7 + AC-8 (remove FR-8 reference).

3. **[MAJOR — F-3]** Add `_load_pricing` import scrub to T-8 file list and Verify section in tasks.md. No spec.md or design.md change required.

4. **[MINOR — F-4]** Add bash 3.2 test scenario to T-9 in tasks.md.

5. **[MINOR — F-5]** Document `ensure_schema` import path in design.md §7 and add ImportError check to T-12 Verify.

Note: findings F-2 through F-5 affect tasks.md only. Finding F-1 requires atomic updates to all three artifacts (spec.md + design.md + tasks.md).

---

## Re-review 2026-04-21

**Reviewer**: reviewer agent (claude-sonnet-4-6)
**Date**: 2026-04-21
**Prior score**: 7.5/10 (REJECTED)

---

### Summary of Architect's Revision

The architect chose Option B' for F-1 (DB acquired in `record.main()`), corrected FR citations throughout tasks.md (F-2), added explicit `_load_pricing` scrub to T-8 (F-3), added bash 3.2 scenario to T-9 (F-4), and documented the `sys.path` import pattern in design.md §7 and T-11/T-12 (F-5).

---

### Finding Resolution

#### F-1 (CRITICAL) — `_compute_cost_usd` DB-connection gap

**Status: RESOLVED**

Evidence:
- design.md §4 contains an explicit "Caller inventory (verified by grep against HEAD)" table naming all three call sites (A: record.py:394, B: bin/orchestrator:337 `_ingest_driver_main`, C: bin/orchestrator:473 `_ingest_subagents_main`), each with their DB-in-scope status and chosen strategy.
- Approach B' is documented with rationale: why A (`_record_main`) was rejected (doesn't open DB on the record branch), why C (open inside `_compute_cost_usd`) was rejected (DuckDB single-writer lock collision in `_ingest_subagents_main`), why B' was chosen.
- spec.md FR-3 enumerates all three call sites with per-site strategies including the `record.main()` DB-open pseudocode.
- spec.md Impact section explicitly calls out "THREE call sites update atomically" and names them with their line numbers.
- T-6 in tasks.md explicitly lists `bin/orchestrator` (TWO call sites — `_ingest_driver_main` ~line 337 and `_ingest_subagents_main` ~line 473) and `record.py` (modify `_compute_cost_usd`, `record()`, `main()`). T-6 Verify includes `rg '_compute_cost_usd\(' bin/orchestrator config/scripts/orchestrator_next/` showing exactly three call sites with `db` as first argument.
- Live-tree verification: bin/orchestrator line 337 calls `_compute_cost_usd("driver-loop", usage)` BEFORE `db = duckdb.connect(db_path)` at line 344 — confirming the ordering problem documented. Line 473 calls `_compute_cost_usd(agent_name, usage)` with `db` already open. Both match the design's analysis exactly.

#### F-2 (MAJOR) — FR numbering drift in tasks.md

**Status: RESOLVED — and the prior reviewer's original finding was CORRECT**

The current tasks.md citations are all correct:
- T-7 cites FR-4 (`_load_pricing_for_model`) — correct.
- T-8 cites FR-4 — correct.
- T-11 cites FR-5 (`ingest-pricing.py`) — correct.
- T-12 cites FR-5 — correct.
- T-14 cites FR-7 (deletion gate) — correct; no FR-8 reference present.

The prior reviewer's table claimed T-7/T-8 cited "FR-5" and T-11/T-12 cited "FR-7". Since the original tasks.md cannot be recovered from git (state dir files are not version-controlled in this repo), I cannot forensically distinguish "architect fixed the citations" from "prior reviewer miscounted." However, the architect's revision message claimed FR numbering was correct "except for T-14's FR-8 citation which was real." The revised tasks.md has no FR-8 anywhere and T-14 correctly cites FR-7. Given the current artifacts are internally consistent and the prior review's enumerated table was specific (not vague), I assess the prior review's finding was real and the architect fixed it. The architect's claim that only T-14 was wrong appears to have been partially accurate (T-14 was real) but incomplete (the other citations were also corrected). Either way, the current state is correct and this finding is closed.

#### F-3 (MAJOR) — Stale `_load_pricing` import in cost_report.py not scrubbed

**Status: RESOLVED**

Evidence:
- Live cost_report.py:71 confirms `from orchestrator_next.record import _load_pricing` is present (verified by Read of lines 65-79).
- T-8's title explicitly says "scrub stale `_load_pricing` import". T-8 Approach step 1 says "Delete the line `from orchestrator_next.record import _load_pricing` at cost_report.py:71 and the line `pricing = _load_pricing()` at :72."
- T-8 Verify contains: `rg '_load_pricing\b' config/scripts/orchestrator_next/cost_report.py` returns ZERO hits AND `rg 'from orchestrator_next\.record import' cost_report.py` returns zero hits.
- spec.md Impact section explicitly mentions: "A stale `from orchestrator_next.record import _load_pricing` import in `cost_report.py` is scrubbed at the same time (T-8)."

#### F-4 (MINOR) — No bash 3.2 test scenario in T-9

**Status: RESOLVED**

T-9 scenario (d) is explicit and complete: "explicitly invoke `/bin/bash config/scripts/estimate-cost.sh …` on macOS (where `/bin/bash` is 3.2.x) — or equivalently `env BASH_COMPAT=32 bash …` on Linux — and assert exit 0 with no 'declare -A' / 'bad substitution' / associative-array errors in stderr."

T-10 Verify includes: `rg 'declare -A|\\${[A-Za-z_]+\\^\\^|mapfile' config/scripts/estimate-cost.sh` returns zero hits.

#### F-5 (MINOR) — Import path for `ingest-pricing.py` undocumented

**Status: RESOLVED**

design.md §7 has an explicit "Import path" paragraph: "Mirror the pattern already used by `scripts/inline/ingest-feature-metrics.py` (lines 33–37): resolve `ORCHESTRATOR_HOME` (env var, else the parent of the script's directory), then `sys.path.insert(0, os.path.join(ORCHESTRATOR_HOME, 'config', 'scripts'))` BEFORE the `from orchestrator_next...` import. A `python scripts/ingest-pricing.py --help` invocation with and without `ORCHESTRATOR_HOME` set must both resolve the import without error — T-12 Verify asserts this."

T-11 scenario (e) explicitly tests `--help` both with `ORCHESTRATOR_HOME` set AND with it unset.

Live verification of the referenced pattern: `ingest-feature-metrics.py` lines 33-37 do contain the `sys.path.insert` pattern keyed on `ORCHESTRATOR_HOME`. Note: the live script only handles the ORCHESTRATOR_HOME-set case; the fallback "else walk up from `__file__`" described in T-12 Approach is an enhancement over the referenced pattern, not a contradiction. T-12 Approach documents this correctly.

---

### New Finding (introduced by revision)

#### F-6 (MINOR) — spec.md FR-3(a) "falls back to default rates" contradicts design/tasks "cost_usd unset"

**Location**: `spec.md FR-3(a)` vs `design.md §4` and `tasks.md T-5`

**What's wrong**: spec.md FR-3(a) states: "when no path is resolvable (test/offline), `record.main()` falls back to default rates with a stderr warning and does NOT raise." This implies a numeric cost is computed and written to state.yaml. However, design.md §4 states: "When `db is None`, `_compute_cost_usd` returns `(resolved_model_id, None)` with a stderr warning." T-5 new scenario confirms: "`record(...db=None)` → `usage.cost_usd` remains unset, stderr warning printed, no exception." These two behaviours are mutually exclusive: "default rates" implies cost_usd is written; "None / unset" means it is not.

**Why it matters**: The developer implementing T-6 will read FR-3 first and build a "compute at default rates when offline" path, then read the design and T-5 and realise the contract requires cost_usd to be absent. Either interpretation is acceptable architecturally, but the spec is the contract and the spec says the wrong thing. A reviewer during implement-phase will reject T-6 if it produces `cost_usd = None` while FR-3 says "default rates."

**Severity**: MINOR (single-line fix). The design+tasks are internally consistent; the spec line is the outlier.

**Required fix**: Change spec.md FR-3(a) from "falls back to default rates with a stderr warning" to "passes `db=None`, `_compute_cost_usd` returns `(model_id, None)`, leaving `cost_usd` unset in state.yaml, stderr warning written, no exception." This is a one-sentence update to FR-3(a).

**Does this block approval?** No — the design and tasks carry sufficient implementation guidance and are mutually consistent. The spec inconsistency is documentation drift, not a correctness gap in the implementation plan. It should be fixed before or during T-6 implementation at the latest.

---

### Dimension Scores (Revision 2)

| Dimension | Score | Notes |
|---|---|---|
| Locked-decision compliance | 10/10 | All four locked decisions honoured; Approach A, no new CLI subcommands, DuckDB-first, separate ingestion |
| Multi-level metrics invariant (NFR-5 + AC-9) | 10/10 | Unchanged |
| SQL field-name drift | 10/10 | Unchanged |
| Test coverage completeness | 9/10 | Bash 3.2 scenario added (F-4 resolved); import-resilience test in T-11(e) added (F-5 resolved) |
| Spec/AC traceability | 9/10 | FR citations corrected (F-2 resolved); minor spec/design inconsistency on db=None path (F-6, MINOR) |
| Scope hygiene | 10/10 | Unchanged |
| Call-site coverage | 10/10 | All three callers explicitly documented in design §4, T-6 file list, and spec FR-3 Impact |
| Clarity and implementability | 9/10 | DB-acquisition strategy fully documented with rationale; F-6 spec inconsistency is the only ambiguity |
| Risk coverage | 9/10 | Bash 3.2 regression locked in T-9(d); F-5 import-path risk covered; F-6 is residual minor risk |

**Overall: 9.5/10**

---

### Verdict: APPROVED

All five prior findings are resolved. One new minor finding (F-6) is introduced by the revision but does not block implementation — the design and tasks are mutually consistent and provide sufficient guidance. The spec FR-3(a) wording should be corrected before or during T-6 implementation.

**Exact text change required for F-6** (not blocking, but must be done before T-6 implementation review):

In `spec.md` FR-3, replace:
> when no path is resolvable (test/offline), `record.main()` falls back to default rates with a stderr warning and does NOT raise.

With:
> when no path is resolvable (test/offline), `record.main()` passes `db=None` to `record()`, `_compute_cost_usd` returns `(model_id, None)`, `cost_usd` is left unset in the state.yaml step_history, a stderr warning is emitted, and no exception is raised.

