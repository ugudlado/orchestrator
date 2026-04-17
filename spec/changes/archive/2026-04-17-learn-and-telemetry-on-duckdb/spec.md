---
feature-id: learn-and-telemetry-on-duckdb
linear-ticket: HL-282
---

# Specification: /learn and /telemetry on DuckDB

## Motivation

The `cross-repo-metrics-duckdb` feature ships a populated
`$ORCHESTRATOR_HOME/metrics.duckdb` that ingests every archived
`state.yaml` across every registered repo. Two skill consumers —
`/learn` and `/telemetry` — still glob `spec/changes/archive/*/state.yaml`
and parse YAML inline, so they remain single-repo and re-parse files on
every invocation. This ticket closes the producer-consumer gap: route
both skills through a named-query helper that reads the DuckDB index,
while preserving correct behavior on installs that have not yet run
`register-repo.sh`.

## What Changes

- A new script `config/scripts/metrics-query.sh` exposing a fixed set of
  named queries over `metrics.duckdb` via `duckdb -csv`.
- `/learn` SKILL.md replaces its five YAML-glob sites with named-query
  calls plus fallback-on-empty.
- `/telemetry` SKILL.md replaces its data-gather block with a named-query
  call merged with `$WORKFLOW_STATE_DIR/*/state.yaml` for active features.
- The existing YAML-glob logic in both skills stays as the fallback path
  (DuckDB missing, binary missing, or zero rows returned).
- A test file `config/scripts/metrics-query.test.sh` paralleling
  `compute-swe-metrics.test.sh` covers the helper end to end against a
  fixture DB.

## Requirements

### Functional

1. **FR-1**: `config/scripts/metrics-query.sh <query-id> [--repo <path>] [--fleet] [--limit <N>]`
   exists and is executable. Query IDs supported initially: `cost-trend`,
   `retry-hotspots`, `cycle-count`, `quality-trend`, `recent-features`.
2. **FR-2**: The helper resolves its DB path from env with defaults:
   `ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"`,
   `METRICS_DB="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"`.
   Callers pass no env plumbing; tests override `METRICS_DB`.
3. **FR-3**: Default scope is per-repo — the helper applies
   `WHERE repo_root = <resolved-repo>` where the resolved repo is
   `--repo <path>` if given, else `$PWD`. `--fleet` (or `--repo all`)
   suppresses the filter for a cross-repo view.
4. **FR-4**: Helper exits non-zero with empty stdout when `duckdb` is
   not on PATH, when `metrics.duckdb` does not exist, or when the query
   returns no data rows. Errors are silent (no stderr noise).
5. **FR-5**: `/learn` SKILL.md §2b (cross-feature retry), §5b (rule
   effectiveness), §5b-decay trigger, §5c (adaptive quality bar), and
   the rule-metadata cycle count call `metrics-query.sh` first and fall
   back to the existing `spec/changes/archive/*/state.yaml` glob when
   the helper is unavailable or returns empty.
6. **FR-6**: `/telemetry` SKILL.md data-gather section calls the helper
   for archived metrics and merges `$WORKFLOW_STATE_DIR/*/state.yaml`
   for active features. Specifically: `recent` mode →
   `metrics-query.sh recent-features --limit 5`; `all` mode →
   `metrics-query.sh recent-features` (no `--limit`, returns every
   row in scope); trend modes → `metrics-query.sh cost-trend` and
   `metrics-query.sh quality-trend`. The dashboard output format
   (SKILL.md lines 80–128) is unchanged.
7. **FR-7**: `/telemetry` defaults to per-repo. A documented `--fleet`
   invocation opts into cross-repo view. This intentionally diverges
   from the idea.md phrasing ("cross-repo by default") — see Decisions.
8. **FR-8**: `config/scripts/metrics-query.test.sh` seeds a fixture DB
   at `$TMPDIR/test.duckdb` with at least 2 repos and at least one row
   carrying a populated `step_history`, and asserts per-repo, `--fleet`,
   missing-DB, and empty-result paths.

### Non-Functional

1. **NFR-1 (compatibility)**: No change to `register-repo.sh`, the
   `features` table schema, or `compute-swe-metrics.sh`. No new tables.
2. **NFR-2 (fallback correctness)**: On a fresh clone where
   `register-repo.sh` has never run, `/learn` and `/telemetry` produce
   identical behavior to pre-migration — no visible failure, no stderr
   noise.
3. **NFR-3 (output stability)**: `/telemetry` dashboard fields and
   ordering are byte-stable wherever the DB path now feeds data that
   the glob path used to feed.
4. **NFR-4 (perf)**: A single helper invocation is a `duckdb -csv`
   shell-out with a bounded-row query (`--limit` defaults to 10).

## Architecture

See `design.md` for the component + data-flow view. Files touched:

| File | Change |
|---|---|
| `config/scripts/metrics-query.sh` | NEW — named-query wrapper |
| `config/scripts/metrics-query.test.sh` | NEW — fixture-DB tests |
| `~/.claude/skills/learn/SKILL.md` | EDIT — 5 consumption points |
| `~/.claude/skills/telemetry/SKILL.md` | EDIT — data-gather + --fleet docs |
| `config/scripts/register-repo.sh` | NO CHANGE |
| `config/scripts/compute-swe-metrics.sh` | NO CHANGE |

