# Discovery Brief — learn-and-telemetry-on-duckdb (HL-282)

## Problem Statement

`/learn` and `/telemetry` both read workflow metrics by globbing
`spec/changes/archive/*/state.yaml` and parsing YAML inline. The
`cross-repo-metrics-duckdb` feature (shipped) ingests those same files
into `$ORCHESTRATOR_HOME/metrics.duckdb`. The consumers have not caught
up: they still do single-repo, per-file parsing when a queryable
fleet-wide index is available. This ticket closes the producer-consumer
gap so both skills benefit from cross-repo aggregation.

---

## Current State — Where YAML Parsing Happens

### `/learn` (`~/.claude/skills/learn/SKILL.md`)

| Section | Line | Current pattern |
|---|---|---|
| §2b cross-feature retry | 54 | `ls spec/changes/archive/*/state.yaml` (last 10, sorted mtime) |
| §5b rule effectiveness | 255–256 | reads `step_history[]` from current feature's state.yaml only |
| §5b-decay trigger | 274 | `ls spec/changes/archive/*/state.yaml \| wc -l` |
| §5c adaptive quality bar | 314 | `ls -t spec/changes/archive/*/state.yaml \| head -5` |
| Rule metadata cycle count | 230 | `ls spec/changes/archive/*/state.yaml \| wc -l` |

### `/telemetry` (`~/.claude/skills/telemetry/SKILL.md`)

| Section | Line | Current pattern |
|---|---|---|
| Data gather source list | 27–28 | `spec/changes/archive/*/state.yaml` + `$WORKFLOW_STATE_DIR/*/state.yaml` |
| `recent` mode | 35 | last 5 archived files by mtime |
| `all` mode | 36 | full glob |
| Cost/token/quality aggregation | 40–65 | in-memory YAML parsing per file |
| Trend analysis | 130–138 | manual first-half vs second-half slice |

---

## DuckDB Schema — What Exists Today

Single table `features` in `$ORCHESTRATOR_HOME/metrics.duckdb`
(from `config/scripts/register-repo.sh` lines 76–88):

```
repo_root      VARCHAR NOT NULL
change_id      VARCHAR NOT NULL  -- PK: (repo_root, change_id)
schema         VARCHAR
status         VARCHAR
started_at     VARCHAR
completed_at   VARCHAR
payload_json   VARCHAR           -- full state.yaml serialized as JSON
ingested_at    TIMESTAMP
```

**Critical finding**: There is NO separate `step_history` table.
Every retry reason, review score, and step-level metric lives inside
`payload_json` as a nested JSON blob. Querying step-level data requires
`json_extract()` or DuckDB JSON unnest (`json_each()`). This is the
primary complexity driver for §2b retry analysis.

Live DB has 8 rows (all from one repo). `step_history` is present in
all 8 rows (5–17 steps each). `metrics.*` fields are populated in ~5 of
8 rows (3 older features pre-date the cost-zero fix in a6a2e95).

---

## Constraints

1. **DuckDB may be absent**: New clones before `register-repo.sh` runs
   have no `metrics.duckdb`. Both skills must fall back to YAML glob.
   `register-repo.sh` line 62 already exits 0 when duckdb is missing
   — the same guard pattern applies to the query helper.

2. **Active workflows stay YAML-only**: The DB is post-archive only.
   `$WORKFLOW_STATE_DIR/*/state.yaml` (in-progress features) is not
   ingested. `/telemetry` must merge DuckDB (archived) + YAML (active).

3. **Output format must be stable**: The `/telemetry` dashboard format
   (SKILL.md lines 80–128) must not change. Callers and tests depend on it.

4. **Skills are prose, not code**: SKILL.md files are instructions
   executed by an LLM agent. Any `metrics-query.sh` helper must be
   simple enough to invoke from those prose instructions.

5. **`register-repo.sh` is out of scope**: Producer is correct as-is
   per idea.md. Any schema additions (e.g., a `step_history` table)
   require an explicit scope unlock.

---

## Integration Points — Files That Will Change

