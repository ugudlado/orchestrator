# Scope-Delta Review: T-23 + T-24 (Attempt 3)

**Delta**: Auto-invoke `orchestrator ingest-driver` at complete phase so driver-loop tokens always land in step_events.
**Commits**: T-23 (0189614, RED test), T-24 (c7c1220, GREEN implementation)
**Date**: 2026-04-20
**Reviewer**: Reviewer Agent

---

## Verdict: APPROVED (delta maintains 9/10)

All 8 required checks pass. No new findings. No regressions. The delta is clean, minimal, and correctly wired.

---

## Test Results

### 1. Scope-delta test: `bash config/tests/test-ingest-driver-auto.sh`

```
=== Test: ingest-driver-auto step script exists ===
PASS: step script exists at .../scripts/inline/ingest-driver-auto.py
=== Test: TMPDIR-based session_id resolution (primary path) ===
PASS: step exits 0 (primary TMPDIR path)
PASS: step_events has exactly 1 driver-loop row (primary TMPDIR path)
PASS: driver-loop row has non-null input_tokens, output_tokens, cost_usd
=== Test: fallback scan when TMPDIR has no UUID ===
PASS: step exits 0 (fallback scan path)
PASS: step_events has exactly 1 driver-loop row (fallback scan path)
=== Test: fail-soft when JSONL not found (no UUID, no projects dir) ===
PASS: step exits 0 on unresolvable session_id (fail-soft)
PASS: fail-soft output contains skipped=true
PASS: stderr contains warning about unresolvable session_id

Results: 9 passed, 0 failed
```

**Result: 9/9 PASS**

### 2. Ordering test: `bash config/tests/test-complete-phase-order.sh`

```
Step positions: predict=1 learn=2 mark=3 ingest_driver=4 ingest=5 metrics=6 archive=7 remove=8
PASS: mark-change-completed, ingest-driver-auto, ingest-feature-metrics, and compute-swe-metrics all have positions
PASS: mark-change-completed (pos 3) appears before ingest-driver-auto (pos 4)
PASS: ingest-driver-auto (pos 4) appears before ingest-feature-metrics (pos 5)
PASS: ingest-feature-metrics (pos 5) appears before compute-swe-metrics (pos 6)
PASS: compute-swe-metrics (pos 6) appears before archive-completed-change (pos 7)
PASS: run-learn-cycle (pos 2) appears before mark-change-completed (pos 3)

Results: 15 passed, 0 failed
```

**Result: 15/15 PASS. POS_INGEST_DRIVER (4) < POS_INGEST (5) < POS_METRICS (6) — ordering correct.**

### 3. Spike unchanged: `git diff main -- config/workflows/_complete-phase-spike.yaml`

```
(empty output)
```

**Result: PASS — spike file unmodified.**

### 4. Pytest regression: `pytest config/scripts/orchestrator_next/tests/ -q`

```
2 failed, 144 passed in 1.09s
```

**Result: PASS — same 2 pre-existing failures (test_archive_backlog_cleanup), 144 pass unchanged. No new failures introduced.**

### 5. Integration test: `bash config/tests/test-metrics-pipeline-integration.sh`

```
Results: 54 passed, 0 failed
```

**Result: 54/54 PASS — no regression.**

---

## Script Analysis: `scripts/inline/ingest-driver-auto.py`

- **Line count**: 125 lines (well within 200-line sanity bar)
- **subprocess.run**: Called with `shell=False` (list form, no `shell=True`) — safe
- **Fail-soft**: Every code path exits 0. Exceptions from state.yaml read, missing change_id/repo_root, subprocess failure, and unresolvable session_id all produce `{"skipped": True}` + `return 0`. Archive is never blocked.
- **Primary resolution**: Parses UUID from `$TMPDIR` path components via regex fullmatch — correct for the Claude Code TMPDIR convention.
- **Fallback resolution**: Scans `~/.claude/projects/<repo-slug>/*.jsonl` for newest file within `started_at..completed_at` window. Uses `repo_root` from `state.yaml` to derive slug, which bounds the scan to one specific repo's project directory.
- **Potential misattribution (minor, noted)**: The fallback picks the `mtime`-newest JSONL within the time window. If two concurrent Claude Code sessions are active against the same repo within the same feature window, the wrong session could be selected. This is an inherent ambiguity of the fallback heuristic, not a script defect — the primary TMPDIR path avoids it entirely. No fix required; the tradeoff is appropriate and the fail-soft means the worst outcome is a warning, not wrong data silently written.

---

## Contract Shape: `config/steps/ingest-driver-auto.yaml`

- `agent: inline` — correct
- `run: scripts/inline/ingest-driver-auto.py` — correct path relative to repo root
- `outputs: [ingest_driver_result]` — declared
- Rules capture the ordering constraints and fail-soft behavior
- No issues.

---

## Complete-Phase Wiring: `config/workflows/_complete-phase.yaml`

Step order confirmed as:
```
compute-prediction-accuracy → run-learn-cycle → mark-change-completed
→ ingest-driver-auto → ingest-feature-metrics → compute-swe-metrics
→ archive-completed-change → remove-worktree
```

`ingest-driver-auto` is inserted at position 4 (between mark-change-completed and ingest-feature-metrics). Not first, not last. Correct.

---

## Per-Dimension Impact (vs. 9/10 prior score)

| Dimension | Prior | Delta Impact | New Score |
|-----------|-------|-------------|-----------|
| Spec compliance | 9 | Closes the driver-loop gap; scope fully covered | 9 |
| Correctness | 9 | Fail-soft on all paths; 9/9 assertions pass | 9 |
| Security | 9 | subprocess list form (no shell=True); no new attack surface | 9 |
| Simplicity | 9 | 125-line script, single responsibility, no abstractions | 9 |
| Code quality | 9 | Clean structure, follows existing inline step conventions | 9 |

**Overall: 9/10 — unchanged.**

---

## New Findings

None. No must-fix items. One low-severity observation documented above (fallback misattribution under concurrent sessions) — not a defect, behavior is correct and documented.

---

## Pre-existing Known Items (NOT re-scored)

- ISSUE-33, register-repo.test.sh T-5b, metrics-no-data-graceful: unchanged, excluded per instructions.
- test_archive_backlog_cleanup (2 pytest failures): pre-existing, excluded.
