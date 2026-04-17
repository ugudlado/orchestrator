# /learn Skill Should Query metrics.duckdb Instead of Globbing Archive YAML

## Idea

The `/learn` skill (`skills/learn/SKILL.md`) was designed before the cross-repo
metrics index existed. It still globs `spec/changes/archive/*/state.yaml` and
parses YAML in bash for:

- §2b cross-feature retry analysis (last 10 features, this repo only)
- §5b rule effectiveness (per-rule retry correlation, this repo only)
- §5c adaptive quality bar (avg review score + retry rate over last 5, this repo only)

Now that `$ORCHESTRATOR_HOME/metrics.duckdb` exists (shipped in
`cross-repo-metrics-duckdb`), `/learn` should prefer SQL queries against the
DuckDB index. Two big wins:

1. **Cross-repo learning**: rule effectiveness across the whole orchestrator
   fleet, not just the current repo. A rule that's been shipping with `hits: 0`
   in 6 different repos is more clearly dead than one with `hits: 0` in just
   the current repo.
2. **One source of truth**: the same metrics power `/telemetry`, `/learn`, and
   `workflow-improver`. No drift between consumers.

## Scope

- Update `skills/learn/SKILL.md` §2b, §5b, §5c to query
  `$ORCHESTRATOR_HOME/metrics.duckdb` via `duckdb -csv ... | awk` (or similar)
  for cross-repo aggregations
- Keep the file-glob fallback when `metrics.duckdb` doesn't exist (newly-cloned
  orchestrator install before bootstrap runs)
- Cycle metrics in §5 stay file-based (`.claude/metrics.jsonl`) OR move to a
  `cycles` table in DuckDB — design call

## Out of scope

- `/telemetry` rewrite (separate ticket: `telemetry-dashboard-real`)
- Changes to `register-repo.sh` schema
- Cross-repo retry pattern routing (workflow-improver still emits per-repo
  fixes by default)

## Why Now

This is the natural follow-up to `cross-repo-metrics-duckdb`. The producer
exists; the most valuable consumer (`/learn`) hasn't caught up. Surfaced
during the cycle-11 learn run for `cross-repo-metrics-duckdb` itself —
the user pointed out that the running learn cycle was still globbing YAML
even though the DuckDB index was now live.

## Priority

- User value: 6/10
- Strategic fit: 8/10 (closes the loop on the metrics-index investment)
- Technical leverage: 7/10
- Effort: small-medium
- **Score: 7.0**
