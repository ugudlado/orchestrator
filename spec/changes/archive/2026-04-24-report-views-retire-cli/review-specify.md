---
phase: specify
change_id: report-views-retire-cli
reviewer: reviewer-agent
attempt: 1
---

# Phase Review: specify — report-views-retire-cli

## Verification Gates

- Type-check: N/A (specify phase — no compilable code landed yet; artifacts are .md and .yaml files)
- Build: N/A (specify phase)
- Tests: N/A (specify phase)
- Assertions checked: see below

### verify.assertions

| Assertion | Result | Evidence |
|---|---|---|
| spec.md exists in $WORKFLOW_STATE_DIR/$CHANGE_ID/ | PASS | `/Users/spidey/code/orchestrator/.state/report-views-retire-cli/spec.md` present, 129 lines |
| design.md exists in $WORKFLOW_STATE_DIR/$CHANGE_ID/ | PASS | `/Users/spidey/code/orchestrator/.state/report-views-retire-cli/design.md` present |
| tasks.md exists with at least one task | PASS | 15 tasks (T-0 through T-14) |
| spec.md has Acceptance Criteria section with testable criteria | PASS | 12 ACs with [traces:] back-links |

All four assertions passed on first attempt.

## Baseline Comparison

Historical archive scanned: `spec/changes/archive/*/state.yaml`. Matching entries (schema: feature) with `metrics.review_score_avg` present:
- `2026-04-21-durable-intent-and-resume`: specify-phase reviewer score 9.6

Current overall score: 7 (see below). Delta: −2.6 below historical average of 9.6.

**Quality regression warning**: current score 7 is 2+ points below historical average 9.6 for schema=feature, phase=specify.

## Acceptance Criteria Coverage

| AC | Criterion (abbreviated) | Covered by task(s) | Testable? |
|---|---|---|---|
| AC-1 | `SELECT COUNT(*) FROM feature_report` returns distinct (repo_root, change_id) count | T-1(b), T-2 | Yes |
| AC-2 | NULL cost_usd coalesced to 0 in partial run | T-1(c), T-2 | Yes |
| AC-3 | Missing feature_metrics row → one row returned with NULL metric columns | T-1(d), T-2 | Yes |
| AC-4 | compute-swe-metrics.sh run twice → byte-identical stdout | T-4, T-5 | Yes |
| AC-5 | Rewritten compute-swe-metrics.sh output matches baseline fixture | T-4, T-5 | Yes |
| AC-6 | Rewritten read-sub-state-metrics.sh output matches baseline fixture | T-6, T-7 | Yes |
| AC-7 | grep `orchestrator (cost|metrics)` across production dirs returns zero | T-10, T-11 | Yes |
| AC-8 | `bin/orchestrator cost/metrics` exits 3 with usage banner | T-10, T-11 | Yes |
| AC-9 | per_agent_tokens parseable via json.loads | T-1(f), T-2 | Yes (partial — see finding F-1) |
| AC-10 | cost-report.sh output matches baseline diff | T-8, T-9 | Yes |
| AC-11 | All FR-2/3/4/5 columns have asserting tests; all zero-div guards have denom=0 tests | T-1, T-2 | Yes |
| AC-12 | Coverage ≥ 90% on modified files | T-13 | Yes |

All 12 ACs are traceable to at least one task. No AC is unmapped.

## Locked-Decision Compliance Check

