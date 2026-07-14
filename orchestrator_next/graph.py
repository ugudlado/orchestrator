"""
Mermaid DAG renderer for the orchestrator engine (ORC-63).

`render_workflow_graph(schema_name)` returns a static Mermaid `flowchart TD`
of a workflow schema's step topology — linear chain + conditional routing
edges (on_success / on_failure).

`render_html(mermaid_src, title, step_data)` wraps it into a self-contained
HTML page with status colours, a click-to-inspect sidebar, and Mermaid CDN.

Read-only: no state mutation, no DuckDB.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def _safe_id(node_id: str) -> str:
    """Return a Mermaid-safe node identifier (alphanumerics + underscore)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id) or "node"


def _normalize_steps(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a flat list of step dicts from a schema's top-level steps list."""
    raw_steps = schema.get("steps", [])
    result: list[dict[str, Any]] = []
    for entry in raw_steps:
        if isinstance(entry, str):
            step_id = entry.split(" if ")[0].strip()
            result.append({"id": step_id})
        elif isinstance(entry, dict):
            result.append(entry)
    return result


def _scan_state_dir(
    state_dir: str | Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Single pass over *_state.yaml files.

    Returns (metrics, last_entry): step_id → summed tokens/cost + max attempt,
    and step_id → last step_history entry (highest attempt wins).
    """
    metrics: dict[str, dict[str, Any]] = {}
    best: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(state_dir).glob("*_state.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                state = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(state, dict):
            continue
        for entry in state.get("step_history") or []:
            if not isinstance(entry, dict):
                continue
            step_id = entry.get("step_id") or ""
            if not step_id:
                continue
            attempt = entry.get("attempt") or 1
            usage = entry.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            m = metrics.setdefault(step_id, {"tokens": 0, "cost": 0.0, "attempts": 1})
            m["tokens"] += (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            m["cost"] += float(usage.get("cost_usd") or 0.0)
            m["attempts"] = max(m["attempts"], attempt)
            prev = best.get(step_id)
            if prev is None or (entry.get("attempt") or 0) >= (prev.get("attempt") or 0):
                best[step_id] = entry
    return metrics, best


def _render_schema_graph(
    schema_name: str,
    schema: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Render workflow schema topology; annotate nodes when metrics is non-empty."""
    overlay = bool(metrics)
    steps = _normalize_steps(schema)
    lines = ["flowchart TD", f"  %% workflow: {schema_name}"]

    if not steps:
        lines.append('  empty["(no steps)"]')
        return "\n".join(lines) + "\n", {}

    for step in steps:
        sid = str(step.get("id", ""))
        safe = _safe_id(sid)
        m = metrics.get(sid, {})
        tokens = int(m.get("tokens") or 0)
        cost = float(m.get("cost") or 0.0)
        if overlay and tokens > 0:
            label = f"{sid}\\n{tokens:,} tok · ${cost:.2f}"
        else:
            label = sid
        lines.append(f'  {safe}["{label}"]')

    step_ids = [str(s.get("id", "")) for s in steps]
    for idx, step in enumerate(steps):
        sid = str(step.get("id", ""))
        on_success = step.get("on_success")
        on_failure = step.get("on_failure")
        next_sid = step_ids[idx + 1] if idx + 1 < len(step_ids) else None

        if on_success or on_failure:
            if on_success and on_success != next_sid:
                lines.append(f"  {_safe_id(sid)} -->|success| {_safe_id(on_success)}")
            elif on_success and on_success == next_sid:
                lines.append(f"  {_safe_id(sid)} --> {_safe_id(on_success)}")
            if on_failure:
                lines.append(f"  {_safe_id(sid)} -->|retry| {_safe_id(on_failure)}")
            if not on_success and next_sid:
                lines.append(f"  {_safe_id(sid)} --> {_safe_id(next_sid)}")
        elif next_sid:
            lines.append(f"  {_safe_id(sid)} --> {_safe_id(next_sid)}")

    if overlay:
        lines.append("")
        for step in steps:
            sid = str(step.get("id", ""))
            attempts = int(metrics.get(sid, {}).get("attempts") or 1)
            if attempts > 1:
                lines.append(
                    f"  style {_safe_id(sid)} fill:#f90,stroke:#d29922,color:#111"
                )
        lines.append("")
        for step in steps:
            sid = str(step.get("id", ""))
            lines.append(f"  click {_safe_id(sid)} showStep")

    return "\n".join(lines) + "\n", {}


def _load_wf_schema(schema_name: str) -> dict[str, Any]:
    """Load config/workflows/<name>.yaml or raise FileNotFoundError."""
    from orchestrator_next.paths import config_root

    schema_path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Workflow schema not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def render_workflow_graph(schema_name: str) -> tuple[str, dict[str, Any]]:
    """Render a workflow schema's step topology as a Mermaid `flowchart TD` string.

    Returns (mermaid_src, step_data). step_data is empty for static schemas
    (no live run state).
    """
    return _render_schema_graph(schema_name, _load_wf_schema(schema_name), {})


def render_workflow_graph_with_overlay(
    schema_name: str, state_dir: str | Path
) -> tuple[str, dict[str, Any]]:
    """Render schema topology with per-step token/cost/retry overlay from run state."""
    schema = _load_wf_schema(schema_name)
    metrics, step_data = _scan_state_dir(state_dir)
    mermaid_src, _ = _render_schema_graph(schema_name, schema, metrics)
    return mermaid_src, step_data


def render_html(mermaid_src: str, title: str, step_data: dict[str, Any] | None = None) -> str:
    """Wrap a Mermaid diagram in a self-contained HTML page.

    step_data maps step_id → last step_history entry. When provided, clicking
    a node opens a sidebar with status, agent, tokens, cost, duration, model.
    """
    step_data = step_data or {}
    step_json = json.dumps(step_data)

    # Build legend rows
    legend_items = [
        ("completed",   "#2ea043", "Completed"),
        ("in_progress", "#388bfd", "In progress"),
        ("failed",      "#f85149", "Failed"),
        ("abandoned",   "#d29922", "Abandoned"),
        ("pending",     "#484f58", "Pending"),
    ]
    legend_html = "".join(
        f'<span class="leg-dot" style="background:{c}"></span>{label} &nbsp; '
        for _, c, label in legend_items
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, sans-serif; background: #0d1117; color: #e6edf3;
            margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }}
    header {{ padding: .75rem 1.5rem; border-bottom: 1px solid #21262d;
              display: flex; align-items: center; gap: 1rem; flex-shrink: 0; }}
    header h1 {{ font-size: .9rem; font-weight: 500; color: #8b949e; margin: 0; flex: 1; }}
    .legend {{ display: flex; align-items: center; font-size: .75rem; color: #8b949e; }}
    .leg-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                margin-right: 4px; }}
    .main {{ display: flex; flex: 1; overflow: hidden; }}
    .graph {{ flex: 1; overflow: auto; padding: 1.5rem; }}
    .mermaid svg {{ max-width: 100%; height: auto; }}
    .sidebar {{ width: 300px; background: #161b22; border-left: 1px solid #21262d;
                padding: 1.25rem; overflow-y: auto; flex-shrink: 0;
                display: none; flex-direction: column; gap: .75rem; }}
    .sidebar.open {{ display: flex; }}
    .sidebar h2 {{ font-size: .85rem; font-weight: 600; margin: 0; color: #e6edf3; }}
    .sidebar .close {{ margin-left: auto; background: none; border: none; color: #8b949e;
                       cursor: pointer; font-size: 1rem; padding: 0; line-height: 1; }}
    .sidebar .close:hover {{ color: #e6edf3; }}
    .stat-grid {{ display: grid; grid-template-columns: auto 1fr; gap: .3rem .75rem;
                  font-size: .8rem; }}
    .stat-grid .k {{ color: #8b949e; }}
    .stat-grid .v {{ color: #e6edf3; font-variant-numeric: tabular-nums; }}
    .badge {{ display: inline-block; padding: .15rem .5rem; border-radius: 4px;
              font-size: .75rem; font-weight: 600; }}
    .badge.completed   {{ background: #1a4731; color: #2ea043; border: 1px solid #2ea043; }}
    .badge.in_progress {{ background: #1c2d42; color: #388bfd; border: 1px solid #388bfd; }}
    .badge.failed      {{ background: #3d1c1c; color: #f85149; border: 1px solid #f85149; }}
    .badge.abandoned   {{ background: #2d2010; color: #d29922; border: 1px solid #d29922; }}
    .badge.pending     {{ background: #21262d; color: #8b949e; border: 1px solid #484f58; }}
    .divider {{ border: none; border-top: 1px solid #21262d; margin: .25rem 0; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="legend">{legend_html}</div>
  </header>
  <div class="main">
    <div class="graph">
      <div class="mermaid">
{mermaid_src.strip()}
      </div>
    </div>
    <div class="sidebar" id="sidebar">
      <div style="display:flex;align-items:center;gap:.5rem">
        <h2 id="sb-title">Step</h2>
        <button class="close" onclick="closeSidebar()">✕</button>
      </div>
      <div id="sb-badge"></div>
      <hr class="divider">
      <div class="stat-grid" id="sb-stats"></div>
    </div>
  </div>

  <script>
    const STEP_DATA = {step_json};

    function showStep(id) {{
      // Mermaid passes the safe_id (underscores); map back to original step id
      const stepId = id.replace(/_/g, '-');
      const data = STEP_DATA[stepId] || STEP_DATA[id] || {{}};
      const usage = data.usage || {{}};

      document.getElementById('sb-title').textContent = stepId;

      const status = data.status || 'pending';
      const badge = document.getElementById('sb-badge');
      badge.textContent = '';
      const span = document.createElement('span');
      span.className = 'badge ' + status;
      span.textContent = status;
      badge.appendChild(span);

      const rows = [
        ['Agent',        data.agent    || '—'],
        ['Attempt',      data.attempt  != null ? String(data.attempt) : '—'],
        ['Started',      data.started_at ? data.started_at.replace('T',' ').replace('Z','') : '—'],
        ['Ended',        data.ended_at   ? data.ended_at.replace('T',' ').replace('Z','')   : '—'],
        ['Duration',     fmtDuration(usage.duration_ms)],
        ['Model',        usage.model || '—'],
        ['Turns',        usage.turns  != null ? String(usage.turns)  : '—'],
        ['In tokens',    fmtTokens(usage.input_tokens)],
        ['Out tokens',   fmtTokens(usage.output_tokens)],
        ['Cache read',   fmtTokens(usage.cache_read_input_tokens)],
        ['Cache write',  fmtTokens(usage.cache_creation_input_tokens)],
        ['Cost',         fmtCost(usage.cost_usd)],
      ];

      const grid = document.getElementById('sb-stats');
      grid.textContent = '';
      for (const [k, v] of rows) {{
        const kEl = document.createElement('span');
        kEl.className = 'k';
        kEl.textContent = k;
        const vEl = document.createElement('span');
        vEl.className = 'v';
        vEl.textContent = v;
        grid.appendChild(kEl);
        grid.appendChild(vEl);
      }}

      document.getElementById('sidebar').classList.add('open');
    }}

    function closeSidebar() {{
      document.getElementById('sidebar').classList.remove('open');
    }}

    function fmtDuration(ms) {{
      if (ms == null) return '—';
      if (ms < 1000) return ms + 'ms';
      const s = ms / 1000;
      if (s < 60) return s.toFixed(1) + 's';
      return (s / 60).toFixed(1) + 'm';
    }}

    function fmtTokens(n) {{
      if (n == null) return '—';
      return Number(n).toLocaleString();
    }}

    function fmtCost(usd) {{
      if (usd == null) return '—';
      return '$' + Number(usd).toFixed(4);
    }}
  </script>

  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{
      startOnLoad: true,
      theme: 'dark',
      securityLevel: 'loose',
    }});
  </script>
</body>
</html>
"""
