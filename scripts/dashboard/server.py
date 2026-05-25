"""Live dashboard for orchestrator workflow runs across all registered repos.

Read-only consumer over metrics.duckdb + ~/.workflows/*/state.yaml.
No writes, no auth, single-user localhost use.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any

import duckdb
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "metrics.duckdb"
REGISTRY_PATH = ROOT / "metrics-registry.yaml"
ROUTES_PATH = ROOT / "scripts" / "routes.yaml"
WORKFLOWS_DIR = Path(os.path.expanduser("~/.workflows"))
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Orchestrator Live Dashboard")


def _registered_repos() -> list[str]:
    if not REGISTRY_PATH.exists():
        return []
    data = yaml.safe_load(REGISTRY_PATH.read_text()) or {}
    repos = data.get("repos") or []
    # Drop test pollution
    return [r for r in repos if not r.startswith("/tmp/") and not r.startswith("/private/var/folders/")]


def _db() -> duckdb.DuckDBPyConnection:
    # read_only=True lets the workflow engine keep writing concurrently
    return duckdb.connect(str(DB_PATH), read_only=True)


def _repos_sql_list(repos: list[str]) -> str:
    quoted = ",".join("'" + r.replace("'", "''") + "'" for r in repos)
    return quoted or "''"


def _load_active_states() -> dict[tuple[str, str], dict[str, Any]]:
    """Glob ~/.workflows/*/state.yaml — features that may or may not be in DuckDB yet."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not WORKFLOWS_DIR.exists():
        return out
    for path in WORKFLOWS_DIR.glob("*/state.yaml"):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        repo_root = data.get("repo_root") or ""
        change_id = data.get("change_id") or path.parent.name
        out[(repo_root, change_id)] = {
            "repo_root": repo_root,
            "change_id": change_id,
            "title": data.get("title") or "",
            "status": data.get("status") or "active",
            "phase": data.get("phase") or "",
            "next_step": (data.get("next_step") or {}).get("step_id") or "",
            "linear_ticket": data.get("linear_ticket") or "",
            "state_path": str(path),
        }
    return out


@app.get("/api/fleet")
def get_fleet() -> dict[str, Any]:
    """Return all currently-active features + recent completed history across registered repos."""
    repos = _registered_repos()
    repos_sql = _repos_sql_list(repos)
    active_states = _load_active_states()

    with _db() as con:
        # Per-feature roll-up (one row per (repo_root, change_id))
        rows = con.execute(f"""
            WITH feat AS (
                SELECT
                    repo_root,
                    change_id,
                    MIN(started_at)          AS first_started,
                    MAX(COALESCE(ended_at, started_at)) AS last_activity,
                    SUM(COALESCE(duration_ms, 0))       AS total_duration_ms,
                    SUM(COALESCE(cost_usd, 0))          AS total_cost_usd,
                    SUM(COALESCE(input_tokens, 0))      AS total_input_tokens,
                    SUM(COALESCE(output_tokens, 0))     AS total_output_tokens,
                    SUM(COALESCE(cache_read_input_tokens, 0))     AS total_cache_read,
                    SUM(COALESCE(cache_creation_input_tokens, 0)) AS total_cache_create,
                    COUNT(*)                           AS step_count,
                    SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS in_progress_count,
                    SUM(CASE WHEN status='failed'      THEN 1 ELSE 0 END) AS failed_count
                FROM step_events
                WHERE repo_root IN ({repos_sql})
                GROUP BY repo_root, change_id
            )
            SELECT * FROM feat
            ORDER BY last_activity DESC
            LIMIT 50
        """).fetchall()
        cols = [d[0] for d in con.description]

    features: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        rec = dict(zip(cols, r))
        # Serialize timestamps
        for k in ("first_started", "last_activity"):
            if rec.get(k) is not None:
                rec[k] = rec[k].isoformat()
        rec["title"] = ""
        rec["phase"] = ""
        rec["next_step"] = ""
        rec["linear_ticket"] = ""
        rec["is_active"] = False
        features[(rec["repo_root"], rec["change_id"])] = rec

    # Merge in active state.yaml — overlay live status/phase/title; insert new ones if not in DB
    for key, st in active_states.items():
        if key in features:
            feat = features[key]
            feat["is_active"] = st["status"] not in ("completed", "abandoned", "archived")
            feat["title"] = st["title"]
            feat["phase"] = st["phase"]
            feat["next_step"] = st["next_step"]
            feat["linear_ticket"] = st["linear_ticket"]
            feat["live_status"] = st["status"]
        else:
            features[key] = {
                "repo_root": st["repo_root"],
                "change_id": st["change_id"],
                "first_started": None,
                "last_activity": None,
                "total_duration_ms": 0,
                "total_cost_usd": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read": 0,
                "total_cache_create": 0,
                "step_count": 0,
                "in_progress_count": 0,
                "failed_count": 0,
                "title": st["title"],
                "phase": st["phase"],
                "next_step": st["next_step"],
                "linear_ticket": st["linear_ticket"],
                "is_active": st["status"] not in ("completed", "abandoned", "archived"),
                "live_status": st["status"],
            }

    feat_list = list(features.values())
    # Active first, then by last_activity desc
    def _sort_key(f: dict[str, Any]):
        return (0 if f.get("is_active") else 1, -(_iso_to_ts(f.get("last_activity"))))
    feat_list.sort(key=_sort_key)

    # Cross-fleet totals
    totals = {
        "active_features": sum(1 for f in feat_list if f.get("is_active")),
        "total_features": len(feat_list),
        "total_cost_usd": sum(f["total_cost_usd"] or 0 for f in feat_list),
        "total_input_tokens": sum(f["total_input_tokens"] or 0 for f in feat_list),
        "total_output_tokens": sum(f["total_output_tokens"] or 0 for f in feat_list),
    }

    return {"features": feat_list[:30], "totals": totals, "registered_repos": repos}


