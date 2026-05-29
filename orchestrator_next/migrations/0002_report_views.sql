-- Phase 3 of workflow-engine-as-state-machine.
-- Replaces metrics_report.aggregate_metrics() and cost_report._totals/_per_phase/_per_agent
-- with pure SQL views. Consumer scripts (compute-swe-metrics.sh,
-- read-sub-state-metrics.sh, cost-report.sh) read these via `duckdb -json -readonly`.

-- ---------------------------------------------------------------------------
-- 1. feature_report — one row per (repo_root, change_id)
-- ---------------------------------------------------------------------------
-- Matches aggregate_metrics() output shape (metrics-schema.md § feature schema).
-- LEFT JOIN on feature_metrics (UC-E2: missing row must not drop the feature).
-- Stringified JSON columns: per_agent_tokens, per_agent_tools, per_tool_uses, per_step.

CREATE OR REPLACE VIEW feature_report AS
WITH base AS (
  -- Totals from step_events, per change
  SELECT
    se.repo_root,
    se.change_id,
    COALESCE(SUM(se.cost_usd), 0.0)                        AS cost_usd,
    COALESCE(SUM(se.input_tokens), 0)                      AS input_tokens,
    COALESCE(SUM(se.output_tokens), 0)                     AS output_tokens,
    COALESCE(SUM(se.cache_creation_input_tokens), 0)       AS cache_creation_input_tokens,
    COALESCE(SUM(se.cache_read_input_tokens), 0)           AS cache_read_input_tokens,
    COALESCE(SUM(se.input_tokens), 0)
      + COALESCE(SUM(se.output_tokens), 0)
      + COALESCE(SUM(se.cache_creation_input_tokens), 0)   AS total_tokens,
    COALESCE(SUM(se.turns), 0)                             AS turns,
    COALESCE(SUM(se.duration_ms), 0)                       AS duration_ms,
    COUNT(*)                                               AS step_count,
    COALESCE(SUM(CASE WHEN se.attempt > 1 THEN se.cost_usd ELSE 0.0 END), 0.0) AS rework_cost,
    COALESCE(SUM(se.cost_usd), 0.0)                        AS rework_denom
  FROM step_events se
  GROUP BY se.repo_root, se.change_id
),
dom_model AS (
  -- Dominant model: max SUM(input_tokens) per change
  SELECT repo_root, change_id, model
  FROM (
    SELECT
      repo_root, change_id, model,
      SUM(input_tokens) AS input_sum,
      ROW_NUMBER() OVER (
        PARTITION BY repo_root, change_id
        ORDER BY SUM(input_tokens) DESC NULLS LAST
      ) AS rn
    FROM step_events
    WHERE model IS NOT NULL
    GROUP BY repo_root, change_id, model
  ) ranked
  WHERE rn = 1
),
priced AS (
  -- Most recent pricing row per model_id
  SELECT
    model_id,
    input_usd, output_usd, cache_read_usd, cache_creation_usd,
    ROW_NUMBER() OVER (PARTITION BY model_id ORDER BY effective_from DESC) AS rn
  FROM pricing
),
tool_count AS (
  SELECT repo_root, change_id, COUNT(*) AS tool_calls_count
  FROM tool_calls
  GROUP BY repo_root, change_id
),
per_agent_tokens_agg AS (
  -- Two-level: inner CTE aggregates per (repo_root, change_id, agent_name);
  -- outer query collapses by (repo_root, change_id) emitting one JSON with
  -- every agent as a top-level key. Mirrors per_agent_tools_agg below.
  SELECT
    repo_root, change_id,
    json_group_object(agent_name, agent_stats)::VARCHAR AS per_agent_tokens
  FROM (
    SELECT
      repo_root, change_id, agent_name,
      json_object(
        'total_tokens',  COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0),
        'input_tokens',  COALESCE(SUM(input_tokens),0),
        'output_tokens', COALESCE(SUM(output_tokens),0),
        'cost_usd',      COALESCE(SUM(cost_usd),0.0),
        'duration_ms',   COALESCE(SUM(duration_ms),0),
        'step_count',    COUNT(*)
      ) AS agent_stats
    FROM step_events
    GROUP BY repo_root, change_id, agent_name
  ) t
  GROUP BY repo_root, change_id
),
per_agent_tools_agg AS (
  SELECT
    tc.repo_root, tc.change_id,
    json_group_object(agent_name, tool_map)::VARCHAR AS per_agent_tools
  FROM (
    SELECT
      repo_root, change_id, agent_name,
      json_group_object(tool_name, calls) AS tool_map
    FROM (
      SELECT repo_root, change_id, agent_name, tool_name, COUNT(*) AS calls
      FROM tool_calls
      GROUP BY repo_root, change_id, agent_name, tool_name
    )
    GROUP BY repo_root, change_id, agent_name
  ) tc
  GROUP BY tc.repo_root, tc.change_id
),
per_tool_uses_agg AS (
  SELECT
    repo_root, change_id,
    json_group_object(tool_name, calls)::VARCHAR AS per_tool_uses
  FROM (
    SELECT repo_root, change_id, tool_name, COUNT(*) AS calls
    FROM tool_calls
    GROUP BY repo_root, change_id, tool_name
  )
  GROUP BY repo_root, change_id
),
per_step_agg AS (
  SELECT
    step_data.repo_root, step_data.change_id,
    json_group_object(
      step_id,
      json_object(
        'total_tokens', total_tokens,
        'tool_uses',    tool_uses,
        'duration_ms',  duration_ms,
        'executions',   executions
      )
    )::VARCHAR AS per_step
  FROM (
    SELECT
      se.repo_root, se.change_id, se.step_id,
      COALESCE(SUM(se.input_tokens),0) + COALESCE(SUM(se.output_tokens),0) AS total_tokens,
      COALESCE(SUM(se.duration_ms),0) AS duration_ms,
      COUNT(*) AS executions,
      COALESCE((
        SELECT COUNT(*) FROM tool_calls tc
        WHERE tc.repo_root = se.repo_root
          AND tc.change_id = se.change_id
          AND tc.step_id   = se.step_id
      ), 0) AS tool_uses
    FROM step_events se
    GROUP BY se.repo_root, se.change_id, se.step_id
  ) step_data
  GROUP BY step_data.repo_root, step_data.change_id
)
SELECT
  b.repo_root,
  b.change_id,
  -- totals
  b.cost_usd,
  b.input_tokens,
  b.output_tokens,
  b.cache_creation_input_tokens,
  b.cache_read_input_tokens,
  b.total_tokens,
  b.turns,
  b.duration_ms,
  b.step_count,
  -- rework ratio (denom-zero guard)
  CASE WHEN b.rework_denom = 0 THEN 0.0
       ELSE b.rework_cost / b.rework_denom END AS rework_ratio,
  -- dominant model + pricing (LEFT JOIN; fallback __default__ applied by shell if model IS NULL)
  dm.model                                                AS model,
  COALESCE(p.input_usd,      pd.input_usd,      15.0)     AS pricing_input_usd,
  COALESCE(p.output_usd,     pd.output_usd,     75.0)     AS pricing_output_usd,
  COALESCE(p.cache_read_usd, pd.cache_read_usd,  1.5)     AS pricing_cache_read_usd,
  COALESCE(p.cache_creation_usd, pd.cache_creation_usd, 18.75) AS pricing_cache_creation_usd,
  -- gross_usd = (input + cache_create + cache_read) * input_rate / 1M + output * output_rate / 1M
  (
    (b.input_tokens + b.cache_creation_input_tokens + b.cache_read_input_tokens)
      * COALESCE(p.input_usd, pd.input_usd, 15.0) / 1000000.0
    + b.output_tokens * COALESCE(p.output_usd, pd.output_usd, 75.0) / 1000000.0
  )                                                        AS gross_usd,
  -- tool_calls_count (LEFT JOIN; zero if no tool_calls rows)
  COALESCE(tc.tool_calls_count, 0)                        AS tool_calls_count,
  -- category / complexity (LEFT JOIN both)
  COALESCE(fm.schema_name, fc.schema_name, 'feature')     AS category,
  fc.complexity                                            AS complexity,
  -- feature_metrics passthroughs (NULL-safe via LEFT JOIN)
  fm.tasks_total, fm.tasks_planned, fm.tasks_added,
  fm.tasks_completed, fm.tasks_failed,
  fm.resolve_rate, fm.pass_at_1, fm.pass_at_2,
  fm.regressions, fm.regression_rate,
  fm.retries_total, fm.human_interventions,
  fm.files_changed, fm.insertions, fm.deletions,
  fm.total_commits, fm.rework_commits, fm.rework_rate,
  fm.review_scores_json, fm.review_score_avg,
  fm.wall_clock_minutes,
  -- benchmarks with denom-zero guards
  CASE WHEN COALESCE(fm.tasks_total,0) = 0 THEN 0.0
       ELSE b.cost_usd / fm.tasks_total END                AS cost_per_task_usd,
  CASE WHEN COALESCE(fm.tasks_completed,0) = 0 THEN 0.0
       ELSE b.cost_usd / fm.tasks_completed END            AS cost_per_resolution_usd,
  CASE WHEN COALESCE(fm.tasks_total,0) = 0 THEN 0
       ELSE CAST(b.total_tokens / fm.tasks_total AS BIGINT) END AS tokens_per_task,
  CASE WHEN COALESCE(fm.tasks_completed,0) = 0 THEN 0
       ELSE CAST(b.total_tokens / fm.tasks_completed AS BIGINT) END AS tokens_per_resolution,
  CASE WHEN b.output_tokens = 0 THEN 0.0
       ELSE ROUND((b.input_tokens + b.cache_creation_input_tokens)::DOUBLE / b.output_tokens, 4) END AS input_output_ratio,
  CASE WHEN (b.input_tokens + b.cache_creation_input_tokens + b.cache_read_input_tokens) = 0 THEN 0.0
       ELSE ROUND(b.cache_read_input_tokens::DOUBLE /
                  (b.input_tokens + b.cache_creation_input_tokens + b.cache_read_input_tokens), 4) END AS cache_hit_rate,
  -- stringified JSON nested columns
  COALESCE(pat.per_agent_tokens, '{}')                     AS per_agent_tokens,
  COALESCE(pato.per_agent_tools, '{}')                     AS per_agent_tools,
  COALESCE(ptu.per_tool_uses,    '{}')                     AS per_tool_uses,
  COALESCE(ps.per_step,           '{}')                    AS per_step
