# Tasks — Unify metric collection across all workflow schemas

- [x] T-1: Write test for `_complete-phase-spike.yaml` structure (RED)
  Verify: `bash workflows/__tests__/complete-phase-spike.test.sh` fails — test asserts the yaml's `steps:` contains exactly `compute-swe-metrics` and `archive-completed-change`, and does NOT contain `run-learn-cycle` or `compute-prediction-accuracy`. File does not exist yet → test fails for the right reason.

- [x] T-2: Create `workflows/_complete-phase-spike.yaml` (GREEN)
  Verify: `yq '.steps' workflows/_complete-phase-spike.yaml` outputs exactly `[compute-swe-metrics, archive-completed-change]`; T-1 test passes.
  depends: T-1

- [x] T-3: Write test for `spike.yaml` complete-phase wiring (RED)
  Verify: `bash workflows/__tests__/spike.test.sh` fails — test resolves the `complete` phase by following `include: _complete-phase-spike` and asserts it resolves to the two-step list. Fails because `spike.yaml` has no `complete` phase yet.
  depends: T-2

- [x] T-4: Add `complete` phase to `workflows/spike.yaml` (GREEN)
  Verify: `yq '.phases.complete.include' workflows/spike.yaml` outputs `_complete-phase-spike`; T-3 test passes.
  depends: T-3

- [x] T-5: Write test for `compute-swe-metrics.sh` schema-dispatch on spike fixture (RED)
  Verify: `bash scripts/__tests__/compute-swe-metrics.test.sh` fails — test uses fixture state.yaml with `schema: spike`, runs the script, and asserts the output block has `resolution.resolve_rate: ~` (explicit null), contains `tokens:`, `cost:`, `churn:`, and does NOT contain `review_scores:`. Also asserts feature fixture still produces the existing byte-identical output. Fails because dispatch not yet implemented.
  depends: T-4

- [x] T-6: Implement schema-dispatch in `scripts/compute-swe-metrics.sh` (GREEN)
  Verify: T-5 test passes. `diff <(bash scripts/compute-swe-metrics.sh fixtures/state.feature.yaml) fixtures/metrics.feature.golden.yaml` is empty (byte-identical for feature). Running on the spike fixture produces null resolution fields and no `review_scores:` key (checked with `yq 'has("review_scores")'` returning false).
  depends: T-5

- [x] T-7: Write test for autopilot per-iteration metrics read helper (RED)
  Verify: `bash steps/__tests__/autopilot-iterate-metrics.test.sh` fails — test creates a fake `~/.workflows/<slug>/state.yaml` with known `step_history[].usage` values; invokes the autopilot-iterate read-sub-state helper; asserts the extracted iteration metrics block has `tokens.total` equal to the fixture sum. Also tests the archive-fallback path (when active path is absent, reads from `spec/changes/archive/<slug>/state.yaml`). Fails because helper does not exist.
  depends: T-6

- [x] T-8: Extend `steps/autopilot-iterate.yaml` to append per-iteration metrics (GREEN)
  Verify: T-7 test passes. After running a mock iteration, `yq '.iterations[0].metrics.tokens.total' spec/changes/archive/autopilot-<session_id>/state.yaml` matches the sub-feature's summed usage. Active-path and archive-fallback paths both work.
  depends: T-7

- [x] T-9: Write test for autopilot session close — aggregate metrics (RED)
  Verify: `bash workflows/__tests__/autopilot-session-close.test.sh` fails — test seeds `spec/changes/archive/autopilot-<session_id>/state.yaml` with 3 iteration records (2 completed, 1 failed, with known token/cost sums); invokes the report-phase rollup; asserts the finalized file has top-level `schema: autopilot`, `status: completed`, `metrics.tokens.total` equal to the sum across iterations, `metrics.resolution.iterations_completed: 2`, `iterations_failed: 1`, and no `review_scores:` key. Fails because rollup not implemented.
  depends: T-8

- [x] T-10: Implement session-close rollup in autopilot report phase (GREEN)
  Verify: T-9 test passes. Running a real 2-iteration autopilot session produces `spec/changes/archive/autopilot-<session_id>/state.yaml` with populated top-level `metrics:` block; telemetry's existing glob `spec/changes/archive/*/state.yaml` already covers this path (verified by running telemetry skill and confirming one rendered entry per archived session with no crash).
  depends: T-9

- [x] T-11: Write `config/steps/contracts/metrics-schema.md`
  Verify: `test -f config/steps/contracts/metrics-schema.md`; file contains sections "Field Registry", "Per-Schema Variants", "Consumer Contract"; documents every field emitted by `compute-swe-metrics.sh`; explicitly lists `review_scores: omitted` for spike/autopilot and `resolution.*: null` for spike/autopilot; `markdownlint config/steps/contracts/metrics-schema.md` exits 0.
  depends: T-10

- [x] T-12: Add `§ Metrics Schema` pointer to `config/steps/CONVENTIONS.md`
  Verify: `grep -q '## Metrics Schema' config/steps/CONVENTIONS.md`; the section contains a link/pointer to `contracts/metrics-schema.md`.
  depends: T-11

- [x] T-13: Update `agents/workflow-improver.md` for spike/autopilot categories
  Verify: `grep -E 'spike|autopilot' agents/workflow-improver.md` shows the per-feature metrics table notes `resolution.*` is N/A for those categories.
  depends: T-12

- [x] T-14: End-to-end regression — feature workflow metrics byte-identical
  Verify: Run a tiny feature workflow end-to-end (existing dry-run harness). Archived `state.yaml`'s `metrics:` block diffs to zero against the pre-change golden fixture captured before T-6. `diff <(yq '.metrics' spec/changes/archive/<test-slug>/state.yaml) fixtures/metrics.feature.golden.yaml` is empty.
  depends: T-13

- [x] T-15: End-to-end integration — spike + autopilot produce reduced metrics
  Verify: Run a tiny spike end-to-end; archived `state.yaml` exists at `spec/changes/archive/<spike-slug>/state.yaml` with `metrics.tokens.total > 0`, `metrics.resolution.resolve_rate: ~`, no `review_scores:` key. Run a 1-iteration autopilot session; `spec/changes/archive/autopilot-<session_id>/state.yaml` exists with `schema: autopilot`, `status: completed`, `metrics.tokens.total > 0`, `metrics.resolution.iterations_completed: 1`. Telemetry skill reads both archives without error.
  depends: T-14

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- TDD: each implementation task (T-2, T-4, T-6, T-8, T-10) has a preceding test task -->
<!-- T-11..T-13 are doc-only; T-14/T-15 are regression + integration gates -->
