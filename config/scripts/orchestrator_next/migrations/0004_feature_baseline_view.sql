-- ORC-1: feature_baseline view — per-repo median cost for delta reporting.
-- Used by cost-report.sh --tail and the Median Delta section of the full report.

CREATE OR REPLACE VIEW feature_baseline AS
SELECT
  repo_root,
  change_id,
  cost_usd,
  duration_ms,
  step_count,
  MEDIAN(cost_usd)    OVER (PARTITION BY repo_root)    AS median_cost_usd,
  MEDIAN(duration_ms) OVER (PARTITION BY repo_root)    AS median_duration_ms,
  MEDIAN(step_count)  OVER (PARTITION BY repo_root)    AS median_step_count,
  COUNT(*) OVER (PARTITION BY repo_root) AS repo_feature_count
FROM feature_report
WHERE cost_usd > 0;