| Decision | Spec/Design claim | Verified against source | Status |
|---|---|---|---|
| D-1 (OQ-1): anomalies → Approach C | `_anomalies` + `_step_allowlist_anomalies` preserved in cost_report.py, no CLI surface | cost_report.py confirmed present; tasks.md T-12 preserves them | PASS |
| D-2 (OQ-2): markdown renderer fate → T-9 gate | T-9 implements inline formatter with diff gate; `render_markdown_feature` retained only if diff non-empty | T-9 task body describes the gate correctly | PASS |
| D-3 (OQ-3): `--since` / `--by complexity` dropped | repo_report view exposed; no --since filter in view DDL | design.md FR-5: `--since` not exposed as view | PASS |
| D-4 (OQ-4): read-sub-state-metrics.sh in scope | T-6/T-7 cover it; T-3 captures baseline fixture for it | tasks.md T-3 references `baseline_read_sub_state_metrics.yaml` | PASS |
| D-5 (OQ-5): baseline = 2026-04-21-durable-intent-and-resume | T-3 references it | T-3 approach reads `spec/changes/archive/2026-04-21-durable-intent-and-resume/state.yaml` | PASS |
| D-6 (OQ-6): json_group_object + sort_keys=True | design.md DDL uses `json_group_object(...)::VARCHAR`; shell consumer re-dumps with sort_keys=True | design.md § Components §1 and §3 confirm both | PASS |
| D-7 (OQ-7): no orchestrator_next.report module | No new Python module in any task | tasks.md — no task creates a .py module except test files | PASS |
| D-8 (DV-8): per_step inside feature_report | per_step_agg CTE inside feature_report DDL | design.md lines 245–272 confirm per_step_agg | PASS |
| D-9 (DV-9): per_model not a view | scripts/cost-report.sh uses direct `SELECT model, SUM(cost_usd)... GROUP BY model` | design.md § Components §2 confirms direct GROUP BY query | PASS |

All 9 locked decisions honored.

## SQL Field-Name Drift Check (cycle-12 rule)

Cross-referenced design.md DDL column references against `upsert.py` live DDL:

| Column used in view DDL | Exists in upsert.py? |
|---|---|
| se.repo_root | PASS — `_DDL_STEP_EVENTS` |
| se.change_id | PASS |
| se.cost_usd | PASS |
| se.input_tokens | PASS |
| se.output_tokens | PASS |
| se.cache_creation_input_tokens | PASS |
| se.cache_read_input_tokens | PASS |
| se.turns | PASS |
| se.duration_ms | PASS |
| se.agent_name | PASS |
| se.model | PASS |
| se.attempt | PASS |
| se.step_id | PASS |
| se.started_at | PASS (phase_report first_seen = MIN(started_at)) |
| tc.tool_name | PASS — `_DDL_TOOL_CALLS` |
| tc.agent_name | PASS |
| fm.resolve_rate | PASS — `_DDL_FEATURE_METRICS` |
| fm.files_changed | PASS |
| fm.wall_clock_minutes | PASS |
| fm.review_score_avg | PASS |
| fm.rework_rate | PASS |
| pricing.model_id | PASS — `0001_seed_pricing.sql` |
| pricing.input_usd | PASS |
| pricing.cache_read_usd | PASS |
| pricing.cache_creation_usd | PASS |

No field-name drift found.

## Zero-Division Guard Verification

design.md § Zero-Division Translation Table lists 7 guards. All verified present in view DDL:

1. `rework_ratio` — `CASE WHEN b.rework_denom = 0 THEN 0.0 ELSE b.rework_cost / b.rework_denom END` ✓ (design lines 288–289)
2. `cost_per_task_usd` — `CASE WHEN COALESCE(fm.tasks_total,0) = 0 THEN 0.0 ELSE ...` ✓ (design line 318–319)
3. `cost_per_resolution_usd` — `CASE WHEN COALESCE(fm.tasks_completed,0) = 0 THEN 0.0 ELSE ...` ✓ (design line 320–321)
4. `tokens_per_task` — `CASE WHEN COALESCE(fm.tasks_total,0) = 0 THEN 0 ELSE ...` ✓ (design line 322–323)
5. `tokens_per_resolution` — `CASE WHEN COALESCE(fm.tasks_completed,0) = 0 THEN 0 ELSE ...` ✓ (design line 324–325)
6. `input_output_ratio` — `CASE WHEN b.output_tokens = 0 THEN 0.0 ELSE ...` ✓ (design line 326–327)
7. `cache_hit_rate` — `CASE WHEN (b.input_tokens + ...) = 0 THEN 0.0 ELSE ...` ✓ (design line 328–330)