FROM base b
LEFT JOIN dom_model dm
  ON dm.repo_root = b.repo_root AND dm.change_id = b.change_id
LEFT JOIN priced p
  ON p.model_id = dm.model AND p.rn = 1
LEFT JOIN priced pd
  ON pd.model_id = '__default__' AND pd.rn = 1
LEFT JOIN tool_count tc
  ON tc.repo_root = b.repo_root AND tc.change_id = b.change_id
LEFT JOIN feature_metrics fm
  ON fm.repo_root = b.repo_root AND fm.change_id = b.change_id
LEFT JOIN feature_complexity fc
  ON fc.repo_root = b.repo_root AND fc.change_id = b.change_id
LEFT JOIN per_agent_tokens_agg pat
  ON pat.repo_root = b.repo_root AND pat.change_id = b.change_id
LEFT JOIN per_agent_tools_agg pato
  ON pato.repo_root = b.repo_root AND pato.change_id = b.change_id
LEFT JOIN per_tool_uses_agg ptu
  ON ptu.repo_root = b.repo_root AND ptu.change_id = b.change_id
LEFT JOIN per_step_agg ps
  ON ps.repo_root = b.repo_root AND ps.change_id = b.change_id;


-- ---------------------------------------------------------------------------
-- 2. phase_report — one row per (repo_root, change_id, phase)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW phase_report AS
SELECT
  repo_root,
  change_id,
  phase,
  COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
  COALESCE(SUM(input_tokens), 0)  AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(duration_ms), 0)   AS duration_ms,
  COUNT(*)                         AS step_count,
  MIN(started_at)                  AS first_seen
