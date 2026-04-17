# Discovery — cross-repo-metrics-duckdb

## Problem Statement

The orchestrator accumulates per-feature metrics in `spec/changes/archive/*/state.yaml` files spread across individual repos. There is no cross-repo aggregate index, so trend analysis, benchmark comparisons, and learning-loop queries require ad-hoc shell scripting against each repo's archive separately. This feature establishes a single trigger point (bootstrap) that registers the repo in a central YAML registry at `$ORCHESTRATOR_HOME/metrics-registry.yaml`, walks all archived state.yaml files, converts them to JSON via `yq`, and ingests them into a DuckDB database at `$ORCHESTRATOR_HOME/metrics.duckdb` — enabling SQL-based queries across all registered repos without any per-step instrumentation.

## Use Cases

- UC-1 (happy path): Developer runs bootstrap on a repo for the first time. `register-repo.sh` appends the repo root to `metrics-registry.yaml`, walks `spec/changes/archive/*/state.yaml`, converts each to JSON, ingests into `metrics.duckdb`. DuckDB ends up with one row per archived feature.
- UC-2 (happy path): Developer re-runs bootstrap on an already-registered repo (e.g. after new features are archived). Script detects repo already in registry (idempotent), re-walks archive, upserts new rows into DuckDB. Existing rows are not duplicated.
- UC-3 (happy path): Developer bootstraps a brand-new repo with no archive yet. Registry append succeeds. Archive walk finds zero state.yaml files. DuckDB ingest is a no-op. Step completes without error.
- UC-E1 (edge/error): `yq` or `duckdb` not installed. Step detects missing tool, logs error message, exits non-blocking (metrics registration failure must not block bootstrap). Bootstrap continues to `verify-report`.
- UC-E2 (edge/error): A state.yaml has malformed YAML (e.g. partially written during a crash). `yq` conversion fails for that file. Script logs the bad path and continues ingesting the remaining files.
- UC-E3 (edge/error): `--no-metrics` flag is passed. `register-with-orchestrator-home` step is filtered out of the workflow plan. Bootstrap proceeds without any DuckDB writes.

## Existing Patterns

- **Inline step contract (no `agent:` field)**: `config/steps/autopilot-preflight.yaml` (line 1–48) and `config/steps/write-bootstrap-state.yaml` (line 1–97) are both inline — they have `id:`, `intent:`, `inputs:`, `rules:`, `instruction:`, `verify:`, `outputs:` but no `agent:` field. The new `register-with-orchestrator-home.yaml` should follow this exact shape.
- **Conditional step in bootstrap.yaml**: `setup-portless if portless` (line 62) and `check-linear-config if linear` (line 63) — single-line `- <step-id> if <flag>` syntax.
- **`--no-*` flag pattern in workflows**: `config/workflows/feature.yaml` lines 35–42, `config/workflows/bootstrap.yaml` line 21 (`--no-portless: sets: { portless: false }`). `--no-metrics` would follow this identical pattern.
- **`defaults:` block in bootstrap.yaml**: lines 15–18 — `portless: true` shows boolean defaults. `metrics: true` adds one line here.
- **yq usage for YAML read**: `config/scripts/autopilot-session-rollup.sh` lines 30–43 uses `yq ".path.to.field" file` for extraction. The script note on line 25 ("yq v4 mikefarah doesn't support reduce") is a known constraint to carry forward.
- **yq for YAML-to-JSON conversion**: `yq -o json <file>` (confirmed working with mikefarah yq v4.52.5). Used in estimate-cost.sh implicitly via ARCHIVE_GLOB pattern for archive walks at lines 139–163.
- **Archive walk pattern**: `config/scripts/estimate-cost.sh` lines 139–163 — `for archive_state in $ARCHIVE_GLOB; do [[ -f "$archive_state" ]] || continue; ...` — glob is `$REPO_ROOT/spec/changes/archive/*/state.yaml`. This is the exact pattern for `register-repo.sh`.
- **Idempotent pricing.yaml read**: `config/scripts/compute-swe-metrics.sh` lines 33–34 — resolves path via env var with fallback: `PRICING_FILE="${PRICING_FILE:-${ORCHESTRATOR_HOME:-$(git rev-parse --show-toplevel)}/config/pricing.yaml}"`. Same pattern for locating `metrics-registry.yaml` and `metrics.duckdb`.
- **Non-blocking failure pattern**: `compute-swe-metrics.yaml` lines 13–14 — "Metrics script failure is non-blocking — proceed with null placeholders." Same constraint applies to `register-with-orchestrator-home`.
- **Bootstrap state.yaml shape** (produced by `write-bootstrap-state`): see `config/steps/write-bootstrap-state.yaml` lines 47–76. Fields include `schema: bootstrap`, `status:`, `step_history[]` with `{step_id, phase, status, agent}` entries. No `metrics:` block — bootstrap schema state.yaml is simpler than feature state.yaml. Archive walk in `register-repo.sh` should filter on `schema: feature|bugfix|chore` to avoid ingesting bootstrap or spike state files.

