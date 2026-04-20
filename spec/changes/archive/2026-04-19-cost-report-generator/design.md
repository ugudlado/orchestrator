# Design — cost-report-generator

## Context

`step_events` in `metrics.duckdb` already holds every completed step's
tokens, cost, model, duration, agent, and tool-call JSON (see
`config/scripts/orchestrator_next/upsert.py` — DDL in `ensure_schema()`).
`sum_cost_usd()` is the only read helper today.

This feature adds a second grain table, `tool_calls`, a pure-Python
aggregation module, and one CLI subcommand. The complete-phase pipeline
is untouched; the skill prints the report at end-of-workflow.

Simplicity target: one Python module holds all SQL and all rendering.
The CLI is a <60-line adapter. No inline step, no workflow edit.

## Approaches Considered

### A. Single-module aggregator, CLI-only (SELECTED)

- All DuckDB queries and renderers live in `cost_report.py`.
- `bin/orchestrator cost ...` imports `aggregate_*()` + `render_*()`.
- The skill invokes the CLI at `complete_workflow` and prints stdout.

Pros: zero SQL duplication; one source of truth for ordering/formatting;
no workflow wiring to maintain; no archive file to review.

Cons: CLI is now the only entry point — but that matches user intent
(on-demand cost inspection + skill-level print). No downside.

### B. Inline step that writes `cost-report.md` into the archive

