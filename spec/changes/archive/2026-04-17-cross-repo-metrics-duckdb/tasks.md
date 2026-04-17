# Tasks — cross-repo-metrics-duckdb

- [x] T-1: Add `*.duckdb` to `.gitignore` at the orchestrator repo root
  Verify: `grep -Fxq '*.duckdb' .gitignore` exits 0; `git check-ignore $ORCHESTRATOR_HOME/metrics.duckdb` prints the path

- [x] T-2: Create `config/scripts/register-repo.sh` per design.md mechanism — preflight (`yq`, `duckdb`), idempotent registry append (`grep -Fxq || echo >>`), `CREATE TABLE IF NOT EXISTS features` with typed columns + PK on `(repo_root, change_id)`, archive walk with `INSERT OR REPLACE`, support `--dry-run` and `--rebuild` flags, non-blocking on tool/parse errors, `set -uo pipefail`, executable bit set; uses `sql_quote` helper for ALL SQL-interpolated values (repo_root, change_id, schema, status, started_at, completed_at, payload_json); enforces change_id regex `^[a-z0-9._-]+$` with skip-and-continue on mismatch
  Verify: `bash -n config/scripts/register-repo.sh` exits 0; `config/scripts/register-repo.sh --dry-run` prints planned registry path, DB path, and archive file count without creating any files; running with `PATH=/usr/bin` (no yq/duckdb) prints `skip:` and exits 0; create a temp state.yaml with `change_id: change_id-with-bad-char'` (or similar special char) under a fixture archive directory, run the script, confirm stderr contains `skip: change_id has unsafe chars`, the loop continues, and the final row count excludes the bad fixture
  depends: T-1

- [x] T-3: Run `register-repo.sh` end-to-end against the orchestrator repo with `ORCHESTRATOR_HOME=/Users/spidey/code/orchestrator` and `REPO_ROOT=/Users/spidey/code/orchestrator`, creating `metrics-registry.yaml` and `metrics.duckdb`
  Verify: `grep -Fxq -- '  - /Users/spidey/code/orchestrator' $ORCHESTRATOR_HOME/metrics-registry.yaml` exits 0; `duckdb $ORCHESTRATOR_HOME/metrics.duckdb "SELECT count(*) FROM features WHERE repo_root = '/Users/spidey/code/orchestrator'"` returns >= 5
  depends: T-2

- [x] T-4: Verify idempotency by re-running `register-repo.sh` against the same repo
  Verify: second-run `metrics-registry.yaml` is byte-identical to first-run snapshot (`diff /tmp/registry-first $ORCHESTRATOR_HOME/metrics-registry.yaml` exits 0); per-repo row count from the same `SELECT count(*)` query is unchanged
  depends: T-3

- [x] T-5: Verify `--rebuild` flag — capture pre-rebuild row count, run `register-repo.sh --rebuild`, confirm rows for this repo were deleted then re-ingested with the same final count
  Verify: post-rebuild `SELECT count(*) FROM features WHERE repo_root = '/Users/spidey/code/orchestrator'` equals the value captured in T-3; rows for any other `repo_root` (if present) are untouched (`SELECT count(*) FROM features WHERE repo_root != '/Users/spidey/code/orchestrator'` unchanged)
  depends: T-4

- [x] T-6: Create `config/steps/register-with-orchestrator-home.yaml` inline step contract (no `agent:` field) — `id`, `intent`, `inputs`, `instruction` invoking `config/scripts/register-repo.sh`, `verify` (registry contains repo path AND DB has features rows for repo), `outputs` listing the registry + DB paths; explicitly notes non-blocking on script failure; the `verify` block MUST include the explicit non-blocking note: "if duckdb exits non-zero, bootstrap continues" (covers DuckDB exclusive-lock race documented in design.md Risks)
  Verify: `yq '.id, .intent, .inputs, .instruction, .verify, .outputs' config/steps/register-with-orchestrator-home.yaml` returns all six sections non-null; `yq '.agent // "none"' config/steps/register-with-orchestrator-home.yaml` returns `"none"` (confirms inline shape); `yq '.verify' config/steps/register-with-orchestrator-home.yaml | grep -F 'if duckdb exits non-zero, bootstrap continues'` exits 0
  depends: T-2

- [x] T-7: Wire the step into `config/workflows/bootstrap.yaml` — add `metrics: true` to `defaults`, add `--no-metrics: { sets: { metrics: false } }` to `flags`, insert `- register-with-orchestrator-home if metrics` between `write-bootstrap-state` and `verify-report` in `setup.steps`
  Verify: `yq '.defaults.metrics' config/workflows/bootstrap.yaml` returns `true`; `yq '.flags["--no-metrics"].sets.metrics' config/workflows/bootstrap.yaml` returns `false`; structural ordering check via `yq '.phases[] | select(.name == "setup") | .steps' config/workflows/bootstrap.yaml` — confirm the resulting list contains `write-bootstrap-state`, then `register-with-orchestrator-home if metrics` immediately after, then `verify-report` immediately after that (e.g., capture index of `write-bootstrap-state` via `yq '.phases[] | select(.name == "setup") | .steps | to_entries | .[] | select(.value | test("write-bootstrap-state")) | .key'` and confirm index+1 matches the new step and index+2 matches `verify-report`); `yq '.' config/workflows/bootstrap.yaml > /dev/null` exits 0 (valid YAML)
  depends: T-6

- [x] T-8: Update `spec/project.yaml` — add `duckdb` and `yq` to `context.tech_stack`; add a `metrics-db-derived` learning entry per the focus brief (id, summary, evidence linking to this feature)
  Verify: `yq '.context.tech_stack | contains(["duckdb", "yq"])' spec/project.yaml` returns `true`; `yq '.learnings[] | select(.id == "metrics-db-derived")' spec/project.yaml` returns a non-empty mapping
  depends: T-7

- [x] T-9: Acceptance test for AC-9 (sql-injection / malformed change_id defense) — create a temporary archive directory containing two state.yaml fixtures: one valid (`change_id: good-fixture-001`) and one malformed (`change_id: "bad'; DROP TABLE features;--"` or any value violating `^[a-z0-9._-]+$`). Point `register-repo.sh` at this fixture archive (e.g., via a temp `REPO_ROOT` symlink) and run it.
  Verify: stderr contains exactly one line matching `^skip: change_id has unsafe chars: ` for the malformed fixture; the script exits 0; `SELECT count(*) FROM features WHERE change_id = 'good-fixture-001'` returns 1; `SELECT count(*) FROM features WHERE change_id LIKE '%DROP%'` returns 0; the `features` table still exists (no DDL damage); the script's final report line shows `skipped` incremented by at least 1
  depends: T-3

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD off (tdd_required: false) — Verify steps replace red/green discipline -->