| File | Change type | Why |
|---|---|---|
| `~/.claude/skills/learn/SKILL.md` | Edit | Replace 5 YAML-glob patterns with DuckDB queries + fallback |
| `~/.claude/skills/telemetry/SKILL.md` | Edit | Replace data-gather section with DuckDB + YAML merge for active |
| `config/scripts/metrics-query.sh` | New file | Named query wrapper invoked by skills; shells out to `duckdb -csv` |
| `config/scripts/register-repo.sh` | No change | Producer is correct as-is |
| `config/scripts/compute-swe-metrics.sh` | No change | Metric writer; not a consumer |

---

## Use Cases

**UC-1 (happy path): `/telemetry recent` with populated DB**
Actor runs `/telemetry` after several completed features across repos.
Skill checks DB exists and duckdb is installed, calls
`metrics-query.sh cost-trend --limit 5` and `metrics-query.sh
retry-hotspots --limit 5`. Merges any active in-progress features from
`$WORKFLOW_STATE_DIR` YAML. Dashboard renders with cross-repo fleet data.
Outcome: cost trend, quality, retry hotspots shown; output format unchanged.

**UC-2 (happy path): `/learn` §2b cross-feature retry with DB**
Orchestrator runs `/learn` after completing a feature.
§2b calls `metrics-query.sh retry-hotspots --limit 10`. The helper
uses `json_each()` to unnest `step_history` from `payload_json` and
returns `(step_id, reason, feature_count, total_retries)` rows.
Systemic patterns (feature_count >= 3, retry_rate > 30%) are flagged.
Evaluator receives fleet-wide signal instead of single-repo last-10.
Outcome: workflow-improver gets richer retry signal.

**UC-E1 (error path): DuckDB absent — new install**
Developer on a fresh clone; `register-repo.sh` has not run yet.
`metrics-query.sh` detects `metrics.duckdb` is missing or `duckdb`
binary is absent. Returns exit 1 and empty stdout.
Both skills detect empty/error result and fall back to the existing
`spec/changes/archive/*/state.yaml` glob logic unchanged.
Outcome: identical behavior to pre-migration; no visible failure.

**UC-E2 (edge): DB exists but repo not registered**
Developer's repo was never passed to `register-repo.sh`.
All queries return empty result sets. Skills treat empty result as
"no data" — show empty dashboard or skip cross-feature analysis.
Outcome: graceful empty-state, no crash.

---

## Key Decisions (for Architect to resolve)

### KD-1: `metrics-query.sh` helper — build it

Build `config/scripts/metrics-query.sh` wrapping `duckdb -csv`.
Skills invoke it by name with named query IDs (`cost-trend`,
`retry-hotspots`, `cycle-count`) and optional flags (`--repo`, `--limit`).
SQL stays in the script, testable independently. The idea.md explicitly
calls for this; no alternative warrants consideration.

### KD-2: step_history access — JSON unnest vs new table (UNRESOLVED)

The §2b target query needs per-step retry data across features.
Two paths:

- **Option A (JSON unnest, default)**: Keep current schema. Query uses
  `SELECT json_extract(s.value, '$.step_id'), json_extract(s.value, '$.retry_reasons') FROM features f, json_each(json_extract(f.payload_json, '$.step_history')) s`.
  No schema change. Complex SQL but no producer changes.
- **Option B (denormalized table)**: Add `step_history` table populated
  at ingest time. Simpler queries. Requires register-repo.sh changes
  (currently out of scope) and DDL migration for existing installs.

Given the out-of-scope constraint on register-repo.sh, Option A is the
default path. Architect should confirm before implementation.

### KD-3: `/telemetry` default scope — fleet vs per-repo (UNRESOLVED)

The idea.md says "cross-repo by default; per-repo filter via WHERE
repo_root = ?". Defaulting to fleet-wide is a behavior change.
Single-repo users will see data from all registered repos unexpectedly.
Architect should decide: fleet-wide default OR per-repo default with
opt-in fleet flag.

### KD-4: cycles table / `metrics.jsonl` fate (UNRESOLVED)

`/learn` §5 writes to `.claude/metrics.jsonl` (line 245). The idea.md
floats adding a `cycles` table to DuckDB. Creating it requires new DDL
not in the current schema. Decision: add DuckDB write alongside
`metrics.jsonl`, OR keep `metrics.jsonl` only, OR replace it. The
safest default is keep `metrics.jsonl` and defer the `cycles` table to
a follow-on ticket.

---

## Unresolved Questions

1. **step_history unnest vs table**: JSON unnest (Option A) or new
   `step_history` table unlocking register-repo.sh scope (Option B)?

