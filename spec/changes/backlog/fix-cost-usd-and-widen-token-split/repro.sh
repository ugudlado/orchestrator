#!/usr/bin/env bash
# repro.sh — Reproduce cost_usd=0 bug in per_agent_metrics
# Run from any directory. Requires: duckdb on PATH, ORCHESTRATOR_HOME set or defaulting to ~/code/orchestrator.

METRICS_DB="${ORCHESTRATOR_HOME:-$HOME/code/orchestrator}/metrics.duckdb"
SCRIPT="${ORCHESTRATOR_HOME:-$HOME/code/orchestrator}/config/scripts/compute-swe-metrics.sh"

echo "=== 1. DuckDB: per_agent_metrics cost_usd totals (expect non-zero if fixed) ==="
duckdb "$METRICS_DB" "SELECT agent, SUM(total_tokens) AS tokens, SUM(cost_usd) AS cost FROM per_agent_metrics GROUP BY agent ORDER BY tokens DESC LIMIT 10;"

echo ""
echo "=== 2. Row count: total rows vs zero-cost rows (expect zero_cost_rows = 0 if fixed) ==="
duckdb "$METRICS_DB" "SELECT COUNT(*) AS total_rows, SUM(CASE WHEN cost_usd = 0 OR cost_usd IS NULL THEN 1 ELSE 0 END) AS zero_cost_rows FROM per_agent_metrics;"

echo ""
echo "=== 3. Assertion: any row with total_tokens > 10000 AND cost_usd = 0 is a failure ==="
duckdb "$METRICS_DB" "SELECT COUNT(*) AS failing_rows FROM per_agent_metrics WHERE total_tokens > 10000 AND (cost_usd = 0 OR cost_usd IS NULL);"

echo ""
echo "=== 4. compute-swe-metrics.sh PER_AGENT_TOKENS awk block (lines 421-453) ==="
sed -n '421,453p' "$SCRIPT"

echo ""
echo "=== 5. agent_pricing table (must exist and be seeded for fix to work) ==="
duckdb "$METRICS_DB" "SELECT agent, model, input_per_1m, output_per_1m FROM agent_pricing ORDER BY agent;" 2>/dev/null || echo "ERROR: agent_pricing table missing or empty"
