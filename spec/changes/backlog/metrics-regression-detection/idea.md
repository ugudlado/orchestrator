# Metrics Regression Detection + Autopilot Breaker

## Idea
Turn the metrics stack from a passive ledger into an active guardrail. Detect feature-level and step-level regressions against rolling baselines, surface them in `/telemetry`, and stop `/autopilot` from compounding damage when the last 3 runs all regressed.

## Evidence
- `execute-next-task` averaged ~19 min across 2 samples — no rolling baseline exists to flag drift.
- The `/learn` skill references "steps taking >2× average" but nothing produces that signal.
- Prior commit 62166d6 fixed a PyYAML indent bug that had silently corrupted step_history parsing — a drift alarm would have caught it days earlier.
- The cost_usd=0 regression that motivated the observability batch also went undetected for multiple features.

## Fix
1. New step `compute-metrics-regressions.yaml`, run after `compute-swe-metrics`.
2. New table `metrics_anomalies`:
   ```
   change_id, anomaly_type, metric, observed, baseline_median, ratio, detected_at
   ```
3. Flag:
   - feature `cost_usd` > 1.5× 30-day median (same schema)
   - step `duration_ms` > 2× median for same `step_id`
   - single-agent token spike > 2× median
4. Surface top anomalies in `/telemetry`.
5. Autopilot breaker: the iterate step refuses to pick new work if the last 3 completed features each appear in `metrics_anomalies`; writes `stop_reason: regression_breaker` to checkpoint.

## Why Now
Prerequisite: fix-cost-usd-and-widen-token-split (baselines on zeros are meaningless) and backfill-step-history-jsonl (needs full history). This caps the observability arc — once it lands, future regressions self-report.

## Priority
- User value: 9/10
- Strategic fit: 8/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 8.2**