All 7 guards present in DDL. T-1 criterion (e) mandates denominator-zero test cases for each.

## Scope Hygiene

- No Phase 4 scope items (orchestrator done rename, salvage path): confirmed absent
- No Phase 5 scope items (ingest-driver/ingest-subagents retirement, `ingest-feature-metrics.py`): confirmed absent
- No new `step_events` columns introduced: design.md Non-Goals explicitly states "No changes to step_events [...] DDL"
- `render_metrics_md` retirement is unconditional per D-2: T-12 lists it for deletion; `render_markdown_feature` conditional on T-9 gate — confirmed

## Multi-Level Metrics Invariant

`total_tokens` formula in view DDL (design line 148–150):
```sql
COALESCE(SUM(se.input_tokens), 0)
  + COALESCE(SUM(se.output_tokens), 0)
  + COALESCE(SUM(se.cache_creation_input_tokens), 0)   AS total_tokens
```

`aggregate_metrics()` in `metrics_report.py` line 289:
```python
total_tokens = input_tok + output_tok + cache_create
```

Both formulas omit `cache_read_input_tokens`. Formulas are consistent. ✓

## Byte-Equivalence Test Quality

T-3 baseline capture strategy: replay `2026-04-21-durable-intent-and-resume/state.yaml` step_history through `upsert_step_event`, dump via `duckdb -c ".dump"`, capture pre-rewrite stdout. Archive confirmed to exist with usable step_history data (checked: 35 archived features present). T-4 and T-6 diff against those fixtures. T-5 and T-7 must pass the same diff. This satisfies AC-4, AC-5, AC-6, NFR-1.

## Performance Budget Quality

NFR-5 specifies "full `spec/changes/archive/**` replayed, ~30 features, ~600 step_events rows" as the production-shaped target. Archive verified to contain 35 features. This is a realistic production-scale target, not a synthetic microbenchmark. T-13 implements the perf gate explicitly. ✓

## Risk Coherence Check

Key risks identified in discovery/design and mapped to mitigations:
- UC-E4 (anomaly detection disappears): mitigated by D-1 (Approach C, functions preserved)
- UC-E1 (NULL cost_usd in in-progress rows): mitigated by `COALESCE(SUM(cost_usd),0)` throughout
- UC-E2 (missing feature_metrics row): mitigated by LEFT JOIN on feature_metrics
- UC-E3 (non-deterministic output): mitigated by D-6 (sort_keys=True + explicit ORDER BY on views)
- Bash 3.2 compatibility: FR-9 explicitly forbids `declare -A`, `mapfile`, etc.; T-5/T-7 implementations must enforce

All major risks identified in discovery are covered by design decisions or test requirements. ✓

---

## Findings

### F-1 [IMPORTANT] design.md — `per_agent_tokens_agg` CTE GROUP BY includes `agent_name`, producing one-agent JSON per row

**Location**: `design.md` § Components §1, lines 189–216 (the `per_agent_tokens_agg` CTE definition)

**What is wrong**:

```sql
per_agent_tokens_agg AS (
  SELECT
    repo_root, change_id,
    json_group_object(agent_name, json_object(...))::VARCHAR AS per_agent_tokens
  FROM (
    SELECT repo_root, change_id, agent_name, input_tokens, output_tokens, cost_usd, duration_ms
    FROM step_events
  )
  GROUP BY repo_root, change_id, agent_name   -- <-- agent_name in GROUP BY
)
```