FROM step_events
GROUP BY repo_root, change_id, phase
ORDER BY repo_root, change_id, first_seen ASC, phase ASC;


-- ---------------------------------------------------------------------------
-- 3. agent_report — one row per (repo_root, change_id, agent_name)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW agent_report AS
SELECT
  repo_root,
  change_id,
  agent_name,
  COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
  COALESCE(SUM(input_tokens), 0)  AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(duration_ms), 0)   AS duration_ms,
  COUNT(*)                         AS step_count
FROM step_events
GROUP BY repo_root, change_id, agent_name
ORDER BY repo_root, change_id, agent_name ASC;


-- ---------------------------------------------------------------------------
-- 4. repo_report — one row per (repo_basename, change_id)
-- ---------------------------------------------------------------------------
-- Note: GROUP BY 1 is used because DuckDB requires the GROUP BY expression to
-- exactly match the SELECT expression string. Using the column alias
-- repo_basename in GROUP BY is not supported in DuckDB; GROUP BY 1 references
-- the first SELECT column by position.

CREATE OR REPLACE VIEW repo_report AS
SELECT
  regexp_extract(repo_root, '[^/]+$') AS repo_basename,
  change_id,
  COALESCE(SUM(cost_usd), 0.0)        AS cost_usd,
  COALESCE(SUM(input_tokens), 0)      AS input_tokens,
  COALESCE(SUM(output_tokens), 0)     AS output_tokens,
  COUNT(*)                             AS step_count,
  MIN(started_at)                      AS first_seen
FROM step_events
GROUP BY 1, change_id
ORDER BY repo_basename, first_seen ASC, change_id ASC;