Rejected (amendment #1). Committed reports create review churn with no
read-side value; grep-able archives weren't asked for. A skill-side
print is strictly simpler.

### C. Bash-native aggregator using `duckdb -csv`

Rejected. Eight-section markdown rendering in bash is painful; parsing
agent frontmatter for anomaly detection in bash is worse.

**Decision: A.**

## Schema Addition — `tool_calls`

Added to `ensure_schema()` in `upsert.py`:

```sql
CREATE TABLE IF NOT EXISTS tool_calls (
  repo_root     TEXT    NOT NULL,
  change_id     TEXT    NOT NULL,
  phase         TEXT    NOT NULL,
  step_id       TEXT    NOT NULL,
  attempt       INTEGER NOT NULL,
  agent_name    TEXT    NOT NULL,
  tool_name     TEXT    NOT NULL,
  is_mcp        BOOLEAN NOT NULL,
  call_seq      INTEGER NOT NULL,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  cost_usd      DOUBLE,
  duration_ms   INTEGER,
  called_at     TEXT,
  PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, call_seq)
);
```

### Upsert extension

`upsert_step_event()` continues to write one `step_events` row. After
that row is written, if the event's `usage.tools` dict is non-empty,
the function fans it out:

```
for tool_name, count in sorted(usage_tools.items()):
    for i in range(1, count + 1):
        INSERT OR REPLACE INTO tool_calls (...)
        VALUES (
          repo_root, change_id, phase, step_id, attempt,
          agent_name,
          tool_name,
          tool_name.startswith("mcp__"),
          running_call_seq,        -- monotonic across tools in this step
          NULL, NULL, NULL, NULL, NULL
        )
        running_call_seq += 1
```

`call_seq` is monotonic within `(repo_root, change_id, phase, step_id,
attempt)` so retried attempts don't collide. An idempotent re-upsert
for the same step must first `DELETE FROM tool_calls WHERE (...)` keyed
on that tuple to avoid stale rows under retry.

Per-call tokens/cost/duration stay NULL — Claude Code doesn't surface
that grain today. The columns are nullable so we can populate them
later without another migration.

## Component Breakdown

```
config/scripts/orchestrator_next/cost_report.py
├── @dataclass ReportScope        # change_id | repo | scope_by
├── aggregate_feature(db, repo_root, change_id) -> dict
│     - _totals, _per_phase, _per_agent, _per_model
│     - _native_tools, _mcp_calls, _per_agent_tools
│     - _anomalies   (reads agent frontmatter via _load_agent_tools)
├── aggregate_by_scope(db, rr, cid, scope)  # scope: step|agent|tool
├── aggregate_repo(db, repo_basename, since=None, scope=None)
│                                  # scope: feature|agent|tool
├── render_markdown_feature(data) -> str
├── render_markdown_scoped(data, scope) -> str
├── render_markdown_repo(data, scope) -> str
├── render_json(data) -> str
├── _load_agent_tools(agent_name) -> Optional[set[str]]
│     - $ORCHESTRATOR_HOME/agents/<agent>.md then ~/.claude/agents/
│     - yaml.safe_load on frontmatter between '---' fences
│     - return None if file missing or no 'tools:' key
└── _SLUG_RE, _fmt_usd, _fmt_tokens, _fmt_ms   (formatting helpers)

bin/orchestrator
└── new branch: args[0] == "cost"
       parse flags (argparse), validate mutual exclusion,
       resolve db path, dispatch to aggregate_* + render_*,
       print to stdout.
```

No inline script, no workflow edit, no step contract file.

## SQL Sketches

### Per-agent (feature scope)

```sql
SELECT
  se.agent_name,
  SUM(COALESCE(se.gen_ai_usage_cost_usd, 0.0))       AS cost_usd,
  SUM(COALESCE(se.gen_ai_usage_input_tokens, 0))     AS input_tokens,
  SUM(COALESCE(se.gen_ai_usage_output_tokens, 0))    AS output_tokens,
  SUM(COALESCE(se.duration_ms, 0))                   AS duration_ms,
  COUNT(*)                                           AS step_count
FROM step_events se
WHERE se.repo_root = ? AND se.change_id = ?
GROUP BY se.agent_name
ORDER BY se.agent_name ASC;
```

### Native tools table (is_mcp=false)

```sql
SELECT tool_name, COUNT(*) AS calls
FROM tool_calls
WHERE repo_root = ? AND change_id = ? AND is_mcp = false
GROUP BY tool_name
ORDER BY calls DESC, tool_name ASC;
```

### MCP tools table (is_mcp=true) — same shape, `is_mcp = true`.

### Per-agent tool use

```sql
SELECT agent_name, tool_name, COUNT(*) AS calls
FROM tool_calls
WHERE repo_root = ? AND change_id = ?
GROUP BY agent_name, tool_name
ORDER BY agent_name ASC, calls DESC, tool_name ASC;
```

### Repo-level (basename match)

```sql
-- repo default: per-feature totals
SELECT
  change_id,
  SUM(COALESCE(gen_ai_usage_cost_usd, 0.0))  AS cost_usd,
  SUM(COALESCE(gen_ai_usage_input_tokens, 0)) AS input_tokens,
  SUM(COALESCE(gen_ai_usage_output_tokens, 0)) AS output_tokens,
  MIN(started_at) AS first_seen
FROM step_events
WHERE regexp_extract(repo_root, '[^/]+$') = ?
  AND (? IS NULL OR started_at >= ?)
GROUP BY change_id
ORDER BY first_seen ASC, change_id ASC;
```

Anomaly query:

```sql
SELECT agent_name, tool_name, COUNT(*) AS calls
FROM tool_calls
WHERE repo_root = ? AND change_id = ?
GROUP BY agent_name, tool_name;
-- filter in Python using _load_agent_tools(agent)
```

## Anomaly Detection — Frontmatter Parsing

Agent files live at `$ORCHESTRATOR_HOME/agents/<name>.md` (primary;
confirmed path) with fallback `~/.claude/agents/<name>.md`. Every
production agent file examined has valid YAML frontmatter fenced by
`---` lines with a `tools:` key whose value is a JSON-style array of
strings.

Parser (`_load_agent_tools`):

```python
def _load_agent_tools(agent_name: str) -> Optional[set[str]]:
    for root in (os.environ.get("ORCHESTRATOR_HOME", ""),
                 os.path.expanduser("~/.claude")):
        if not root:
            continue
        path = os.path.join(root, "agents", f"{agent_name}.md")
        if not os.path.isfile(path):
            continue
        text = open(path, "r", encoding="utf-8").read()
        # Match the first '---' ... '---' block at the top of the file.
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not m:
            return None
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None
        tools = fm.get("tools")
        if not isinstance(tools, list):
            return None
        return set(str(t) for t in tools)
    return None
```

`yaml` is already a project dependency (used elsewhere in
`orchestrator_next/`); no new requirement. A `None` return means
"frontmatter missing or unparseable" → skip that agent in anomalies.

## Error Handling

| Condition | CLI behaviour |
|---|---|
| DB file missing | exit 3, stderr `metrics.duckdb not found at <path>` |
| `--change-id` with zero matching rows | exit 3, `no events for change_id=<x>` |
| `--repo` with zero matches | exit 3, `no events for repo basename=<x>` |
| Slug-guard violation on `--change-id` | exit 3, slug-guard message; no DB query |
| `--since` combined with `--change-id` | stderr warning, flag ignored |
| `--change-id` and `--repo` both set / both missing | exit 3, usage message |
| DuckDB error mid-query | exit 3, surface exception message |

## Trade-offs

- **Determinism vs richness**: stable ordering means `per_phase` is
  sorted by first-seen `MIN(started_at)`, not by cost. Users who want
  cost-sorted views can pipe `--format json` into `jq`.
- **Per-call token/cost NULLs**: accepted. When Claude Code starts
  emitting per-call usage, the columns are already there.
- **Repo match by basename**: `basename(repo_root) == basename($PWD)`
  is a pragmatic approximation — it conflates forks with the same name.
  Acceptable for dev-machine scope; revisit if we ever host a shared DB.

## Open Questions

- **Per-step `tool_calls` retention across retries**: the primary key
  includes `attempt`, so retried attempts are kept, not overwritten.
  That matches the `step_events` convention. Confirmed during T-1.
- **Skill addendum wording**: finalised during T-4 with the exact
  paragraph written into SKILL.md; no design decision gated on it.