When `agent_name` is part of the GROUP BY, each output row contains exactly one agent. The `json_group_object(agent_name, json_object(...))` aggregates over groups that each have a single `agent_name` value, producing `{"architect": {...}}` per row — not a merged `{"architect": {...}, "reviewer": {...}}` per change. Because the outer `feature_report` SELECT has no GROUP BY to collapse this CTE's output, the LEFT JOIN against `base` (which is one row per `(repo_root, change_id)`) would either return no match or a multi-row result, depending on DuckDB's JOIN semantics.

**Why it matters**:
- FR-2 requires `feature_report` to return exactly one row per `(repo_root, change_id)` — the `per_agent_tokens` column must be a single JSON object containing all agents. The current CTE produces `(distinct agents × changes)` rows, not one row per change.
- AC-1 verifies the row count equals distinct (repo_root, change_id) from step_events. This assertion would fail at T-1 if fixtures include 2+ agents.
- T-2 instructs "Write the four CREATE OR REPLACE VIEW statements exactly as sketched in design.md § Components §1" — it will faithfully reproduce this defect unless the spec is corrected.
- T-1 criterion (f) only asserts `per_agent_tokens` is `parseable by json.loads` — it does not require multi-agent content. A one-agent JSON string passes criterion (f) even with 2 agents seeded. The defect would survive this test.

The NOTE comment at lines 211–216 acknowledges awkwardness but does not specify the correct fix; it suggests two alternatives without mandating one. This leaves the implementer free to reproduce the defect.

**Required fix (T-fix-1 below)**:
Replace the `per_agent_tokens_agg` CTE with a two-level aggregation:
1. Inner CTE: per-agent aggregate with `GROUP BY repo_root, change_id, agent_name`
2. Outer CTE: collapse by `(repo_root, change_id)` with `json_group_object(agent_name, json_object(...))` computed from the per-agent row

Example correct form:
```sql
per_agent_base AS (
  SELECT
    repo_root, change_id, agent_name,
    COALESCE(SUM(input_tokens),0)  AS input_tokens,
    COALESCE(SUM(output_tokens),0) AS output_tokens,
    COALESCE(SUM(cost_usd),0.0)    AS cost_usd,
    COALESCE(SUM(duration_ms),0)   AS duration_ms,
    COUNT(*)                        AS step_count
  FROM step_events
  GROUP BY repo_root, change_id, agent_name
),
per_agent_tokens_agg AS (
  SELECT
    repo_root, change_id,
    json_group_object(
      agent_name,
      json_object(
        'total_tokens',  input_tokens + output_tokens,
        'input_tokens',  input_tokens,
        'output_tokens', output_tokens,
        'cost_usd',      cost_usd,
        'duration_ms',   duration_ms,
        'step_count',    step_count
      )
    )::VARCHAR AS per_agent_tokens
  FROM per_agent_base
  GROUP BY repo_root, change_id
),
```

In addition, T-1 criterion (f) must be strengthened to assert that when 2+ agents are seeded, `json.loads(per_agent_tokens)` contains 2+ keys.

**Severity**: IMPORTANT (caps correctness and spec_compliance dimensions at important_cap=7)

---

### F-2 [INFORMATIONAL] design.md — SKILL.md line number references are stale

**Location**: `design.md` § Components §1 header; T-9 task body "SKILL.md (edit — lines 97–102 to replace `orchestrator cost` invocation)"

**What is wrong**: The cost invocation in `skills/orchestrate/SKILL.md` is at lines 122–128, not lines 97–102. Design and T-9 both cite lines 97–102. This does not affect correctness of the implementation (the implementer will grep for the invocation regardless), but it creates a misleading anchor.

**Severity**: INFORMATIONAL (no cap effect — implementer can find the correct line; does not affect any AC)

---

### F-3 [INFORMATIONAL] design.md — `_cost_main` end-line claim off by one

**Location**: `design.md` § Context, line 11: "lines 149–284"

**What is wrong**: `_ingest_driver_main` starts at line 287 in `bin/orchestrator`, meaning `_cost_main` ends at approximately line 285, not 284. T-11 cites "delete lines 149–284 `_cost_main`". Off by one line.

