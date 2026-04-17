# Design: /learn and /telemetry on DuckDB

## Context

`$ORCHESTRATOR_HOME/metrics.duckdb` contains one `features` row per
archived `state.yaml` across all registered repos. Two skills — `/learn`
and `/telemetry` — still read archive YAML directly. The producer
schema (single `features` table with `payload_json` blob) is locked; no
`step_history` or `cycles` table exists. Step-level data lives inside
`payload_json` and must be reached with DuckDB JSON unnest. `duckdb` may
be absent on fresh clones. `/telemetry`'s dashboard format is load-bearing
and callers depend on it.

## Goals / Non-Goals

### Goals

- A single named-query wrapper (`metrics-query.sh`) that both skills
  invoke without env plumbing.
- Skill prose stays short: one command per consumption point, with a
  clear fallback branch.
- Silent, zero-noise fallback when DuckDB is absent.
- Per-repo default for `/telemetry`; opt-in `--fleet` flag.
- Step-level retry analysis for `/learn` §2b via JSON unnest — no
  producer change.

### Non-Goals

- No changes to `register-repo.sh`, `features` schema, or
  `compute-swe-metrics.sh`.
- No new `step_history` or `cycles` table.
- No replacement of `.claude/metrics.jsonl`.
- No migration or backfill for existing DBs.
- No change to `/telemetry` dashboard output fields or ordering.

## Approaches Considered

### Approach A: JSON-unnest wrapper, per-repo default, silent fallback (SELECTED)

One new script `config/scripts/metrics-query.sh` resolves env itself,
ships a fixed named-query set, uses `json_each(json_extract(payload_json,
'$.step_history'))` for step data. `/telemetry` defaults per-repo with
`--fleet` opt-in. Fallback is silent.

- Pros: smallest surface, no producer change, no behavior change for
  single-repo users.
- Cons: step-level SQL is verbose — contained inside the helper.

### Approach B: Same as A but fleet-wide default + stderr fallback notice

- Pros: matches idea.md phrasing literally.
- Cons: silently changes observed output for every single-repo user;
  stderr noise on every fresh-clone invocation until `register-repo.sh`
  runs.

### Approach C: New `step_history` and `cycles` tables

- Pros: cleanest long-term SQL.
- Cons: violates "register-repo.sh out of scope"; needs DB migration;
  expands producer surface beyond this ticket.

### Selected Approach

**Approach A.** Lowest complexity tier, no schema/producer change,
smallest behavioral blast radius. The divergence from idea.md on the
`/telemetry` default (per-repo, not fleet) is an intentional trade to
avoid silent output changes for existing users; `--fleet` is a one-flag
opt-in documented in SKILL.md.

## High-Level Design

### Architecture Overview

```
[/learn SKILL.md]         [/telemetry SKILL.md]
     |                            |
     +------ metrics-query.sh ----+
                     |
                     | duckdb -csv (read-only)
                     v
        $ORCHESTRATOR_HOME/metrics.duckdb
                     |
                     | (absent or empty)
                     v
         fallback → spec/changes/archive/*/state.yaml glob
                    + $WORKFLOW_STATE_DIR/*/state.yaml (for /telemetry)
```

`metrics-query.sh` is the single integration point. Skills never touch
env or DB paths; they call a named query and inspect exit status and
stdout. Anything other than exit 0 with non-empty stdout → fallback.

### Key Abstractions

- **Named query**: a stable string ID (`cost-trend`, `retry-hotspots`,
  `cycle-count`, `quality-trend`, `recent-features`) mapped inside the
  script to one DuckDB SQL statement. Skills reference IDs, not SQL.
- **Scope flags**: `--repo <path>` (explicit), `--fleet` (unfiltered),
  absent (default → `$PWD`).
- **Silent empty result**: helper exits non-zero with empty stdout on
  any unhappy path (missing binary, missing DB, zero rows).

## Low-Level Design

### Components

#### `config/scripts/metrics-query.sh`

- **Responsibility**: resolve env, validate DuckDB + DB presence,
  dispatch on query-id, run SQL via `duckdb -csv`, stream csv to stdout
  (or exit non-zero on empty/missing).
- **Inputs**: positional `<query-id>`; optional `--repo <path>`,
  `--fleet`, `--limit <N>`.
- **Env contract**:
  - `ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"`
  - `METRICS_DB="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"`
  - Callers export nothing. Tests export `METRICS_DB` to point at a
    fixture.
- **Outputs**: CSV on stdout (header + data rows) on success; empty
  stdout + non-zero exit on every unhappy path.
- **Dependencies**: `duckdb` binary on PATH. Same presence guard pattern
  as `register-repo.sh` line 62.

#### `config/scripts/metrics-query.test.sh`

- **Responsibility**: seed a fixture DB at `$TMPDIR/test.duckdb` with
  rows from two repos (one with populated `step_history`, one with
  missing `metrics.*`), invoke every named query with every scope flag,
  and delete the DB to exercise the missing-DB path.
- **Pattern**: mirrors `config/scripts/compute-swe-metrics.test.sh`.

#### `~/.claude/skills/learn/SKILL.md` edits

At each of the five consumption points, prepend a one-line helper call
and keep the current glob logic as the `else` branch of an
exit-status check.

| Site | Named query |
|---|---|
| §2b cross-feature retry (line 54) | `retry-hotspots --fleet --limit 10` |
| §5b rule effectiveness (line 255–256) | `recent-features --limit 10` (reads step_history rollup) |
| §5b-decay trigger (line 274) | `cycle-count` |
| §5c adaptive quality bar (line 314) | `quality-trend --limit 5` |
| Rule-metadata cycle count (line 230) | `cycle-count` |