## Integration Points

- **bootstrap.yaml setup phase**: new step `register-with-orchestrator-home` goes after `write-bootstrap-state` (line 64) and before `verify-report` (line 65). The step reads no prior-step outputs — it's self-contained.
- **ORCHESTRATOR_HOME**: resolved at runtime via env var. On this machine: `/Users/spidey/code/orchestrator`. `~/.config/orchestrator/` contains only symlinks into `config/` subdirs (steps, workflows, scripts, etc.) — NOT a symlink to the repo root. `metrics.duckdb` and `metrics-registry.yaml` must live at `$ORCHESTRATOR_HOME` directly (the real repo path), not via `~/.config/orchestrator/`.
- **Archive glob**: `$REPO_ROOT/spec/changes/archive/*/state.yaml` — 10 state.yaml files currently in this repo's archive (11 directories, 10 with state.yaml).
- **DuckDB ingest path**: `yq -o json <state.yaml> | duckdb metrics.duckdb "INSERT INTO features SELECT * FROM read_json_auto('/dev/stdin')"` — confirmed `read_json_auto` parses the JSON output from `yq -o json` correctly (tested: `change_id`, `schema`, `status`, `metrics` struct all land correctly).
- **Nested field handling**: `metrics` lands as a deeply nested STRUCT in DuckDB. `per_agent_tokens` and `per_agent_tools` are stored as VARCHAR (quoted JSON strings in the YAML) — no flattening needed at ingest time.
- **gitignore**: `.state/` is already gitignored (line 7 of `.gitignore`). `*.duckdb` is NOT yet in `.gitignore`. `metrics.duckdb` at `$ORCHESTRATOR_HOME` root would not be ignored by current rules — needs a `*.duckdb` entry.
- **spec/project.yaml tech_stack**: currently `[bash, zsh, yaml]` at line 56. Needs `duckdb` and `yq` added.

## Constraints

- Step contracts must stay agent-agnostic — no tool-specific instructions in `register-with-orchestrator-home.yaml`.
- `metrics.duckdb` must not be committed. Either add `*.duckdb` to `.gitignore` (preferred) or locate it outside any git repo.
- ORCHESTRATOR_HOME on this machine is `/Users/spidey/code/orchestrator` (a real directory, not a symlink). `~/.config/orchestrator/` symlinks point INTO it, not TO it. Writing `metrics.duckdb` to `$ORCHESTRATOR_HOME` is safe and writable.
- **Worktree gotcha** (from `spec/project.yaml` line 42): `install.sh` re-points symlinks. Running `make setup` from the worktree would repoint `~/.config/orchestrator/` symlinks to the worktree. Testing `register-repo.sh` from the worktree must NOT run `make setup` / `install.sh`. Script should be tested by calling it directly with explicit `ORCHESTRATOR_HOME` env override.
- `yq` v4 (mikefarah) — no `reduce`, limited arithmetic. Arithmetic should go through `awk` as in `autopilot-session-rollup.sh`.
- DuckDB v1.5.2 installed at `/opt/homebrew/bin/duckdb`. No library bindings — CLI-only.
- bootstrap.yaml currently has no `metrics` default. Adding `metrics: true` and `--no-metrics` flag follows the exact same pattern as `portless: true` / `--no-portless`.