**Severity**: INFORMATIONAL (same rationale as F-2; implementer will use the actual function boundary)

---

## Score

| Dimension | Score | Rationale |
|---|---|---|
| spec_compliance | 7 | F-1 is an IMPORTANT finding: the design's DDL sketch for `per_agent_tokens_agg` violates FR-2 (one-row-per-change) and AC-1. T-2's "write exactly as sketched" instruction will faithfully reproduce the defect. Caps at important_cap=7. |
| correctness | 7 | F-1 is a semantic SQL defect (not merely a style issue): the CTE produces multi-row output for multi-agent changes, breaking the one-row-per-change contract. T-1 criterion (f) is too weak to catch it. Caps at important_cap=7. |
| security | 9 | Slug-guard specified in FR-9, NFR-2, and design.md § Key Abstractions. SQL injection path blocked. No hardcoded secrets. No new attack surface introduced. All green. |
| simplicity | 9 | Four views, direct shell idiom matching Phase 1 pattern, no new Python module (D-7 honored), no superfluous abstractions. Task count is proportionate to scope (15 tasks for 15 discrete change units). All green. |
| code_quality | 9 | Artifacts are internally consistent. AC↔task mapping complete (all 12 ACs covered). Zero-division guards complete (7/7). Format contract marker present. All green. The F-2/F-3 stale line numbers are informational only. |
| **overall** | **7** | min(7, 7, 9, 9, 9) = **7** |

Score 7 < 9 threshold. No first-pass bonus applies (would require score ≥ 9 anyway).

## Fix Tasks

### T-fix-1: Correct `per_agent_tokens_agg` CTE and strengthen T-1 criterion (f)

**Finding**: F-1 — GROUP BY includes agent_name, causing one-agent JSON per row rather than merged multi-agent JSON; T-1 criterion (f) does not assert multi-agent content.

**Scope**: Two files only — `design.md` and `tasks.md`. No other artifact needs to change.

**Approach**:

1. In `design.md` § Components §1, replace the `per_agent_tokens_agg` CTE block (lines 189–216) with the two-level form shown in Finding F-1:
   - Split into `per_agent_base` (inner, GROUP BY repo_root/change_id/agent_name) and `per_agent_tokens_agg` (outer, GROUP BY repo_root/change_id only).
   - Remove the floating NOTE comment at lines 211–216 (it acknowledges the confusion and will no longer apply).

2. In `tasks.md` T-1 criterion (f), replace:
   > `per_agent_tokens` / `per_step` are strings parseable by `json.loads`
   
   with:
   > `per_agent_tokens` is a string parseable by `json.loads` that, when fixtures seed 2+ distinct agents for the same change_id, contains 2+ top-level keys (one per agent); `per_step` is parseable by `json.loads`

**Verify**:
- `design.md` § Components §1: `per_agent_tokens_agg` no longer has `agent_name` in its outermost GROUP BY.
- `tasks.md` T-1 criterion (f): mentions "2+ distinct agents" and "2+ top-level keys".
- No other artifact (spec.md, discovery.md, state.yaml) requires change — the defect is purely in design sketch and test criterion.

---

## Verdict

**REJECTED** — Overall score 7/10. Minimum is 9/10.

One IMPORTANT finding (F-1) blocks approval: the `per_agent_tokens_agg` CTE in design.md has a structural SQL defect that violates FR-2 (one-row-per-change contract) and that T-2 would faithfully reproduce since it instructs the implementer to write "exactly as sketched." The fix is narrow: two edits to design.md and tasks.md, no changes to spec.md or discovery.md.

Two INFORMATIONAL findings (F-2, F-3) are noted but do not block: stale SKILL.md line numbers and a one-line off-by-one on `_cost_main` end boundary.

