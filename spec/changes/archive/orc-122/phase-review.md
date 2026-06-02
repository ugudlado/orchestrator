# Phase Review — ORC-122: orchestrator graph cost/token/attempt overlay

**Schema:** implement  
**Phase:** implement  
**Verdict:** PASS  
**Overall score:** 9 / 10

---

## Verification Commands

All verify commands executed and exited 0:

```
python3 -m pytest orchestrator_next/tests/test_graph_overlay.py orchestrator_next/tests/test_graph_workflow.py -v -k "not telemetry"
→ 16 passed, 1 deselected

python3 -c "import ast; ast.parse(open('bin/orchestrator').read())"
→ valid Python

orchestrator graph feature | grep -q 'workflow: feature'
→ no-slug path intact

python3 orchestrator_next/tests/_overlay_html_smoke.py
→ OK: overlay HTML smoke passed

python3 -c "import sys; src=open('orchestrator_next/tests/test_graph_overlay.py').read(); sys.exit(1 if 'xfail' in src else 0)"
→ xfail markers absent
```

Pre-existing test failure confirmed on main (not introduced by this feature):
- `test_capture_test_baseline_script_uses_step_dir_env` — FileNotFoundError on `config/steps/capture-test-baseline/script.sh`, pre-existing on main branch.

---

## Acceptance Criteria Verification

**AC-1:** `orchestrator graph <schema> <slug>` reads all state files and overlays metrics.  
→ PASS. `_aggregate_step_metrics` globs `*_state.yaml` from the slug dir, merges `step_history` across files. CLI routes to `render_workflow_graph_with_overlay` when slug is provided (`bin/orchestrator:203–211`). Smoke test builds a synthetic fixture and asserts overlay labels are present.

**AC-2:** Agent steps show `N tok · $X.XX` in node label; script-only steps render plain.  
→ PASS. `graph.py:193–196`: emits `"{sid}\\n{tokens:,} tok · ${cost:.2f}"` when `tokens > 0` in overlay mode, else plain `"{sid}"`. Both branches exercised by `test_overlay_annotates_agent_step_labels` and `test_overlay_script_only_steps_plain`.

**AC-3:** Steps with `attempts > 1` emit `style <node_id> fill:#f90` line.  
→ PASS. `graph.py:222–226`: only emits the `style` directive when `attempts > 1`. Single-attempt steps emit no style line. Verified by `test_overlay_retry_style_for_multiple_attempts`.

**AC-4:** No-slug path renders byte-for-byte the same plain schema DAG.  
→ PASS. `render_workflow_graph` is a thin wrapper calling `_render_schema_graph(schema, {})`. The empty-dict flag gates all overlay output (click callbacks, style lines). Verified by `test_render_workflow_graph_unchanged_no_overlay` — golden-string equality test.

**AC-5:** `--html` flag still works with the overlay.  
→ PASS. Both slug and no-slug paths return `(mermaid_src, step_data)`. The `html_mode` branch (`bin/orchestrator:221–`) feeds both into `render_html` unchanged. Smoke test asserts `'tok ·'` in HTML, step_id key present in `STEP_DATA` JSON, and `showStep` click binding.

---

## Dimension Scores

| Dimension | Score | Notes |
|---|---|---|
| spec_compliance | 9 | All 5 ACs verified with evidence |
| correctness | 9 | 16/16 tests pass; aggregation (sum tokens/cost, max attempt) matches design contract |
| security | 9 | No unsafe eval/exec/subprocess; all external data via `yaml.safe_load` with dict-type guard |
| simplicity | 9 | Empty-dict as overlay switch is clean. `_render_schema_graph` shared path avoids duplication. `render_workflow_graph` stays as a pure thin wrapper |
| code_quality | 9 | No TODOs/FIXMEs/placeholders; xfail markers fully removed; pre-existing test failure is pre-existing on main (not introduced) |

**Overall: 9**  
First-pass bonus (+1 → 10) not awarded: no evidence of first-attempt-no-retry (workflow had multiple tasks), per scoring rule.

---

## Baseline Comparison

No archived `implement` schema state.yaml entries found with `review_score_avg`. This is the first implement-schema run — no baseline available. Skipping silently per instructions.

---

## Findings

No critical findings.  
No important findings.

---

## Quarantine Review

No `quarantine_events` in state.yaml.

---

## Summary

ORC-122 implements the `orchestrator graph <schema> <slug>` cost/token/attempt overlay cleanly. The implementation:

- Adds `_aggregate_step_metrics` to glob and merge state files
- Extends `_render_schema_graph` with a metrics overlay path gated by an empty-dict flag (preserving byte-for-byte parity for the no-slug path)
- Routes the CLI's slug branch to `render_workflow_graph_with_overlay`
- Removes all TDD xfail markers in T-4 as required
- Provides a self-contained smoke test that doesn't depend on gitignored `.orchestrator/` runtime data

All ACs pass. Phase gates met. Ready to advance.