2. **metrics-query.sh env contract**: What env var resolves the DB path
   at call time? Does the skill need to export `ORCHESTRATOR_HOME` and
   `METRICS_DB` before calling, or does the script resolve them itself?

3. **Fleet vs per-repo default in `/telemetry`**: Cross-repo by default
   changes existing single-repo user experience. Confirm intent.

4. **cycles table scope**: In scope for this ticket or follow-on?

5. **Fallback verbosity**: Should skills log "DuckDB not available,
   using local archive" or stay silent on fallback?

6. **Test coverage**: Should `metrics-query.sh` get a test file
   paralleling `compute-swe-metrics.test.sh`? Pattern already
   established: fixture DB via `METRICS_DB=$TMPDIR/test.duckdb`.

---

## Design Approaches Considered

### Approach A — Minimal JSON-unnest wrapper (script resolves env, self-contained)

- **Description**: Single new script `config/scripts/metrics-query.sh` that
  owns env resolution (`ORCHESTRATOR_HOME` default `$HOME/.orchestrator`,
  `METRICS_DB` default `$ORCHESTRATOR_HOME/metrics.duckdb`) and ships a
  fixed set of named queries (`cost-trend`, `retry-hotspots`, `cycle-count`,
  `quality-trend`). Step-level queries use `json_each(json_extract(payload_json, '$.step_history'))`
  against the existing `features` table. No schema change.
- **step_history access**: JSON unnest via `json_each` (KD-2 Option A).
- **metrics-query.sh location / DB resolution**: `config/scripts/metrics-query.sh`.
  Script resolves `METRICS_DB` itself from env with defaults; skills call it
  with no env plumbing. Exit 1 + empty stdout when binary missing, DB missing,
  or query returns no rows beyond header.
- **/telemetry and /learn fallback**: Skills check exit code / empty stdout
  from helper. Non-zero or empty → fall back to existing
  `spec/changes/archive/*/state.yaml` glob logic unchanged. Silent fallback
  (no stderr noise) — single-line comment in skill prose documents the
  behavior.
- **/telemetry default scope**: Per-repo default (`--repo "$PWD"` passed
  by the skill); opt-in fleet via `--fleet` or `--repo all`. Preserves
  today's single-repo UX.
- **cycles table**: Deferred. `/learn` keeps `.claude/metrics.jsonl`
  writes untouched; follow-on ticket adds `cycles` table.
- **Reused modules**: `register-repo.sh` guard pattern (duckdb presence
  check), `compute-swe-metrics.test.sh` fixture pattern (for optional
  test), existing `features` table schema.
- **Pros**: No schema change, no producer change, smallest surface,
  matches idea.md and KD-1 verbatim, preserves single-repo UX, keeps
  `metrics.jsonl` authoritative for cycles.
- **Cons**: §2b retry-hotspots SQL is verbose (nested `json_extract` +
  `json_each`). Per-repo default means users miss fleet value until
  they opt in.
- **Complexity**: **S** (2)
- **Reused modules count**: 3

### Approach B — JSON-unnest wrapper with fleet-wide default + verbose fallback

- **Description**: Same script + query set as A, but `/telemetry` defaults
  to fleet-wide (`WHERE 1=1`) with `--repo` filter for single-repo view,
  matching idea.md's stated "cross-repo by default". Fallback logs a
  one-line stderr notice ("DuckDB unavailable, using local archive").
- **step_history access**: JSON unnest (Option A).
- **metrics-query.sh location / DB resolution**: Same as A.
- **/telemetry fallback**: Same fallback trigger, but emits visible
  stderr notice on downgrade.
- **/telemetry default scope**: Fleet-wide. Single-repo users opt in
  with `--repo "$PWD"`.
- **cycles table**: Deferred (same as A).
- **Reused modules**: Same 3 as A.
- **Pros**: Matches idea.md's stated cross-repo default; fallback
  notice aids debugging.
- **Cons**: Behavior change for single-repo users — they suddenly see
  rows from unrelated repos. Stderr noise on every fresh clone until
  `register-repo.sh` runs. More user-visible risk for no additional
  capability.
- **Complexity**: **S** (2)
- **Reused modules count**: 3

### Approach C — New `step_history` table + `cycles` table (full normalization)