## Key Decisions (build-or-reuse)

- **Reuse `yq`**: already in use (`autopilot-session-rollup.sh`). Version confirmed v4.52.5. No new install needed.
- **Reuse `duckdb` CLI**: preinstalled at `/opt/homebrew/bin/duckdb` v1.5.2. No new install needed.
- **Reuse archive walk pattern**: `estimate-cost.sh` lines 139–163 provides the exact glob+loop pattern.
- **Reuse `--no-*` flag + `defaults:` pattern**: bootstrap.yaml lines 15–18 and 21–23 are the template.
- **Reuse inline step contract shape**: `autopilot-preflight.yaml` is the cleanest inline step example.
- **Build new**: `config/scripts/register-repo.sh` — no existing script handles registry append or DuckDB ingest.
- **Build new**: `config/steps/register-with-orchestrator-home.yaml` — no existing step contract for this.
- **No external libraries**: bash + `yq` + `duckdb` CLI only. No jq required (yq handles YAML→JSON).
- **Selected design approach (see `design.md`)**: Approach 1 — Minimal/Simple. Single wide `features` table with explicit typed identifier columns + `payload_json VARCHAR`; `INSERT OR REPLACE` upsert keyed on `(repo_root, change_id)`; ingest all schemas (no filtering); `grep -Fxq + echo >>` for idempotent registry append. Selected per auto-approve rule (lowest complexity S=2, ties broken by greater reuse of existing patterns).

## Open Questions

1. **DuckDB schema strategy**: one wide `features` table via `read_json_auto` (schema inferred per-run, evolves with state.yaml shape) vs. explicit CREATE TABLE with known columns? `read_json_auto` is simpler but may produce different column types across archive versions (e.g., `resolve_rate` seen as `bigint` when value is `1` vs `double` when `0.75`). Does the Architect want a stable typed schema or accept auto-inferred?
2. **Idempotent registry append**: best bash pattern for "append repo to YAML list only if not already present" without `yq` edit-in-place quirks. Options: `grep` check + `echo >>` (fragile for YAML lists), `yq -i` (requires handling missing key init), or write/overwrite full file each time. Which does Architect prefer?
3. **DuckDB upsert strategy**: on re-run, how do we avoid duplicate rows? Options: `DELETE FROM features WHERE repo_root = ? AND change_id = ?` before INSERT; use `INSERT OR REPLACE` with a primary key on `(repo_root, change_id)`; or DROP+recreate the whole table on each run. The simplest (drop+recreate) loses cross-repo data if only one repo is re-ingested. Needs design decision.
4. **`metrics.duckdb` location conflict with worktrees**: if `ORCHESTRATOR_HOME` resolves differently from worktree vs main repo, concurrent bootstrap runs could write to different `.duckdb` files. Does register-repo.sh need to hardcode the canonical path, or always resolve via a known-stable mechanism (e.g., `git -C ~ rev-parse --show-toplevel` for the orchestrator repo specifically)?
5. **Schema filtering during archive walk**: should `register-repo.sh` ingest ALL schemas (bootstrap, spike, autopilot, feature, bugfix, chore) or only feature-class schemas? The metrics block shape differs by schema (spike/autopilot have null resolution fields). Does the Architect want a unified table or filtered to feature/bugfix/chore only?

## Out of Scope (per brief)

- Reader helpers / dashboard / metrics-query.sh
- Archive-time ingest fallback (only bootstrap trigger in v1)
- Per-step or per-agent normalization tables (feature-level only)
- Closing the estimate_vs_actual learning loop