**After T-fix-1 is applied**: re-run phase review. If no new findings emerge, expected score is 9/10 (all green base across all dimensions; no first-pass bonus since this is attempt 2).

---

## Re-review 2026-04-21 (F-1 fix)

### Scope of Re-review

Only the two changes described in the re-review brief are assessed: the `per_agent_tokens_agg` CTE restructure in design.md and the T-1(f) criterion strengthening in tasks.md. All other dimensions from attempt 1 are unchanged and carry forward.

### Verification of F-1 Fix

**design.md — `per_agent_tokens_agg` CTE (lines 189–211)**

Confirmed two-level structure:

- Inner subquery (lines 196–209): aggregates per `(repo_root, change_id, agent_name)`, produces `agent_stats` as a `json_object(...)` with total_tokens, input_tokens, output_tokens, cost_usd, duration_ms, step_count.
- Outer SELECT (lines 193–195, 210): `json_group_object(agent_name, agent_stats)::VARCHAR AS per_agent_tokens`, `GROUP BY repo_root, change_id` only — `agent_name` is absent from the outer GROUP BY.
- Comment at line 190–192 explicitly states: "Two-level: inner CTE aggregates per (repo_root, change_id, agent_name); outer query collapses by (repo_root, change_id) emitting one JSON with every agent as a top-level key. Mirrors per_agent_tools_agg below."
- The old broken NOTE comment (previously lines 211–216) is gone.
- Structure now mirrors `per_agent_tools_agg` (lines 212–228), which the prior review approved.

**tasks.md — T-1 criterion (f) (line 16)**

Confirmed strengthened wording: "AND when 2+ distinct agents are seeded for the same change_id, `json.loads(per_agent_tokens)` returns a dict with 2+ top-level keys (one per agent) — NOT one row per agent."

Both changes match exactly what T-fix-1 required.

### Deferred Findings Status

F-2 (stale SKILL.md line numbers) and F-3 (`_cost_main` end-line off-by-one) remain INFORMATIONAL and continue to be deferred per the prior review's own non-blocking classification. No new evidence changes their severity.

### Revised Score

| Dimension | Prior Score | Revised Score | Rationale |
|---|---|---|---|
| spec_compliance | 7 | 9 | F-1 resolved: `per_agent_tokens_agg` DDL now produces one row per `(repo_root, change_id)` with all agents merged — FR-2 one-row-per-change contract is satisfied. No remaining IMPORTANT findings in this dimension. |
| correctness | 7 | 9 | F-1 resolved: the semantic SQL defect (multi-row output for multi-agent changes) is gone. T-1(f) now mandates a 2+ agents / 2+ keys assertion that would catch a regression. No other correctness issues found on re-read. |
| security | 9 | 9 | Unchanged. |
| simplicity | 9 | 9 | Unchanged. The two-level inline subquery pattern is the idiomatic DuckDB form — no added abstraction. |
| code_quality | 9 | 9 | Unchanged. The old NOTE comment is removed; the surviving comment is accurate and helpful. |
| **overall** | **7** | **9** | min(9, 9, 9, 9, 9) = **9** |

### Resolved Findings

- F-1 [IMPORTANT]: RESOLVED — `per_agent_tokens_agg` restructured to two-level form; T-1(f) strengthened.

### Remaining Open Findings

- F-2 [INFORMATIONAL]: still deferred (stale SKILL.md line numbers, no AC impact).
- F-3 [INFORMATIONAL]: still deferred (`_cost_main` end-line off-by-one, implementer will use actual function boundary).

### Verdict

**APPROVED** — Overall score 9/10. Threshold met.

F-1 blocking finding is resolved. The two-level `per_agent_tokens_agg` pattern in design.md is structurally correct and mirrors the approved `per_agent_tools_agg` pattern. T-1(f) now mandates the multi-agent key-count assertion that prevents the original defect from surviving test. No new findings introduced by the inline fix. The two informational findings remain noted but do not block.
