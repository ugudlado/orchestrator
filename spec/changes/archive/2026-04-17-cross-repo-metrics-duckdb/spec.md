---
feature-id: cross-repo-metrics-duckdb
linear-ticket: none
---

# Specification: cross-repo-metrics-duckdb

## Motivation

The orchestrator accumulates per-feature metrics in `spec/changes/archive/*/state.yaml` files
spread across individual repos. There is no cross-repo aggregate index, so trend analysis,
benchmark comparisons, and learning-loop queries require ad-hoc shell scripting against each
repo's archive separately. Establishing a single trigger point at bootstrap that registers the
repo in a central YAML registry and ingests all archived state.yaml files into a DuckDB database
at `$ORCHESTRATOR_HOME/metrics.duckdb` enables SQL-based queries across all registered repos
without any per-step instrumentation.

## What Changes

- New script `config/scripts/register-repo.sh` performs registry append + archive walk + DuckDB
  upsert ingest. Idempotent; non-blocking on tool/parse errors.
- New inline step contract `config/steps/register-with-orchestrator-home.yaml` invokes the script
  during bootstrap.
- `config/workflows/bootstrap.yaml` gains a `metrics: true` default, a `--no-metrics` flag, and
  the new step wired between `write-bootstrap-state` and `verify-report`.
- `.gitignore` adds `*.duckdb` so the database is never committed.
- `spec/project.yaml` tech_stack adds `duckdb` and `yq`; new `metrics-db-derived` learning is
  recorded.

## Requirements

### Functional

1. **FR-1**: `register-repo.sh` MUST append `$REPO_ROOT` to `$ORCHESTRATOR_HOME/metrics-registry.yaml`
   exactly once across re-runs (idempotent; flat YAML sequence of literal absolute paths).
2. **FR-2**: `register-repo.sh` MUST walk `$REPO_ROOT/spec/changes/archive/*/state.yaml`, convert
   each via `yq -o json`, and upsert into a `features` table in `$ORCHESTRATOR_HOME/metrics.duckdb`,
   keyed on `(repo_root, change_id)` via `INSERT OR REPLACE`.
3. **FR-3**: The `features` table MUST be created with explicit typed columns (`repo_root`,
   `change_id`, `schema`, `status`, `started_at`, `completed_at`, `payload_json`, `ingested_at`)
   and a PRIMARY KEY on `(repo_root, change_id)` — no `read_json_auto` inference drift.
4. **FR-4**: The script MUST ingest ALL state.yaml schemas (bootstrap, spike, autopilot, feature,
   bugfix, chore) into the same `features` table; consumers filter by `schema` column.
5. **FR-5**: The script MUST exit 0 if `yq` or `duckdb` is not installed (preflight skip),
   logging a `skip:` message; bootstrap MUST continue to `verify-report`.
6. **FR-6**: A malformed state.yaml MUST log a warning and be skipped without aborting the loop;
   remaining files continue ingesting.
7. **FR-7**: Empty archive (no state.yaml files) MUST complete the script with `ingested=0` and
   exit 0.
8. **FR-8**: The script MUST support a `--rebuild` flag that deletes existing rows for the
   current `repo_root` before re-ingesting, supporting full refresh of one repo's data without
   touching other repos.
9. **FR-9**: The script MUST support `--dry-run` printing the planned actions (registry path,
   DB path, archive count) without touching disk.
10. **FR-10**: `bootstrap.yaml` MUST default `metrics: true` and accept `--no-metrics` to filter
    the `register-with-orchestrator-home` step from the plan.
11. **FR-11**: The new step contract MUST be inline (no `agent:` field) with `id`, `intent`,
    `inputs`, `instruction`, `verify`, `outputs` sections.
12. **FR-12**: `.gitignore` MUST include `*.duckdb` so `metrics.duckdb` is never committed.
13. **FR-13**: `spec/project.yaml` MUST add `duckdb` and `yq` to `context.tech_stack` and add a
    `metrics-db-derived` learning entry.
14. **FR-14**: `register-repo.sh` MUST escape single-quotes in all SQL-interpolated values via a
    `sql_quote` helper (applied to `repo_root`, `change_id`, `schema`, `status`, `started_at`,
    `completed_at`, and `payload_json`) AND skip ingest of any state.yaml whose `change_id` does
    not match `^[a-z0-9._-]+$`, logging a `skip:` line and continuing the walk.

### Non-Functional

1. **NFR-1**: Script execution against the current orchestrator repo (10 archived state.yaml
   files) MUST complete in under 10 seconds on first run.
2. **NFR-2**: Re-runs MUST be idempotent — registry file byte-identical, DB row counts unchanged
   (modulo `ingested_at` timestamp).
3. **NFR-3**: Metrics-step failure MUST be non-blocking: bootstrap proceeds even if the script
   exits non-zero (script itself returns 0 on tool-missing; step contract treats other failures
   as warnings).
4. **NFR-4**: `metrics.duckdb` MUST never appear in `git status` after a bootstrap run.

## Architecture

| File | Change |
|------|--------|
| `.gitignore` | Add `*.duckdb` |
| `config/scripts/register-repo.sh` | New, ~80 LOC bash, set -uo pipefail |
| `config/steps/register-with-orchestrator-home.yaml` | New inline step contract |
| `config/workflows/bootstrap.yaml` | Add `metrics: true` default, `--no-metrics` flag, step entry between `write-bootstrap-state` and `verify-report` |
| `spec/project.yaml` | Add duckdb+yq to `context.tech_stack`; add `metrics-db-derived` learning |
| `$ORCHESTRATOR_HOME/metrics-registry.yaml` | Created/appended at runtime |
| `$ORCHESTRATOR_HOME/metrics.duckdb` | Created/upserted at runtime |

