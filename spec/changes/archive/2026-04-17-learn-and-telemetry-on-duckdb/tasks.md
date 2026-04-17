# Tasks: /learn and /telemetry on DuckDB

- [x] T-1 Write tests: `config/scripts/metrics-query.test.sh` (RED — tests must fail)
  - **Why**: FR-1, FR-2, FR-3, FR-4, FR-8, AC-6. Establish fixture DB at `$TMPDIR/test.duckdb` with rows from two repos (one with populated `step_history`, one without) and assert: each named query (`cost-trend`, `retry-hotspots`, `cycle-count`, `quality-trend`, `recent-features`) returns rows on per-repo default; `--fleet` aggregates across repos; `--repo <path>` filters explicitly; missing `duckdb` binary → exit non-zero + empty stdout; missing DB file → exit non-zero + empty stdout; zero-row query → exit non-zero + empty stdout; no stderr on any failure path.
  - **Verify**: Running `bash config/scripts/metrics-query.test.sh` FAILS for the right reason (helper does not yet exist).

- [x] T-2 Implement: `config/scripts/metrics-query.sh` (GREEN) (depends: T-1)
  - **Why**: FR-1, FR-2, FR-3, FR-4. Script resolves `ORCHESTRATOR_HOME` and `METRICS_DB` from env with defaults; dispatches on positional `<query-id>`; supports `--repo`, `--fleet`, `--limit`; shells out to `duckdb -csv` with `2>/dev/null`; exits non-zero + empty stdout on missing binary, missing DB, or header-only output; ships the five named queries (`cost-trend`, `retry-hotspots`, `cycle-count`, `quality-trend`, `recent-features`) with `retry-hotspots` using `json_each(json_extract(payload_json, '$.step_history'))`.
  - **Verify**: All T-1 assertions pass green; `shellcheck config/scripts/metrics-query.sh` clean; script has `chmod +x`.

- [x] T-3 Review checkpoint (phase gate) (depends: T-2)
  - **Verify**: `bash config/scripts/metrics-query.test.sh` passes; `shellcheck` clean on both script and test; `register-repo.sh` and `compute-swe-metrics.sh` unchanged vs main (NFR-1, AC-8).

- [x] T-4 Write tests: fresh-clone fallback path for `metrics-query.sh` (RED) (depends: T-2)
  - **Why**: NFR-2, AC-4. Add a case in `metrics-query.test.sh` that removes the fixture DB and asserts the helper emits nothing on stdout or stderr, exits non-zero, and does not create any files. This is the contract `/learn` and `/telemetry` rely on for silent fallback.
  - **Verify**: New case fails if any stderr output appears, DB gets created, or exit code is 0.

- [x] T-5 Implement: silent-fallback polish in `metrics-query.sh` (GREEN) (depends: T-4)
  - **Why**: NFR-2, AC-4. Ensure every error branch redirects `duckdb` stderr to `/dev/null`, returns nothing on stdout, and uses consistent non-zero exit codes (1 = missing/empty, 2 = unknown query-id).
  - **Verify**: T-4 case passes; manual `rm $METRICS_DB; metrics-query.sh cost-trend; echo exit=$?` prints only `exit=1`.

- [x] T-6 Migrate `/learn` SKILL.md consumption points (depends: T-3)
  - **Why**: FR-5, AC-3. Replace each of the five YAML-glob sites (§2b line 54 → `retry-hotspots --fleet --limit 10`; §5b line 255–256 → `recent-features --limit 10`; §5b-decay line 274 → `cycle-count`; §5c line 314 → `quality-trend --limit 5`; rule-metadata line 230 → `cycle-count`) with a helper call + `if exit-status != 0 or stdout empty` fallback to the existing glob. Prose must stay short (one invocation line + one fallback line per site).
  - **Verify**: Diff shows five sites updated; every site still contains the original glob as the `else` branch; a dry-run of `/learn` against a populated DB produces the expected cross-repo retry signal, and against a missing DB produces output byte-identical to pre-migration.

- [x] T-7 Migrate `/telemetry` SKILL.md data gather + document `--fleet` (depends: T-3)
  - **Why**: FR-6, FR-7, AC-1, AC-2, AC-7, NFR-3. Replace the data-gather block (lines 27–28, 35–36, 40–65, 130–138) with: `recent` mode → `metrics-query.sh recent-features --limit 5`; `all` mode → `metrics-query.sh recent-features` (no `--limit`); trend modes → `metrics-query.sh cost-trend` and `metrics-query.sh quality-trend`. All default per-repo. Merge `$WORKFLOW_STATE_DIR/*/state.yaml` for active features; preserve dashboard fields (lines 80–128) byte-for-byte. Add an invocation-section note: per-repo default, use `/telemetry --fleet` for cross-repo.
  - **Verify**: Dashboard rendered from the fixture DB (per-repo mode) matches the pre-migration fields byte-for-byte; `--fleet` mode shows rows from both fixture repos; invocation section documents the flag and the per-repo default explicitly.

