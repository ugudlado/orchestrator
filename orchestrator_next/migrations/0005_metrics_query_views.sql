-- ORC-40: Replace empty legacy tables with views over step_events / feature_report.
-- metrics-query.sh queries features, per_agent_metrics, per_step_metrics.
-- These tables were populated by register-repo.sh reading old state.yaml metrics: blocks,
-- which no longer exist in post-DuckDB state.yaml files. Replace with views so all
-- queries hit the authoritative tables (step_events, feature_report) directly.

-- Drop the empty legacy tables and recreate as views.
-- Use CREATE OR REPLACE VIEW so re-running is safe.

-- Drop legacy TABLES before VIEWs: pre-0005 DBs have BASE TABLE objects; DROP VIEW
-- on a table name fails in DuckDB ("trying to drop type View").
DROP TABLE IF EXISTS per_agent_metrics;
DROP TABLE IF EXISTS per_step_metrics;
DROP TABLE IF EXISTS features;
DROP VIEW IF EXISTS per_agent_metrics;
DROP VIEW IF EXISTS per_step_metrics;
DROP VIEW IF EXISTS features;

-- features — one row per (repo_root, change_id), mirrors feature_report shape
-- payload_json is a JSON object of the full feature_report row for legacy consumers.
CREATE OR REPLACE VIEW features AS
SELECT
  fr.repo_root,
  fr.change_id,
  COALESCE(fr.category, 'feature')  AS schema,
  'completed'                        AS status,
  MIN(se.started_at)::VARCHAR        AS started_at,
  MAX(se.ended_at)::VARCHAR          AS completed_at,
  to_json(fr)::VARCHAR               AS payload_json
FROM feature_report fr
JOIN step_events se ON se.repo_root = fr.repo_root AND se.change_id = fr.change_id
GROUP BY fr.repo_root, fr.change_id, fr.category, to_json(fr);

-- per_agent_metrics — one row per (repo_root, change_id, agent_name)
CREATE OR REPLACE VIEW per_agent_metrics AS
SELECT
  repo_root,
  change_id,
  agent_name                                                           AS agent,
  COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)   AS total_tokens,
  COALESCE(SUM(cost_usd), 0.0)                                        AS cost_usd,
  NULL::INTEGER                                                        AS tool_uses,
  COALESCE(SUM(duration_ms), 0)                                       AS duration_ms,
  COUNT(*)                                                             AS steps
FROM step_events
GROUP BY repo_root, change_id, agent_name;

-- per_step_metrics — one row per (repo_root, change_id, step_id)
CREATE OR REPLACE VIEW per_step_metrics AS
SELECT
  repo_root,
  change_id,
  step_id,
  COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)   AS total_tokens,
  NULL::INTEGER                                                        AS tool_uses,
  COALESCE(SUM(duration_ms), 0)                                       AS duration_ms,
  COALESCE(SUM(cost_usd), 0.0)                                        AS cost_usd
FROM step_events
GROUP BY repo_root, change_id, step_id;