## Test Strategy

### Test File Paths

- `config/scripts/metrics-query.sh` → `config/scripts/metrics-query.test.sh`
- Skill prose (SKILL.md edits) has no unit test; verified by manual
  invocation + the fallback path is exercised by deleting the fixture DB
  in `metrics-query.test.sh`.

### Coverage Targets

- `metrics-query.test.sh` covers: each named query returns rows on the
  fixture; `--repo` filters; `--fleet` aggregates; missing binary →
  exit non-zero, empty stdout; missing DB → exit non-zero, empty stdout;
  empty result → exit non-zero, empty stdout.

### Key Test Scenarios

- Per-repo filter defaults to `$PWD` when `--repo`/`--fleet` are absent.
- `retry-hotspots` correctly unnests `step_history` via `json_each` and
  returns `(step_id, reason, feature_count, total_retries)`.
- Fresh clone fallback — remove `metrics.duckdb`, helper exits non-zero
  with empty stdout, skill glob path produces the expected legacy output.

## Acceptance Criteria

- **AC-1**: Given a populated `metrics.duckdb` with features from two
  repos, when `/telemetry` runs in repo A, then only repo-A rows appear
  in the dashboard. [traces: UC-1]
- **AC-2**: Given the same DB, when `/telemetry --fleet` runs in repo A,
  then rows from both repos appear. [traces: UC-1]
- **AC-3**: Given a populated DB with `step_history` in `payload_json`,
  when `/learn` §2b runs, then `metrics-query.sh retry-hotspots --fleet
  --limit 10` returns `(step_id, reason, feature_count, total_retries)`
  rows derived via `json_each(json_extract(payload_json, '$.step_history'))`.
  [traces: UC-2]
- **AC-4**: Given `metrics.duckdb` absent, when `/learn` or `/telemetry`
  invoke the helper, then the helper exits non-zero with empty stdout
  and the skill falls back to `spec/changes/archive/*/state.yaml` glob
  logic with no stderr output. [traces: UC-E1]
- **AC-5**: Given `metrics.duckdb` present but the current repo is not
  registered, when `/telemetry` runs, then the dashboard renders its
  empty-state (no crash, no error). [traces: UC-E2]
- **AC-6**: Given `METRICS_DB=$TMPDIR/test.duckdb` exported by tests,
  when `metrics-query.test.sh` runs, then all fixture-backed cases
  (per-repo, fleet, missing DB, empty result) pass. [traces: UC-1, UC-E1]
- **AC-7**: `/telemetry` SKILL.md documents the `--fleet` flag and
  per-repo default in its invocation section. [traces: UC-1]
- **AC-8**: `register-repo.sh`, the `features` schema, and
  `compute-swe-metrics.sh` are byte-identical to main after the change.
  [traces: NFR-1]

## Alternatives Considered

**Approach B — fleet-wide default + verbose fallback**
Rejected. Matches idea.md phrasing literally but silently changes
behavior for every existing single-repo user and emits stderr noise on
fresh clones before `register-repo.sh` runs. One-flag opt-in is a
better default.

**Approach C — new `step_history` + `cycles` tables**
Rejected. Violates the explicit "register-repo.sh out of scope"
constraint, requires migrating the live DB, and expands producer
surface beyond this ticket. Deferred to a follow-on.

## Impact

- **Consumers**: `/learn` and `/telemetry` gain cross-repo aggregation
  (via `--fleet` in `/telemetry`, always in `/learn` §2b) without a
  visible behavior change on fresh installs.
- **Producers**: None. `register-repo.sh` and `compute-swe-metrics.sh`
  are untouched.
- **Backward compatibility**: Pre-migration users without
  `metrics.duckdb` see identical output. Dashboard format unchanged.
- **Migration**: None required; the helper is additive.

## Decisions

- **Per-repo default for `/telemetry`** (diverges from idea.md) — a
  silent fleet-wide default changes observed output for every existing
  user. `--fleet` is a one-flag opt-in and is documented in SKILL.md.
- **Helper owns env resolution** — skills call `metrics-query.sh`
  with no env plumbing; the script defaults `ORCHESTRATOR_HOME` and
  `METRICS_DB`. Tests override by exporting `METRICS_DB`.
- **JSON unnest over new table** (KD-2 Option A) — respects the
  out-of-scope constraint on `register-repo.sh`. SQL verbosity is
  contained inside the helper.
- **Silent fallback** — non-zero exit + empty stdout → skill falls back
  without stderr. Avoids spam on fresh clones.
- **`cycles` table deferred** — `.claude/metrics.jsonl` stays the cycles
  store. Follow-on ticket.
- **N/A: new archive/state paths** — this feature consumes the existing
  `spec/changes/archive/*/state.yaml` path and
  `$WORKFLOW_STATE_DIR/*/state.yaml` path; no new consumer globs are
  introduced, so the archive-path grep check is not applicable.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
