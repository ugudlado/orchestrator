# Tasks — cost-report-generator

TDD required: **no** (read-only aggregations over a well-defined
schema + one additive table; unit-test-after is acceptable per
project.yaml policy for this change type). Every task has a concrete
Verify line.

- [x] T-1: Extend `config/scripts/orchestrator_next/upsert.py`:
  add `tool_calls` table to `ensure_schema()` (schema per design.md);
  extend `upsert_step_event()` to fan out `usage.tools` into per-call
  rows with monotonic `call_seq` scoped to
  `(repo_root, change_id, phase, step_id, attempt)`, deleting any
  prior rows for that tuple before insert so retries stay idempotent.
  `is_mcp` derived from `tool_name.startswith("mcp__")`. Per-call
  token/cost/duration fields written as NULL. Add subprocess-style
  tests in `config/scripts/tests/test_upsert_tool_calls.py` covering:
  (a) empty/missing `usage.tools` → zero rows; (b) pure native
  (`{"Bash": 3}`) → 3 rows all `is_mcp=false` with `call_seq` 1,2,3;
  (c) pure MCP (`{"mcp__pal__thinkdeep": 1}`) → 1 row `is_mcp=true`;
  (d) mixed — rows sum correctly, `call_seq` monotonic across the
  step, `agent_name` denormalised from the event.
  Verify: `PYTHONPATH=config/scripts python3 -m pytest
  config/scripts/tests/test_upsert_tool_calls.py -v` passes.

- [x] T-2: Create `config/scripts/orchestrator_next/cost_report.py`
  with `aggregate_feature()`, `aggregate_by_scope()`,
  `aggregate_repo()`, `render_markdown_feature()`,
  `render_markdown_scoped()`, `render_markdown_repo()`, `render_json()`,
  and `_load_agent_tools()`. All SQL parameterised. Deterministic
  ordering per design.md. Anomaly detector reads
  `$ORCHESTRATOR_HOME/agents/<agent>.md` with `~/.claude/agents/`
  fallback and parses YAML frontmatter `tools:` list.
  Verify: `PYTHONPATH=config/scripts python3 -c "from
  orchestrator_next.cost_report import aggregate_feature,
  aggregate_by_scope, aggregate_repo, render_markdown_feature,
  render_markdown_scoped, render_markdown_repo, render_json,
  _load_agent_tools; print('ok')"` prints `ok`.

- [x] T-3: Wire `cost` subcommand into `bin/orchestrator`. Flags:
  `--change-id <cid>`, `--repo`, `--by <step|agent|tool|feature>`,
  `--format md|json` (default `md`), `--since <ISO>`. `--change-id`
  and `--repo` are mutually exclusive; one is required. Slug-guard
  `--change-id`. Resolve DuckDB path from `METRICS_DB` else
  `$ORCHESTRATOR_HOME/metrics.duckdb`. Exit 0 on success, 3 on any
  error (missing DB, zero rows, slug violation, bad flag combo).
  Keep `_usage()` in sync. Add subprocess tests in
  `config/scripts/tests/test_cost_cli.py` exercising: feature default,
  `--by step|agent|tool`, `--repo`, `--repo --by feature|agent|tool`,
  `--format json` parseability, slug-guard rejection (AC-10),
  byte-identical repeated runs (AC-9).
  Verify: `PYTHONPATH=config/scripts python3 -m pytest
  config/scripts/tests/test_cost_cli.py -v` passes.

- [x] T-4: Add the skill addendum to the orchestrate `SKILL.md`
  (path resolved during the task; the skill file is the one the
  `/orchestrate` command loads). In the `complete_workflow` action
  prose, add one paragraph instructing the skill to shell out to
  `orchestrator cost --change-id <cid>` and include stdout verbatim
  in its final user-facing message. No archive side-effect; no file
  committed. Keep wording tight (≤6 sentences).
  Verify: `grep -n 'orchestrator cost --change-id' <SKILL.md path>`
  returns a line inside the `complete_workflow` section
  (context-grep with `-B 20` shows the section header above the hit).

- [x] T-5: Integration tests in
  `config/scripts/tests/test_cost_report_integration.py` that seed
  a tempfile DuckDB with `step_events` + `tool_calls` rows spanning
  two `change_id`s sharing the same `basename(repo_root)`, plus a
  fake agent frontmatter file in a tempdir used as
  `ORCHESTRATOR_HOME`. Assertions:
  (a) feature-level report contains all eight section headings in
  order (AC-2);
  (b) `--by step|agent|tool` each produce a single table with no
  extra sections (AC-3);
  (c) `--repo` lists both features; `--repo --by agent` aggregates
  across both (AC-4);
  (d) `--format json` parses and has the eight documented top-level
  keys (AC-5);
  (e) native vs MCP split is correct (AC-6);
  (f) anomaly row emitted for (developer, Bash) when frontmatter
  only declares `["Read", "Edit"]`; no row when agent file absent
  (AC-7).
  Verify: `PYTHONPATH=config/scripts python3 -m pytest
  config/scripts/tests/test_cost_report_integration.py -v` passes.
