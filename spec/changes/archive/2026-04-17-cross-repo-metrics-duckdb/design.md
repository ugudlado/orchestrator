# Design — cross-repo-metrics-duckdb

## Selected Approach

**Approach 1: Minimal/Simple (single wide table, drop-and-recreate per repo, grep+append registry)** — complexity S. A single `features` table receives all schemas via `read_json_auto`, with a stable PK on `(repo_root, change_id)` enforced via `INSERT OR REPLACE`. Registry append uses a `grep -Fxq` precondition + `echo >>`, treating the registry as a flat YAML list of strings. Selected per the auto-approve rule: lowest complexity (S=2), and reuses more existing patterns (estimate-cost.sh archive walk, autopilot-session-rollup.sh yq usage, bootstrap.yaml `--no-portless` flag template) than Approach 2.

## Approach 1: Minimal-Simple

### Description
Use `INSERT OR REPLACE` against a single wide `features` table keyed on `(repo_root, change_id)` — DuckDB infers nested STRUCT columns from `read_json_auto` and replaces existing rows on re-ingest, no DELETE pass required. Ingest **all** schemas (bootstrap, spike, autopilot, feature, bugfix, chore) into the same table; columns absent in some schemas land as NULL via DuckDB's permissive struct widening. Registry append treats `metrics-registry.yaml` as a YAML sequence of literal repo-root strings (`- /abs/path`) — `register-repo.sh` does `grep -Fxq -- "- $REPO_ROOT" metrics-registry.yaml || echo "- $REPO_ROOT" >> metrics-registry.yaml`, sidestepping `yq -i` quirks entirely.

### Pros
- Smallest diff: one new step contract, one new script (~80 LOC), 4 lines added to bootstrap.yaml, 1 line to .gitignore.
- Reuses `estimate-cost.sh` archive-walk loop verbatim.
- `INSERT OR REPLACE` makes re-runs idempotent without per-row DELETE bookkeeping.
- No-filter ingest means future schemas (e.g., new `chore` variant) just work — no script edit needed.
- Registry append is a 1-liner with zero yq dependency for writes.

### Cons
- `read_json_auto` may infer different column types across runs if archive contents vary (e.g., `resolve_rate` as bigint vs double). Mitigated by an explicit CREATE TABLE with typed core columns + JSON catch-all (see schema below).
- Bootstrap-schema rows have many NULL columns (no metrics block). Acceptable: queries filter by `schema = 'feature'` when needed.
- Flat registry format (`- /path`) loses room for per-repo metadata (last-ingested timestamp, etc.) — out of scope for v1.

### Complexity: S

## Approach 2: Robust/General

### Description
Per-schema CREATE TABLE statements (`features`, `bootstrap_runs`, `spikes`) with explicit typed columns, plus a dispatcher in `register-repo.sh` that reads `schema:` from each state.yaml and routes to the right table. Upsert via `DELETE FROM <table> WHERE repo_root=? AND change_id=?` then `INSERT`. Registry uses `yq -i '.repos += ["…"] | .repos |= unique'` with init-if-missing fallback.

### Pros
- Strict typed schemas — no inference drift across runs.
- Per-schema tables match the natural shape of each state.yaml variant.
- yq-managed registry preserves YAML structure cleanly, supports future per-repo metadata.
- DELETE+INSERT is explicit and auditable.
- Easier to add schema-specific indices later.

### Cons
- ~3x more SQL DDL to maintain; every state.yaml shape change requires a schema update.
- Dispatcher adds branching logic to the script (~150+ LOC).
- `yq -i` on a missing key requires init-or-update guard logic — known v4 quirk, fragile.
- Per-table approach contradicts brief constraint "feature-level only, no per-step tables" in spirit (introduces three tables instead of one).
- Higher upfront design cost for v1; brief explicitly defers reader/dashboard work.

### Complexity: M

## Approach 3: Hybrid (typed core + JSON tail)

### Description
Single `features` table with **explicit typed columns** for the stable identifiers (`repo_root`, `change_id`, `schema`, `status`, `started_at`, `completed_at`) and a single `payload JSON` column holding the full state.yaml content. Ingest is `INSERT OR REPLACE` with the typed columns extracted via `yq` and `payload` populated from `yq -o json` of the whole file. Registry append same as Approach 1.

### Pros
- Stable schema for the columns that matter for cross-repo queries.
- `payload JSON` preserves full fidelity for ad-hoc deep queries via DuckDB's JSON functions.
- Avoids `read_json_auto` inference drift entirely.
- Same registry simplicity as Approach 1.

### Cons
- Requires a small per-file extraction pass with `yq` for each typed column (5–6 yq calls per file).
- Loses the convenience of nested STRUCT auto-typing for the `metrics` block (must use `payload->'metrics'->>'tokens_total'` syntax).
- More SQL boilerplate than Approach 1, less than Approach 2.