#### `~/.claude/skills/telemetry/SKILL.md` edits

- Data-gather block: call `metrics-query.sh recent-features --limit 5`
  for `recent` mode, or `metrics-query.sh recent-features` (no `--limit`)
  for `all` mode. Merge `$WORKFLOW_STATE_DIR/*/state.yaml` for active
  in-progress features, and preserve dashboard formatter.
- Trend analysis: call `metrics-query.sh cost-trend` and
  `quality-trend` instead of slicing in-memory YAML.
- Invocation section: document `--fleet` flag and per-repo default.

### Named Queries — Initial Set

| ID | Purpose | SQL sketch |
|---|---|---|
| `cost-trend` | per-feature cost over time | `SELECT change_id, completed_at, json_extract(payload_json, '$.metrics.cost_usd') AS cost FROM features WHERE <scope> ORDER BY completed_at DESC LIMIT :limit` |
| `quality-trend` | review scores over time | `SELECT change_id, completed_at, json_extract(payload_json, '$.metrics.quality_score') FROM features WHERE <scope> ORDER BY completed_at DESC LIMIT :limit` |
| `retry-hotspots` | step-level retries | `SELECT json_extract(s.value, '$.step_id') AS step_id, r.value AS reason, COUNT(DISTINCT f.change_id) AS feature_count, SUM(CAST(json_extract(s.value, '$.retries') AS INTEGER)) AS total_retries FROM features f, json_each(json_extract(f.payload_json, '$.step_history')) s, json_each(json_extract(s.value, '$.retry_reasons')) r WHERE <scope> GROUP BY step_id, reason ORDER BY total_retries DESC LIMIT :limit` (note: `step_history[].retries` is the int count and `step_history[].retry_reasons` is a JSON array — hence the second `json_each` unnest; exact field names must be validated against a live `payload_json` row during T-1) |
| `cycle-count` | archived feature count | `SELECT COUNT(*) FROM features WHERE <scope>` |
| `recent-features` | row dump for dashboard | `SELECT change_id, status, completed_at, payload_json FROM features WHERE <scope> ORDER BY completed_at DESC LIMIT :limit` |

`<scope>` resolves to `repo_root = ?` when per-repo, `1=1` when
`--fleet`. `LIMIT :limit` is appended only when `--limit <N>` is
provided; omitting `--limit` emits the query with no `LIMIT` clause
(this is how `/telemetry` `all` mode is served by `recent-features`).

### Data Flow

1. Skill invokes `metrics-query.sh <id> [flags]`.
2. Script resolves `METRICS_DB`. If `duckdb` absent or DB file absent →
   exit 1 empty.
3. Script builds SQL from named-query map, substituting scope and limit.
4. Script runs `duckdb -csv "$METRICS_DB" "<sql>"`.
5. If output is header-only (zero data rows) → exit 1 empty.
6. Else stream CSV to stdout, exit 0.
7. Skill checks `$?` and stdout-nonempty. If either fails → glob fallback.

### State Management

No new state. The DB is managed by `register-repo.sh` (producer). The
script is pure read. `metrics.jsonl` for cycles is unchanged.

### Error Handling

| Failure | Helper behavior | Skill behavior |
|---|---|---|
| `duckdb` not on PATH | exit 1, empty stdout | fall back to glob |
| `metrics.duckdb` missing | exit 1, empty stdout | fall back to glob |
| Query returns zero data rows | exit 1, empty stdout | fall back OR empty-state render |
| SQL error | exit non-zero, empty stdout (stderr silenced to `/dev/null`) | fall back |
| Unknown query-id | exit 2, empty stdout | developer bug — fall back too |

Silencing stderr is intentional: fresh-clone invocations must not spam
the user. The script's unit tests cover each failure path explicitly.

## Constraints

- `register-repo.sh` out of scope. No schema change. No new tables.
- `/telemetry` dashboard format unchanged.
- Skills are LLM-executed prose; helper calls must be a single shell
  line that's trivially expressible in prose.
- Fresh clones with no `metrics.duckdb` must behave exactly as today.

## Trade-offs

- Verbose `retry-hotspots` SQL (nested `json_extract` + `json_each`) vs.
  a cleaner normalized table — kept verbose to preserve producer scope.
  Complexity is contained inside the helper; skills see a flat CSV.
- Per-repo default vs. idea.md's "cross-repo default" — chose per-repo
  to avoid silent behavior change; `--fleet` is a one-flag opt-in.
- Silent fallback vs. verbose notice — chose silent; the fresh-clone
  case would otherwise spam every invocation.

## Decisions

- **Env resolution lives in the script** → skills stay prose-simple →
  adding a new consumption point is a one-line edit.
- **Named-query map inside the script** → SQL is testable without
  poking at skill prose → fewer moving parts per change.
- **JSON unnest for step data** → no producer change needed → ticket
  scope held.
- **Per-repo default** → preserves single-repo user experience → fleet
  is opt-in via `--fleet`.
- **Silent fallback** → no stderr noise on fresh installs → helper exit
  status is the sole fallback signal.
- **Test file parallels `compute-swe-metrics.test.sh`** → same fixture
  pattern the repo already knows.

## Open Questions

- None blocking. Dashboard field mapping to `recent-features` columns
  will be resolved during implementation by reading the current
  `/telemetry` dashboard section and matching column names; if a gap
  is found, escalate to architect via consultation.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