Data flow: bootstrap workflow → `register-with-orchestrator-home` step → `register-repo.sh`
→ (1) preflight tools, (2) idempotent registry append via `grep -Fxq || echo >>`, (3)
`CREATE TABLE IF NOT EXISTS features`, (4) for each archive state.yaml: extract typed columns
via `yq`, dump full payload via `yq -o json`, `INSERT OR REPLACE`, (5) report counts.

## Test Strategy

### Test File Paths

N/A — bash + YAML infra work, verified via end-to-end script runs (see Acceptance Criteria) and
`bash -n` syntax checks. No unit-test framework introduced.

### Coverage Targets

N/A — verified by acceptance-criteria runs covering happy path, idempotency, rebuild, empty
archive, missing-tool, and malformed-yaml branches.

### Key Test Scenarios

- First-run ingest (UC-1): row count >= 5 for orchestrator repo.
- Re-run idempotency (UC-2): byte-identical registry, identical row count.
- Empty archive (UC-3): exit 0, ingested=0.
- Missing tool (UC-E1): preflight skip, exit 0, no DB writes.
- Malformed YAML (UC-E2): warn + skip, other files ingested.
- `--no-metrics` flag (UC-E3): step filtered from plan.

## Acceptance Criteria

- AC-1: Given an orchestrator repo with 10 archived state.yaml files, when `register-repo.sh`
  runs first-time with `ORCHESTRATOR_HOME=/Users/spidey/code/orchestrator`, then
  `metrics-registry.yaml` contains the repo path and `SELECT count(*) FROM features WHERE
  repo_root = '/Users/spidey/code/orchestrator'` returns >= 5. [traces: UC-1]
- AC-2: Given a repo already registered, when `register-repo.sh` runs again, then
  `metrics-registry.yaml` is byte-identical to the prior run and the per-repo row count is
  unchanged. [traces: UC-2]
- AC-3: Given a brand-new repo with no `spec/changes/archive/` directory, when
  `register-repo.sh` runs, then it exits 0 with `ingested=0` and the registry contains the
  repo path. [traces: UC-3]
- AC-4: Given `yq` or `duckdb` is not on PATH, when `register-repo.sh` runs, then it logs
  `skip:` and exits 0; bootstrap proceeds to `verify-report`. [traces: UC-E1]
- AC-5: Given an archive containing one malformed state.yaml, when `register-repo.sh` runs,
  then it logs a warning for that file and ingests the remaining valid files. [traces: UC-E2]
- AC-6: Given `bootstrap --no-metrics`, when the workflow plan is built, then
  `register-with-orchestrator-home` is filtered out and no DuckDB writes occur. [traces: UC-E3]
- AC-7: Given the script is invoked with `--rebuild`, when it runs, then existing rows for the
  current `repo_root` are deleted before re-ingest and the resulting row count equals the prior
  first-run count. [traces: UC-2]
- AC-8: Given any bootstrap run, when `git status` is checked afterward, then `metrics.duckdb`
  does NOT appear (covered by `*.duckdb` in `.gitignore`). [traces: UC-1]
- AC-9: Given an archive containing a state.yaml whose `change_id` contains a single-quote or
  any character outside `^[a-z0-9._-]+$`, when `register-repo.sh` runs, then it logs a `skip:`
  line for that file, does NOT attempt the INSERT, and continues ingesting the remaining valid
  files in the walk. [traces: UC-E2]

## Alternatives Considered

**Alternative 1: Per-schema CREATE TABLE with dispatcher (Approach 2 in design.md)**
Rejected. Adds ~150 LOC dispatcher branching, contradicts the brief constraint of
"feature-level only" by introducing per-schema tables, and requires fragile `yq -i`
init-or-update guard logic for the registry. Higher upfront cost with no v1 payoff.

**Alternative 2: Hybrid typed core + JSON payload via per-column yq extraction (Approach 3)**
Rejected (close runner-up). Same complexity score as Approach 1 but loses the tiebreak —
introduces a new per-column yq extraction loop instead of reusing the `estimate-cost.sh`
archive-walk pattern verbatim. Approach 1 borrows the typed-columns + `payload_json` schema
idea anyway, capturing this approach's key benefit without its extraction overhead.

## Impact

No breaking changes. Adds a new bootstrap step gated by `metrics: true` (default on). Existing
bootstraps gain registry+DB side-effects; both written under `$ORCHESTRATOR_HOME` only. Failure
of the new step is non-blocking. No existing scripts modified.

## Decisions

- Single `features` table with explicit typed columns + `payload_json VARCHAR`: stable schema,
  preserves full state.yaml fidelity for ad-hoc queries, avoids `read_json_auto` type drift.
- `INSERT OR REPLACE` keyed on `(repo_root, change_id)`: idempotent re-runs without DELETE
  pre-pass; multi-repo isolation preserved.
- Flat YAML registry (`- /abs/path` per line) appended via `grep -Fxq || echo >>`: sidesteps
  `yq -i` v4 quirks; format is canonical because only this script writes it.
- Ingest all schemas into one table: future schema additions just work; consumers filter by
  `schema` column.
- Non-blocking on tool absence: preserves bootstrap success on minimal environments.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