### Complexity: S (borderline M)

## Selection Rationale

Auto-approve rule:
1. Complexity scores: A1=2 (S), A2=3 (M), A3=2 (S). Tie between A1 and A3.
2. Tiebreak — pattern reuse: A1 reuses the `estimate-cost.sh` archive-walk loop **as-is** (single `yq -o json | duckdb` per file). A3 introduces a new pattern (per-column yq extraction loop). A1 wins.
3. Alphabetical not needed.

**Selected: Approach 1.**

To mitigate the main con (type-inference drift), Approach 1 uses an **explicit CREATE TABLE** with typed columns for the well-known identifiers and a `metrics_json VARCHAR` for the variable-shape metrics block — borrowing the best idea from Approach 3 without its per-column extraction overhead. See schema below.

## Resolved Open Questions

1. **Upsert strategy**: `INSERT OR REPLACE` with PRIMARY KEY on `(repo_root, change_id)`. Re-ingest of a single repo replaces only that repo's rows; other repos untouched. No DELETE pre-pass needed.
2. **Schema filtering**: Ingest **all** schemas into one wide `features` table (column name retained for simplicity even though it spans bootstrap/spike/etc.). Consumers filter by `schema` column. Avoids dispatcher complexity and future schema additions just work.
3. **Registry append**: `grep -Fxq -- "- $REPO_ROOT" "$REGISTRY" || echo "- $REPO_ROOT" >> "$REGISTRY"` after ensuring the file exists with a leading comment. Treats registry as a flat YAML sequence of literal strings — no `yq -i` quirks, atomic enough for the single-bootstrap-at-a-time use case.

## Concrete Mechanism (Approach 1)

### `register-repo.sh` skeleton

```bash
#!/usr/bin/env bash
# register-repo.sh — Append repo to metrics-registry.yaml and ingest archive
# state.yaml files into metrics.duckdb. Non-blocking on tool/parse errors.
set -uo pipefail

# --- Block 1: preflight ---
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:?ORCHESTRATOR_HOME must be set}"
REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel)}"
REGISTRY="$ORCHESTRATOR_HOME/metrics-registry.yaml"
DB="$ORCHESTRATOR_HOME/metrics.duckdb"

command -v yq >/dev/null      || { echo "skip: yq not installed";    exit 0; }
command -v duckdb >/dev/null  || { echo "skip: duckdb not installed"; exit 0; }

# --- Block 2: register repo (idempotent) ---
[[ -f "$REGISTRY" ]] || printf '# Cross-repo metrics registry\nrepos:\n' > "$REGISTRY"
if grep -Fxq -- "  - $REPO_ROOT" "$REGISTRY"; then
  echo "registry: already registered $REPO_ROOT"
else
  printf '  - %s\n' "$REPO_ROOT" >> "$REGISTRY"
  echo "registry: appended $REPO_ROOT"
fi

# --- Block 3: ensure schema ---
duckdb "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS features (
  repo_root      VARCHAR NOT NULL,
  change_id      VARCHAR NOT NULL,
  schema         VARCHAR,
  status         VARCHAR,
  started_at     VARCHAR,
  completed_at   VARCHAR,
  payload_json   VARCHAR,
  ingested_at    TIMESTAMP DEFAULT current_timestamp,
  PRIMARY KEY (repo_root, change_id)
);
SQL

# --- Block 4: walk archive + ingest ---
# sql_quote: double single-quotes in any value before SQL string-literal interpolation.
sql_quote() { printf "%s" "${1//\'/\'\'}"; }

ARCHIVE_GLOB="$REPO_ROOT/spec/changes/archive/*/state.yaml"
ingested=0; skipped=0; failed=0
for state_file in $ARCHIVE_GLOB; do
  [[ -f "$state_file" ]] || continue
  json=$(yq -o json '.' "$state_file" 2>/dev/null) || { failed=$((failed+1)); echo "warn: parse failed $state_file"; continue; }
  change_id=$(yq -r '.change_id // ""' "$state_file")
  schema=$(yq    -r '.schema    // ""' "$state_file")
  status=$(yq    -r '.status    // ""' "$state_file")
  started=$(yq   -r '.started_at // ""' "$state_file")
  completed=$(yq -r '.completed_at // ""' "$state_file")
  [[ -n "$change_id" ]] || { skipped=$((skipped+1)); continue; }

  # Slug guard: refuse anything outside the documented change_id alphabet.
  # Defense-in-depth alongside sql_quote — a corrupted change_id never reaches SQL.
  [[ "$change_id" =~ ^[a-z0-9._-]+$ ]] || {
    echo "skip: change_id has unsafe chars: $change_id" >&2
    skipped=$((skipped+1)); continue;
  }

  # Escape ALL interpolated values — repo paths, status strings, timestamps, payload.
  q_repo=$(sql_quote "$REPO_ROOT")
  q_change=$(sql_quote "$change_id")
  q_schema=$(sql_quote "$schema")
  q_status=$(sql_quote "$status")
  q_started=$(sql_quote "$started")
  q_completed=$(sql_quote "$completed")
  q_payload=$(sql_quote "$json")

  duckdb "$DB" <<SQL || { failed=$((failed+1)); continue; }
INSERT OR REPLACE INTO features (repo_root, change_id, schema, status, started_at, completed_at, payload_json)
VALUES ('$q_repo', '$q_change', '$q_schema', '$q_status', '$q_started', '$q_completed', '$q_payload');
SQL
  ingested=$((ingested+1))
done

# --- Block 5: report ---
echo "metrics: ingested=$ingested skipped=$skipped failed=$failed db=$DB"
exit 0
```