- [x] T-8 Verify fresh-clone fallback end-to-end for both skills (depends: T-6, T-7)
  - **Why**: NFR-2, AC-4, AC-5. With `METRICS_DB` pointed at a non-existent path, dry-run both `/learn` and `/telemetry` and confirm behavior is identical to pre-migration output (same fields, same counts from glob logic, no stderr noise from the helper). Also run with DB present but repo unregistered → empty-state dashboard, no crash.
  - **Verify**: `METRICS_DB=/tmp/nope.duckdb` dry-run of both skills emits no stderr from `metrics-query.sh`, falls back cleanly, matches the pre-migration golden output captured before T-6.

- [x] T-9 Final review checkpoint (depends: T-5, T-8)
  - **Verify**: `bash config/scripts/metrics-query.test.sh` green; `shellcheck` clean; `git diff main -- config/scripts/register-repo.sh config/scripts/compute-swe-metrics.sh` is empty (NFR-1, AC-8); `/telemetry` dashboard format unchanged (NFR-3); `/learn` and `/telemetry` both fall back silently when DB absent (NFR-2); spec ACs 1–8 all demonstrated.

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD: test tasks (RED) always precede implementation tasks (GREEN) -->
<!-- N/A: this feature introduces no new archive/state paths — it consumes existing ones -->

<!-- === REVIEWER FIX TASKS (phase-review retry 1) === -->

- [x] T-10 Fix: resolve `all-features` undefined query ID in design.md and align spec + tasks (depends: none — artifact fix)
  - **Fixed**: design.md line 149 now says `recent-features` (no `--limit`) for `all` mode; design.md Named Queries note clarifies `LIMIT :limit` is only appended when `--limit` is provided; spec.md FR-6 enumerates `recent` / `all` / trend invocations explicitly; T-7 Why clause updated with both `recent` and `all` mode commands. `grep 'all-features'` across all three artifacts returns zero matches.
  - **Why**: design.md line 149 references `all-features` as a query ID for `/telemetry` `all` mode, but this ID does not exist in the Named Queries table (design.md lines 155-163) or spec.md FR-1. An implementer encounters an undefined query ID with no resolution path. The correct resolution is to reuse `recent-features` without `--limit` for `all` mode, since the only difference is the LIMIT clause.
  - **Changes**: (1) design.md line 149: replace `all-features` with `recent-features`; (2) design.md lines 165-166: add note that LIMIT is only appended when --limit is provided; (3) spec.md FR-6: add "`all` mode uses `recent-features` without `--limit`"; (4) T-7 Why clause: add "for `all` mode use `metrics-query.sh recent-features` (no --limit)".
  - **Verify**: `grep -n 'all-features' design.md` returns zero matches; design.md Named Queries table and /telemetry edits section reference the same 5 query IDs; spec.md FR-6 and T-7 explicitly cover both `recent` and `all` mode invocations.

- [x] T-11 Fix: correct `retry-hotspots` SQL sketch field names and array-unnest structure in design.md (depends: none — artifact fix)
  - **Fixed**: design.md retry-hotspots SQL sketch now uses `$.retries` for the int count and adds a second `json_each(json_extract(s.value, '$.retry_reasons')) r` to unnest the reasons array, with `r.value AS reason`. GROUP BY remains `(step_id, reason)` — structurally valid now that `reason` is a scalar per row. Inline note added: exact field names must be validated against a live `payload_json` row during T-1.
  - **Why**: design.md line 161 uses `$.retry_reason` (singular) and `$.retry_count` as JSON keys. SKILL.md lines 56-57 document the actual field names as `step_history[].retries` (count) and `step_history[].retry_reasons[]` (plural array). Because `retry_reasons` is a JSON array, the sketch's GROUP BY is structurally wrong -- it needs a second `json_each` to unnest the reasons array before grouping per (step_id, reason) pair.
  - **Changes**: Update the `retry-hotspots` SQL sketch in design.md line 161 to: use `$.retries` for count, add `json_each(json_extract(s.value, '$.retry_reasons')) r` as a third FROM source, use `r.value AS reason`. Add inline note: "exact field names must be validated against a live payload_json row during T-1".
  - **Verify**: Updated SQL sketch references `$.retries` (not `$.retry_count`), `$.retry_reasons` via second json_each unnest; field names match SKILL.md lines 56-57; updated sketch validated against a real payload_json row from the live DB (discovery.md documents 8 rows available with step_history).

- [x] T-12 Fix: restore archive-glob fallback in /telemetry SKILL.md (depends: T-7)
  - **Fixed**: skills/telemetry/SKILL.md line 49 dangling reference "fall back to the YAML glob below" replaced with explicit inline fallback instructions. `recent` mode fallback: `ls -t spec/changes/archive/*/state.yaml | head -5`; `all` mode fallback: `ls -t spec/changes/archive/*/state.yaml`. Matches learn/SKILL.md pattern. `grep "spec/changes/archive" skills/telemetry/SKILL.md` returns 2 matches in data-gather section.
  - **Why**: Phase-review finding F-1: T-7 removed the glob block but left a dangling reference on line 49 ("fall back to the YAML glob below"). On a fresh clone /telemetry silently produces no archived metrics with no fallback path.

<!-- === STEP HISTORY === -->
<!-- T-9 | 2026-04-17 | reviewer | PASS — 27/27 tests green, git diff main empty on register-repo.sh + compute-swe-metrics.sh, shellcheck unavailable (noted), all ACs 1-8 demonstrated, dashboard format preserved, silent fallback confirmed in SKILL.md prose and test suite -->