- **Description**: Unlock scope on `register-repo.sh` to add
  `step_history(repo_root, change_id, step_id, retry_count, retry_reasons, ...)`
  and `cycles(repo_root, cycle_id, started_at, rules_added, ...)` tables.
  Populate at ingest. `/learn` writes cycles to DuckDB instead of (or
  alongside) `metrics.jsonl`. Clean, flat SQL in `metrics-query.sh`.
- **step_history access**: Direct SELECT from `step_history` table
  (Option B).
- **metrics-query.sh location / DB resolution**: Same as A, simpler SQL.
- **/telemetry fallback**: Same as A.
- **/telemetry default scope**: Per-repo default.
- **cycles table**: In scope. New DDL + `/learn` writer change +
  backfill for existing installs.
- **Reused modules**: `register-repo.sh`, test fixtures, features table.
- **Pros**: Cleanest SQL long-term; unlocks richer cross-cycle
  analytics; removes JSON-extract ceremony.
- **Cons**: Violates the explicit "register-repo.sh out of scope"
  constraint from idea.md / Constraint 5; requires migration for the
  8 rows already in the live DB and any other installs; expands
  producer surface that the ticket explicitly excluded; changes
  `/learn` metrics writer semantics.
- **Complexity**: **L** (4)
- **Reused modules count**: 3

---

## Key Decisions

### Selection criteria (auto, deterministic)

| Approach | Complexity | Reused modules | Alphabetical |
|---|---|---|---|
| A | S (2) | 3 | A |
| B | S (2) | 3 | B |
| C | L (4) | 3 | C |

Lowest complexity tier is S (A and B tied at 2). Reused-modules count is
tied at 3. Alphabetical tie-break selects **Approach A**.

### Chosen approach: **A — Minimal JSON-unnest wrapper**

Rationale: lowest complexity tier, no schema or producer change
(honors Constraint 5), smallest behavioral blast radius for existing
single-repo users, preserves `metrics.jsonl` as cycles source of truth
pending a follow-on ticket. Matches KD-1 verbatim.

### Resolution of the four open design questions

- **KD-2 (step_history access)** — **Option A: JSON unnest** via
  `json_each(json_extract(payload_json, '$.step_history'))`. No schema
  change. `register-repo.sh` remains untouched per Constraint 5. The
  verbosity of the unnest SQL is contained inside `metrics-query.sh`,
  so skill prose stays clean.

- **KD-3 (`/telemetry` default scope)** — **Per-repo default**, fleet
  opt-in via `--fleet` (or `--repo all`). `metrics-query.sh` accepts
  `--repo <path>` and defaults to `$PWD` when the skill does not pass
  `--fleet`. Preserves today's single-repo UX; documented note in
  `/telemetry` SKILL.md tells users how to opt into fleet view. This
  intentionally diverges from the idea.md phrasing ("cross-repo by
  default") on the grounds that a silent behavior change for every
  existing single-repo user is a worse default than a one-flag opt-in.

- **KD-4 (cycles table / `metrics.jsonl` fate)** — **Deferred.**
  Keep `.claude/metrics.jsonl` as the cycles store. No `cycles` table
  in this ticket. Follow-on ticket can add DuckDB `cycles` with
  backfill from `metrics.jsonl` once the consumer path is proven.

- **KD-5 (metrics-query.sh env contract)** — **Script resolves env
  itself.** Skills call `metrics-query.sh <query-id> [--repo P]
  [--fleet] [--limit N]` with no env plumbing. Inside the script:
  `ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"` and
  `METRICS_DB="${METRICS_DB:-$ORCHESTRATOR_HOME/metrics.duckdb}"`.
  Tests override by exporting `METRICS_DB=$TMPDIR/test.duckdb` before
  invocation (same pattern as `compute-swe-metrics.test.sh`).

### Additional resolutions (from Unresolved Questions §5–6)

- **Fallback verbosity**: Silent. Helper exits non-zero / empty stdout;
  skills fall back without stderr noise. Rationale: fresh clones
  without `register-repo.sh` run would otherwise spam stderr on every
  invocation.
- **Test coverage**: `metrics-query.sh` ships with
  `metrics-query.test.sh` paralleling `compute-swe-metrics.test.sh`,
  using a fixture DB at `$TMPDIR/test.duckdb` with 2–3 seeded rows
  (multi-repo, populated `step_history`, one row missing
  `metrics.*`).