def _iso_to_ts(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


@app.get("/api/feature/{repo_name}/{change_id}")
def get_feature(repo_name: str, change_id: str) -> dict[str, Any]:
    """Return per-step breakdown + tool call counts for one feature.

    `repo_name` is the basename of the repo (matched against registry).
    """
    repos = _registered_repos()
    matched = [r for r in repos if Path(r).name == repo_name]
    if not matched:
        raise HTTPException(status_code=404, detail=f"repo {repo_name} not registered")
    repo_root = matched[0]

    with _db() as con:
        step_rows = con.execute("""
            SELECT phase, step_id, attempt, agent_name, status, model,
                   started_at, ended_at, duration_ms,
                   input_tokens, output_tokens,
                   cache_read_input_tokens, cache_creation_input_tokens,
                   cost_usd, tool_calls_json, turns
            FROM step_events
            WHERE repo_root = ? AND change_id = ?
            ORDER BY started_at NULLS LAST, attempt
        """, [repo_root, change_id]).fetchall()
        step_cols = [d[0] for d in con.description]

        # Per-step tool call counts (live, per-call) — for steps still in progress
        tool_rows = con.execute("""
            SELECT phase, step_id, attempt, tool_name, COUNT(*) AS n,
                   MAX(call_seq) AS last_seq, MAX(called_at) AS last_called
            FROM tool_calls
            WHERE repo_root = ? AND change_id = ?
            GROUP BY phase, step_id, attempt, tool_name
            ORDER BY phase, step_id, attempt, last_seq DESC NULLS LAST
        """, [repo_root, change_id]).fetchall()
        tool_cols = [d[0] for d in con.description]

    steps = []
    for r in step_rows:
        s = dict(zip(step_cols, r))
        for k in ("started_at", "ended_at"):
            if s.get(k) is not None:
                s[k] = s[k].isoformat()
        steps.append(s)

    # Group tool calls by (phase, step_id, attempt)
    tools_by_step: dict[tuple, list] = {}
    for r in tool_rows:
        t = dict(zip(tool_cols, r))
        key = (t["phase"], t["step_id"], t["attempt"])
        tools_by_step.setdefault(key, []).append(t)

    for s in steps:
        key = (s["phase"], s["step_id"], s["attempt"])
        s["tool_calls"] = tools_by_step.get(key, [])

    return {"repo_root": repo_root, "change_id": change_id, "steps": steps}


def _repo_slug(repo_root: str) -> str:
    """Mirror jsonl_usage._repo_slug: /Users/spidey/code/orchestrator → -Users-spidey-code-orchestrator."""
    return "-" + repo_root.lstrip("/").replace("/", "-")


def _claude_jsonl_candidates(repo_root: str, since_ts: float) -> list[Path]:
    """Return *.jsonl files under ~/.claude/projects/<slug>/ with mtime ≥ since_ts, newest first."""
    slug_dir = Path(os.path.expanduser("~/.claude/projects")) / _repo_slug(repo_root)
    if not slug_dir.is_dir():
        return []
    candidates = []
    for p in slug_dir.glob("*.jsonl"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= since_ts - 5:  # 5s grace for clock skew
            candidates.append((mtime, p))
    candidates.sort(reverse=True)
    return [p for _, p in candidates]


def _tail_tool_uses(jsonl_path: Path, limit: int = 5) -> list[dict[str, Any]]:
    """Tail the last ~256KB of a JSONL and extract the most recent tool_use entries."""
    try:
        size = jsonl_path.stat().st_size
    except OSError:
        return []
    read_from = max(0, size - 256 * 1024)
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(read_from)
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.split("\n")
    if read_from > 0 and lines:
        lines = lines[1:]  # drop the partial line at the start

    tool_uses: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        ts = obj.get("timestamp")
        msg = obj.get("message") or {}
        for c in msg.get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            inp = c.get("input") or {}
            tool_uses.append({
                "tool_name": c.get("name"),
                "input": inp,
                "summary": _summarize_tool_input(c.get("name"), inp),
                "timestamp": ts,
            })
    return tool_uses[-limit:]


def _summarize_tool_input(tool_name: str | None, inp: dict[str, Any]) -> str:
    """Compress tool args into a one-line label suitable for the UI."""
    if not isinstance(inp, dict):
        return ""
    if tool_name in ("Edit", "Write", "Read", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        return path
    if tool_name == "Bash":
        cmd = inp.get("command") or ""
        return cmd[:200]
    if tool_name == "Grep":
        return f"{inp.get('pattern','')} {('in ' + (inp.get('path') or '.')) if inp.get('path') else ''}".strip()
    if tool_name == "Glob":
        return inp.get("pattern") or ""
    if tool_name == "Task" or tool_name == "Agent":
        return inp.get("description") or inp.get("subagent_type") or ""
    if tool_name == "WebFetch":
        return inp.get("url") or ""
    if tool_name == "TodoWrite":
        todos = inp.get("todos") or []
        return f"{len(todos)} todos"
    # Generic fallback: top-level scalar keys joined
    bits = []
    for k, v in inp.items():
        if isinstance(v, (str, int, float, bool)):
            bits.append(f"{k}={str(v)[:60]}")
        if len(bits) >= 2:
            break
    return ", ".join(bits)[:200]


@app.get("/api/feature/{repo_name}/{change_id}/step/{phase}/{step_id}/{attempt}/activity")
def get_step_activity(repo_name: str, change_id: str, phase: str, step_id: str, attempt: int) -> dict[str, Any]:
    """Return recent tool_use entries for a running step.

    Only returns content for claude-subprocess steps; others get a null `tool_uses`
    list and the caller falls back to tool_calls counts.
    """
    repos = _registered_repos()
    matched = [r for r in repos if Path(r).name == repo_name]
    if not matched:
        raise HTTPException(status_code=404, detail=f"repo {repo_name} not registered")
    repo_root = matched[0]

    with _db() as con:
        # Prefer in_progress row when the writer left both an in_progress and a
        # terminal row for the same (phase, step_id, attempt) — happens when
        # the engine doesn't clean up the live row on completion.
        row = con.execute("""
            SELECT agent_name, status, started_at
            FROM step_events
            WHERE repo_root=? AND change_id=? AND phase=? AND step_id=? AND attempt=?
            ORDER BY (status = 'in_progress') DESC, upserted_at DESC
            LIMIT 1
        """, [repo_root, change_id, phase, step_id, attempt]).fetchone()
    if not row:
        return {"tool_uses": [], "source": "no_step_row"}

    agent_name, status, started_at = row

    # Resolve subprocess from routes.yaml
    subprocess_name = None
    if ROUTES_PATH.exists():
        rdata = yaml.safe_load(ROUTES_PATH.read_text()) or {}
        agent_cfg = (rdata.get("agents") or {}).get(agent_name) or {}
        subprocess_name = agent_cfg.get("subprocess")

    # Live-tail is only meaningful for in-progress steps. For completed/failed
    # steps the final tool_calls_json on the step row is authoritative; trying
    # to tail a session JSONL after the fact will misattribute later activity.
    if status != "in_progress":
        return {"tool_uses": [], "source": f"step_not_running:{status}",
                "agent_name": agent_name, "status": status}

    if subprocess_name != "claude":
        return {"tool_uses": [], "source": f"unsupported_subprocess:{subprocess_name or 'unknown'}",
                "agent_name": agent_name, "status": status}

    if not started_at:
        return {"tool_uses": [], "source": "no_start_time"}

    # A step in_progress for hours is almost certainly a stale row left by a
    # crashed writer, not a live agent. Refuse to live-tail — the JSONL we'd
    # match would be unrelated activity. 30 minutes is generous for any real
    # agent step; tune if real steps regularly take longer.
    import time as _time
    age_seconds = _time.time() - started_at.timestamp()
    if age_seconds > 30 * 60:
        return {"tool_uses": [], "source": f"step_stale:{int(age_seconds)}s",
                "agent_name": agent_name, "status": status}

    since_ts = started_at.timestamp()
    for jsonl in _claude_jsonl_candidates(repo_root, since_ts):
        uses = _tail_tool_uses(jsonl, limit=5)
        if uses:
            return {
                "tool_uses": uses,
                "source": f"jsonl:{jsonl.name}",
                "agent_name": agent_name,
                "status": status,
            }
    return {"tool_uses": [], "source": "no_jsonl_found", "agent_name": agent_name, "status": status}


@app.get("/api/routes")
def get_routes() -> dict[str, Any]:
    """Return agent → {subprocess, model} mapping from scripts/routes.yaml.

    This is the *static* routing config. Per-run overrides
    (e.g. `agent.developer.subprocess=pi`) are not currently recorded
    on step_events, so the UI surfaces this as "configured CLI".
    """
    if not ROUTES_PATH.exists():
        return {"agents": {}, "models": {}}
    data = yaml.safe_load(ROUTES_PATH.read_text()) or {}
    return {
        "agents": data.get("agents") or {},
        "models": data.get("models") or {},
    }


# Static
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