### DuckDB schema

```sql
CREATE TABLE IF NOT EXISTS features (
  repo_root      VARCHAR NOT NULL,   -- absolute repo path; PK component
  change_id      VARCHAR NOT NULL,   -- e.g. cross-repo-metrics-duckdb; PK component
  schema         VARCHAR,            -- bootstrap | spike | autopilot | feature | bugfix | chore
  status         VARCHAR,            -- complete | failed | in_progress | etc.
  started_at     VARCHAR,            -- ISO 8601, kept as VARCHAR for cross-tz ease
  completed_at   VARCHAR,            -- ISO 8601 or NULL
  payload_json   VARCHAR,            -- full state.yaml as JSON string; query via json_extract()
  ingested_at    TIMESTAMP DEFAULT current_timestamp,
  PRIMARY KEY (repo_root, change_id)
);
```

Rationale for VARCHAR-typed timestamps and JSON: avoids `read_json_auto` type drift, keeps schema stable across DuckDB versions, and pushes nested-field access to query time (`json_extract(payload_json, '$.metrics.tokens_total')`). Reader helpers (out of scope) will encapsulate this.

### Bootstrap wiring diff

`config/workflows/bootstrap.yaml`:

```yaml
defaults:
  auto_approve_phases: true
  linear: false
  portless: true
  metrics: true                    # +

flags:
  --no-portless:
    sets: { portless: false }
  --no-metrics:                    # +
    sets: { metrics: false }       # +
  --linear:
    sets: { linear: true }
```

In the `setup.steps` list, between `write-bootstrap-state` and `verify-report`:

```yaml
      - write-bootstrap-state
      - register-with-orchestrator-home if metrics    # +
      - verify-report
```

`.gitignore` (project root):
```
*.duckdb
```

## Risks & Mitigations

- **Worktree gotcha**: `ORCHESTRATOR_HOME` may resolve to a worktree path on test runs, polluting the canonical metrics.duckdb with worktree-specific repo_root entries. *Mitigation*: script reads `ORCHESTRATOR_HOME` from env without falling back to `git rev-parse`, so test runs must `export ORCHESTRATOR_HOME=/tmp/test-home` to isolate. Document in step contract.
- **read_json_auto type drift** (avoided): explicit CREATE TABLE with VARCHAR/JSON columns sidesteps it entirely.
- **Concurrent bootstrap on same machine**: two repos bootstrapping simultaneously could race on `metrics.duckdb`. DuckDB CLI takes an exclusive lock per process — second writer fails fast. *Mitigation*: non-blocking exit on duckdb failure means bootstrap continues; user re-runs metrics later (or v2 adds retry).
- **Registry corruption**: `grep -Fxq` is exact-line match — extra whitespace breaks idempotency. *Mitigation*: only this script writes to the registry, so format stays canonical (`  - <path>\n`).
- **Single-quote in any SQL-interpolated value** (`repo_root`, `change_id`, `schema`, `status`, timestamps, payload): shell substitution into the SQL heredoc would corrupt the INSERT or alter query semantics (e.g., a repo path like `/home/user's-repo/`). *Mitigation (defense in depth)*: (a) a `sql_quote` helper doubles single-quotes for **every** interpolated value before the heredoc, not just `payload_json`; (b) a slug guard `[[ "$change_id" =~ ^[a-z0-9._-]+$ ]]` rejects any state.yaml whose `change_id` contains unsafe characters with a `skip:` log line — the loop continues. The slug constraint is now enforced at the script boundary, not just documented.
- **First-run table creation race**: `CREATE TABLE IF NOT EXISTS` is safe across re-runs; PK constraint enforces upsert semantics.
- **Bootstrap state.yaml has no `metrics` block**: by design — those rows have NULL `payload_json.metrics`, and queries filter on `schema = 'feature'` to exclude.
