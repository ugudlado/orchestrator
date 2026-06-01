"""
Mermaid DAG renderer for the orchestrator engine (ORC-63).

`render_graph(state)` returns a Mermaid `flowchart TD` of the current phase's
node graph — one node per `workflow_plan[phase].nodes` entry, coloured by
status, with an edge per effective `depends_on` relationship.

`render_workflow_graph(schema_name)` returns a static Mermaid `flowchart TD`
of a workflow schema's step topology — linear chain + conditional routing
edges (on_success / on_failure).

`render_html(mermaid_src, title, step_data)` wraps either into a self-contained
HTML page with status colours, a click-to-inspect sidebar, and Mermaid CDN.

Read-only: no state mutation, no DuckDB.
"""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

from orchestrator_next.parser import State, phase_nodes
from orchestrator_next import readiness

# Status → Mermaid classDef name
_STATUS_CLASS = {
    "completed":   "st_completed",
    "in_progress": "st_in_progress",
    "failed":      "st_failed",
    "abandoned":   "st_abandoned",
    "pending":     "st_pending",
}

_CLASS_DEFS = """
  classDef st_completed   fill:#1a4731,stroke:#2ea043,color:#e6edf3
  classDef st_in_progress fill:#1c2d42,stroke:#388bfd,color:#e6edf3
  classDef st_failed      fill:#3d1c1c,stroke:#f85149,color:#e6edf3
  classDef st_abandoned   fill:#2d2010,stroke:#d29922,color:#e6edf3
  classDef st_pending     fill:#21262d,stroke:#484f58,color:#8b949e
""".strip()


def _safe_id(node_id: str) -> str:
    """Return a Mermaid-safe node identifier (alphanumerics + underscore)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id) or "node"


def _last_history_by_step(state: State) -> dict[str, dict[str, Any]]:
    """Return the last step_history entry per step_id (highest attempt wins)."""
    best: dict[str, dict[str, Any]] = {}
    for entry in state.step_history:
        sid = entry.step_id
        prev = best.get(sid)
        if prev is None or (entry.attempt or 0) >= (prev.get("attempt") or 0):
            best[sid] = entry.raw
    return best


def render_graph(state: State) -> tuple[str, dict[str, Any]]:
    """Render the current phase's DAG as a Mermaid `flowchart TD` string.

    Returns (mermaid_src, step_data) where step_data maps step_id → last
    step_history entry (usage, agent, timestamps, etc.) for HTML rendering.
    """
    phase = state.phase
    nodes = phase_nodes(state, phase)
    step_data = _last_history_by_step(state)

    lines = ["flowchart TD", f"  %% phase: {phase}"]

    if not nodes:
        lines.append('  empty["(no nodes)"]')
        return "\n".join(lines) + "\n", {}

    # Node declarations with status label and class
    for node in nodes:
        nid = str(node.get("id", ""))
        status = str(node.get("status", "pending"))
        cls = _STATUS_CLASS.get(status, "st_pending")
        lines.append(f'  {_safe_id(nid)}["{nid}\\n{status}"]:::{cls}')

    lines.append("")
    lines.append(_CLASS_DEFS)
    lines.append("")

    # Edges
    for node in nodes:
        nid = str(node.get("id", ""))
        for dep in readiness.effective_depends_on(nodes, nid):
            lines.append(f"  {_safe_id(dep)} --> {_safe_id(nid)}")

    # Click callbacks (node id → safe_id mapping for JS lookup)
    lines.append("")
    for node in nodes:
        nid = str(node.get("id", ""))
        lines.append(f'  click {_safe_id(nid)} showStep')

    return "\n".join(lines) + "\n", step_data


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


def render_workflow_graph(schema_name: str) -> tuple[str, dict[str, Any]]:
    """Render a workflow schema's step topology as a Mermaid `flowchart TD` string.

    Returns (mermaid_src, step_data). step_data is empty for static schemas
    (no live run state).
    """
    from orchestrator_next.paths import config_root

    schema_path = config_root() / "workflows" / f"{schema_name}.yaml"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Workflow schema not found: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = yaml.safe_load(f) or {}

    steps = _normalize_steps(schema)
    lines = ["flowchart TD", f"  %% workflow: {schema_name}"]

    if not steps:
        lines.append('  empty["(no steps)"]')
        return "\n".join(lines) + "\n", {}

    for step in steps:
        sid = str(step.get("id", ""))
        lines.append(f'  {_safe_id(sid)}["{sid}"]')

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

    return "\n".join(lines) + "\n", {}


def _fmt_duration(ms: Any) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    if ms < 1000:
        return f"{ms}ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    return f"{s/60:.1f}m"


def _fmt_tokens(n: Any) -> str:
    if not n:
        return "—"
    n = int(n)
    return f"{n:,}"


def _fmt_cost(usd: Any) -> str:
    if not usd:
        return "—"
    return f"${float(usd):.4f}"


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
