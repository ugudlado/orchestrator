"""Studio backend for the Inngest driver: serves the built Studio UI and is the
only thing that talks to Inngest, whose API has no login.

    python -m spike.inngest_driver.studio_server

Binds 127.0.0.1 only; reach it over an SSH tunnel. Runs are read from disk
(ORCH_RUNS_ROOT/<slug>/{state.yaml,artifacts,results}) plus Inngest's per-run
status. Inngest's run list proved unreliable, so runs are found from their
`orchestrator/run.requested` events instead. "Waiting on you" comes from the
driver's `orchestrator/attention` events. Approve, reject and answer send the
same events as slack_bridge.py, so a click in either place decides the gate.

Env: INNGEST_SIGNING_KEY, INNGEST_EVENT_KEY, INNGEST_API_BASE_URL (default
http://127.0.0.1:8288), INNGEST_UI_URL (where your browser reaches the Inngest
dashboard, default http://localhost:8288), ORCH_RUNS_ROOT (default
/home/orch/runs), ORCH_PACK (default /home/orch/workflows/config),
ORCH_STUDIO_REPOS (comma-separated project folders a run may target, default
/home/orch/toy), ORCH_PROJECTS_ROOT (optional: each folder under it with a .git or
.mcp.json is a project too), ORCH_STUDIO_DIST (built UI, default <repo>/studio/dist),
ORCH_STUDIO_PORT (default 8790), ORCH_STUDIO_HOSTS (extra Host header values
to accept, comma-separated).

API (JSON; POSTs need `X-Studio: 1`, which a cross-site form can't send):
    GET  /api/health (includes projects and their MCP servers), /api/workflows, /api/runs, /api/runs/<slug>/artifacts/<path>
    POST /api/runs                {workflow, slug, ticket, repo}
    GET  /api/runs/<slug>/comments
    POST /api/runs/<slug>/comments               {text, file?, quote?, line?}
    POST /api/runs/<slug>/comments/<id>/delete   (pending comments only)
    POST /api/runs/<slug>/gate    {gate, attempt, decision, note?, reset_to?, include_comments?}
    POST /api/projects/check      {path}   (re-check a project's MCP servers now)
    POST /api/runs/<slug>/answer  {step_id, wait, answer?, reset_to?, include_comments?}

Comments (on a whole document, or on a highlighted passage of one) are kept in
<runs>/<slug>/studio-comments.json until sent. With include_comments, a
rejection or an answer carries every pending comment in its note or answer
text, which is what the agent reads, and the comments are marked sent.
"""

from __future__ import annotations

import datetime
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from .claude_worker import step_env

HERE = Path(__file__).resolve().parent
API = os.environ.get("INNGEST_API_BASE_URL", "http://127.0.0.1:8288").rstrip("/")
UI_URL = os.environ.get("INNGEST_UI_URL", "http://localhost:8288").rstrip("/")
SIGNING_KEY = os.environ.get("INNGEST_SIGNING_KEY", "")
EVENT_KEY = os.environ.get("INNGEST_EVENT_KEY", "")
RUNS_ROOT = Path(os.environ.get("ORCH_RUNS_ROOT", "/home/orch/runs")).resolve()
PACK = Path(os.environ.get("ORCH_PACK", "/home/orch/workflows/config"))
REPOS = [r for r in os.environ.get("ORCH_STUDIO_REPOS", "/home/orch/toy").split(",") if r]
PROJECTS_ROOT = os.environ.get("ORCH_PROJECTS_ROOT", "")
DIST = Path(os.environ.get("ORCH_STUDIO_DIST", str(HERE.parents[1] / "studio" / "dist")))
PORT = int(os.environ.get("ORCH_STUDIO_PORT", "8790"))
HOSTS = {"localhost", "127.0.0.1", "::1", *(h for h in os.environ.get("ORCH_STUDIO_HOSTS", "").split(",") if h)}
LOOKBACK = datetime.timedelta(days=30)
TIMEOUT = 5  # seconds per Inngest call
FINISHED = ("Completed", "Failed", "Cancelled")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
TAIL = 1500  # characters of an exec step's stdout shown per call

# Plain-language lines for exec steps, which have no SKILL.md to describe them.
EXEC_SUMMARY = {
    "check-rerun": "Checks this ticket hasn't already been finished.",
    "create-worktree": "Creates a separate branch and working copy for the change.",
    "mark-change-completed": "Records the change as complete.",
    "merge-to-main": "Merges the branch into main.",
    "persist-learnings": "Saves what this run learned back into the workflow pack.",
    "remove-worktree": "Removes the working copy once the pull request has merged.",
    "workflow-report": "Writes a short report of the whole run.",
}


# --- Inngest ---------------------------------------------------------------


def _inngest(path: str, params: dict | None = None) -> dict:
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {SIGNING_KEY}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def send_event(name: str, data: dict) -> None:
    req = urllib.request.Request(
        f"{API}/e/{EVENT_KEY}",
        data=json.dumps({"name": name, "data": data}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read()


def _events(name: str, pages: int = 20) -> list[dict]:
    """Events of one name from the last LOOKBACK, newest first. The list endpoint
    returns nothing without received_after, and pages with `cursor` (the last
    event's internal_id)."""
    since = (datetime.datetime.now(datetime.UTC) - LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    params: dict = {"name": name, "received_after": since, "limit": 100}
    out: list[dict] = []
    for _ in range(pages):
        page = _inngest("/v1/events", params).get("data") or []
        out += page
        if len(page) < 100:
            break
        params["cursor"] = page[-1]["internal_id"]
    return out


_run_cache: dict[str, dict] = {}  # event id -> finished run (never changes again)


def _run_for_event(event_id: str) -> dict | None:
    if event_id in _run_cache:
        return _run_cache[event_id]
    runs = _inngest(f"/v1/events/{event_id}/runs").get("data") or []
    if not runs:
        return None
    run = runs[0]
    if run.get("status") in FINISHED:
        # The per-event list has no output (and ended_at stays null); the run itself has
        # both. The two can disagree for a while (the list says Completed, the run still
        # Running), so cache only once the run itself says it finished.
        try:
            run = {**run, **(_inngest(f"/v1/runs/{run['run_id']}").get("data") or {})}
        except (OSError, ValueError):
            return run
        if run.get("status") in FINISHED:
            _run_cache[event_id] = run
    return run


# --- runs on disk ----------------------------------------------------------


def _under_root(p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(RUNS_ROOT)
    except OSError:
        return False


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _enrich(entry: dict, results: Path) -> dict:
    """Add what a step recorded: a judgment step's outputs and summary, an exec
    step's last output lines."""
    e = dict(entry)
    stem = f"{e['step']}-{e['attempt']}"
    res = _read_json(results / f"{stem}.json")
    if res:
        e["summary"] = res.get("summary")
        e["outs"] = {k: v for k, v in res.items() if k not in ("status", "summary")}
    out = results / f"{stem}.stdout"
    if out.is_file():
        with out.open("rb") as f:
            f.seek(max(0, out.stat().st_size - TAIL * 4))  # a few bytes per character at most
            e["stdout_tail"] = f.read().decode(errors="replace")[-TAIL:]
    return e


MAX_FILES = 500


def _artifacts(d: Path) -> list[dict]:
    """The run's files, top level first (that's where step outputs land), so a tree
    an agent dumps into a subfolder can't push design.md off the list."""
    if not (d.is_dir() and _under_root(d)):
        return []
    files = []
    for root, dirs, names in os.walk(d):
        dirs.sort()
        for n in sorted(names):
            p = Path(root) / n
            if p.is_file() and _under_root(p):
                st = p.stat()
                mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.UTC).isoformat()
                files.append({"name": str(p.relative_to(d)), "size": st.st_size, "mtime": mtime})
        if len(files) >= MAX_FILES:
            break
    return files[:MAX_FILES]


def _waiting(slug: str, attention: list[dict], decided: list[dict], answered: list[dict]) -> dict | None:
    """The newest gate or question for this run that nobody has answered yet."""
    mine = [a for a in attention if a["data"].get("slug") == slug]
    if not mine or mine[0]["data"].get("kind") not in ("gate", "question"):
        return None
    a, at = mine[0]["data"], mine[0]["received_at"]
    if a["kind"] == "gate":
        done = any(
            e["data"].get("slug") == slug
            and e["data"].get("gate") == a.get("step_id")
            and e["data"].get("attempt") == a.get("attempt")
            and e["received_at"] >= at
            for e in decided
        )
    else:
        done = any(
            e["data"].get("slug") == slug
            and e["data"].get("step_id") == a.get("step_id")
            and e["data"].get("wait") == a.get("wait")
            and e["received_at"] >= at
            for e in answered
        )
    if done:
        return None
    keys = ("kind", "step_id", "attempt", "wait", "show", "question", "reason")
    out = {**{k: a[k] for k in keys if a.get(k) is not None}, "at": at}
    if out.get("show") and a.get("artifacts_dir"):
        # The driver names shown files by absolute path; Studio names them relative to the artifacts dir.
        art = Path(a["artifacts_dir"])
        out["show"] = {
            n: str(Path(p).relative_to(art)) if Path(p).is_relative_to(art) else p for n, p in out["show"].items()
        }
    return out


_art_dirs: dict[str, Path | None] = {}  # slug -> its artifacts dir (from its run.requested event)
_finished: dict[str, dict] = {}  # event id -> listing of a finished run (its files no longer change)


def list_runs() -> list[dict]:
    requested = _events("orchestrator/run.requested")
    attention = _events("orchestrator/attention")
    decided = _events("orchestrator/gate.decided")
    answered = _events("orchestrator/input.provided")
    wfs = workflows()
    seen, out = set(), []
    for ev in requested:  # newest first: a re-run of a slug shadows older ones
        d = ev.get("data") or {}
        slug = d.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if ev["id"] in _finished:
            out.append(_finished[ev["id"]])
            continue
        art = Path(d.get("artifacts_dir") or RUNS_ROOT / slug / "artifacts")
        base = art.parent if _under_root(art) else None
        run = _run_for_event(ev["id"]) or {}
        istatus = run.get("status")
        output = run.get("output") if isinstance(run.get("output"), dict) else {}
        state = _read_json(base / "state.yaml") if base else {}
        history = output.get("history") or state.get("step_history") or []
        results = base / "results" if base else Path("/nonexistent")
        history = [_enrich(h, results) for h in history]
        wf = wfs.get(d.get("workflow") or "", {"steps": []})
        kinds = {s["id"]: s["kind"] for s in wf["steps"]}
        current = None
        attn = None
        if istatus == "Running":
            locks = sorted(results.glob("*.lock"), key=lambda p: p.stat().st_mtime) if results.is_dir() else []
            if locks:
                step = locks[-1].stem
                current = {"step": step, "kind": kinds.get(step, "judgment")}
            else:
                attn = _waiting(slug, attention, decided, answered)
        if istatus == "Running":
            status = {"gate": "waiting", "question": "needs_you"}.get((attn or {}).get("kind"), "running")
        elif istatus == "Completed":
            status = "done" if output.get("status") == "done" else "failed"
        elif istatus in ("Failed", "Cancelled"):
            status = "failed"
        else:
            status = "queued"  # the event is in, but no driver has picked the run up yet
        ticket = str(d.get("ticket") or "")
        out.append(
            {
                "slug": slug,
                "workflow": d.get("workflow"),
                "repo": d.get("repo_root"),
                "title": (ticket.strip().splitlines() or [slug])[0][:120],
                "ticket": ticket,
                "status": status,
                "outcome": output.get("status") if istatus != "Running" else None,
                "started_at": run.get("run_started_at") or ev.get("received_at"),
                "ended_at": run.get("ended_at"),
                "inngest": {
                    "run_id": run.get("run_id"),
                    "status": istatus,
                    "trace_url": f"{UI_URL}/run?runID={run['run_id']}" if run.get("run_id") else None,
                },
                "history": history,
                "current": current,
                "attention": attn,
                "tokens": state.get("tokens") or [],
                "artifacts": _artifacts(art) if base else [],
                "cost_usd": round(sum(h.get("cost_usd") or 0 for h in history), 4),
            }
        )
        _art_dirs[slug] = art if base else None
        if istatus in FINISHED and ev["id"] in _run_cache:
            _finished[ev["id"]] = out[-1]
    return out


# --- workflows -------------------------------------------------------------


def _summary(step_dir: Path, step_id: str, kind: str) -> str | None:
    """One plain sentence for a step: its SKILL.md description up to "Use when"."""
    skill = step_dir / "SKILL.md"
    if skill.is_file():
        m = re.match(r"---\n(.*?)\n---", skill.read_text(errors="replace"), re.DOTALL)
        try:
            desc = (yaml.safe_load(m.group(1)) or {}).get("description") if m else None
        except yaml.YAMLError:  # some frontmatter isn't strict YAML; take the line as written
            line = re.search(r'^description:\s*"?(.*?)"?\s*$', m.group(1), re.MULTILINE) if m else None
            desc = line.group(1) if line else None
        if desc:
            return re.split(r"\s+Use (?:when|at|after|before)\b", desc)[0].strip()
    return EXEC_SUMMARY.get(step_id) if kind == "exec" else None


def _workflow(path: Path) -> dict:
    d = yaml.safe_load(path.read_text())
    steps = []
    for e in d["steps"]:
        e = {"id": e} if isinstance(e, str) else e
        if "gate" in e:
            show = e.get("show") or []
            s = {
                "id": e["gate"],
                "kind": "gate",
                "show": show,
                "approve_as": e.get("approve_as"),
                "summary": f"You review {', '.join(show) or 'the run'} and approve or send it back.",
            }
            if e.get("routes"):
                s["routes"] = e["routes"]
            steps.append(s)
            continue
        sd = PACK / "steps" / e["id"]
        c = yaml.safe_load((sd / "contract.yaml").read_text())
        ins = {k: v["artifact"] for k, v in (c.get("in") or {}).items() if isinstance(v, dict) and "artifact" in v}
        outs, enums, fail = {}, {}, {}
        for k, v in (c.get("out") or {}).items():
            if "artifact" in v:
                outs[k] = v["artifact"]
            elif v.get("type") == "enum":
                outs[k], enums[k] = "enum", v["values"]
                if v.get("fail_on"):
                    fail[k] = v["fail_on"]
            else:
                outs[k] = v.get("type", "string")
        s = {
            "id": e["id"],
            "kind": c["kind"],
            "on_failure": e.get("on_failure"),
            "requires": e.get("requires"),
            "max_retries": e.get("max_retries"),
            "max_turns": c.get("max_turns"),
            "tools": c.get("tools", []),
            "in": ins,
            "out": outs,
            "enums": enums,
            "fail_on": fail,
            "summary": _summary(sd, e["id"], c["kind"]),
        }
        if c.get("skip_when_unchanged"):
            s["skip_when_unchanged"] = True
        if e.get("routes"):
            s["routes"] = e["routes"]
        steps.append(s)
    return {"name": d["name"], "version": d.get("version", 1), "inputs": d.get("inputs") or {}, "steps": steps}


_wf_cache: tuple[float, dict] = (0.0, {})


def workflows() -> dict:
    global _wf_cache
    files = sorted((PACK / "workflows").glob("*.yaml"))
    stamp = max((f.stat().st_mtime for f in files), default=0.0)
    if stamp != _wf_cache[0]:
        _wf_cache = (stamp, {w["name"]: w for w in map(_workflow, files)})
    return _wf_cache[1]


# --- HTTP ------------------------------------------------------------------

log = logging.getLogger("studio")
_refresh_lock = threading.Lock()
# What requests read. A background thread refreshes it every 2 s, so a slow or
# hung Inngest never holds up a request; when a refresh fails, the last good list
# stays, marked stale, and `error` says why.
_snap: dict = {"runs": [], "ok": False, "error": "not loaded yet", "at": 0.0}


def refresh(quiet: bool = False) -> list[dict]:
    """Re-read every run. On failure the last good list stays; `quiet` (the poller)
    logs instead of raising."""
    with _refresh_lock:
        try:
            runs = list_runs()
        except Exception as e:
            _snap.update(ok=False, error=f"{type(e).__name__}: {e}")
            if not quiet:
                raise
            log.warning("refresh failed", exc_info=True)
            return _snap["runs"]
        _snap.update(runs=runs, ok=True, error=None, at=time.monotonic())
        return runs


def _poll() -> None:
    while True:
        refresh(quiet=True)
        time.sleep(2)


def cached_runs() -> list[dict]:
    return _snap["runs"]


class BadRequest(Exception):
    pass


class AlreadySent(BadRequest):
    pass


def _need(body: dict, *keys: str) -> None:
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        raise BadRequest(f"missing {', '.join(missing)}")


# Decisions this server sent in the last minute. Inngest's event list can lag a
# send by a moment, so a second click would otherwise pass the "still open" check.
_recent: dict[tuple, float] = {}


def _once(key: tuple) -> None:
    now = time.monotonic()
    for k in [k for k, t in _recent.items() if now - t > 60]:
        del _recent[k]
    if key in _recent:
        raise AlreadySent("already answered; the run will move on in a moment")
    _recent[key] = now


def _run(slug: str, fresh: bool = False) -> dict:
    """A run from the listing; `fresh` re-reads Inngest first, so a decision is
    checked against where the run is now, not where it was up to 2 s ago."""
    run = next((r for r in (refresh() if fresh else cached_runs()) if r["slug"] == slug), None)
    if not run:
        raise BadRequest(f"no run {slug}")
    return run


# --- comments --------------------------------------------------------------

_comments_lock = threading.Lock()


def _comments_path(slug: str) -> Path:
    if not SLUG_RE.match(slug):
        raise BadRequest("bad run name")
    _run(slug)  # only runs Studio knows about
    return RUNS_ROOT / slug / "studio-comments.json"


def _load_comments(slug: str) -> list[dict]:
    p = _comments_path(slug)
    return json.loads(p.read_text()) if p.is_file() else []


def _save_comments(slug: str, items: list[dict]) -> None:
    p = _comments_path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    tmp.replace(p)


def add_comment(slug: str, body: dict) -> dict:
    text = str(body.get("text") or "").strip()
    if not text:
        raise BadRequest("a comment needs text")
    c = {
        "id": f"c{time.time_ns()}",
        "file": str(body["file"]) if body.get("file") else None,
        "quote": str(body["quote"])[:2000] if body.get("quote") else None,
        "line": int(body["line"]) if body.get("line") else None,
        "text": text[:5000],
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "sent": None,
    }
    with _comments_lock:
        items = _load_comments(slug)
        items.append(c)
        _save_comments(slug, items)
    return c


def delete_comment(slug: str, cid: str) -> dict:
    with _comments_lock:
        items = _load_comments(slug)
        c = next((c for c in items if c["id"] == cid), None)
        if not c:
            raise BadRequest("no such comment")
        if c["sent"]:
            raise BadRequest("this comment was already sent to the run")
        _save_comments(slug, [x for x in items if x["id"] != cid])
    return {"ok": True}


def _feedback(typed: str, pending: list[dict]) -> str:
    """The text the agent reads: what the person typed, then each comment with
    the file and passage it is about."""
    lines = []
    for c in pending:
        where = c["file"] or "the run"
        if c.get("quote"):
            q = " ".join(c["quote"].split())
            where += f', on "{q[:300]}{"…" if len(q) > 300 else ""}"'
        elif c["file"]:
            where += " (whole document)"
        if c.get("line"):
            where += f" (line {c['line']})"
        lines.append(f"- {where}: {c['text']}")
    parts = [typed.strip()] if typed.strip() else []
    if lines:
        parts.append("Comments on the run's files:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _mark(slug: str, items: list[dict], pending: list[dict], sent_as: dict) -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    for c in pending:
        c["sent"] = {**sent_as, "at": now}
    _save_comments(slug, items)


def _with_comments(slug: str, body: dict, typed: str, send, sent_as: dict) -> None:
    """Send `typed` (plus pending comments when asked) and mark those comments sent,
    holding the lock so a comment added meanwhile is neither lost nor sent twice.
    Inngest refusing the event leaves them pending; a timeout may mean it arrived,
    so they're marked sent but unconfirmed rather than sent a second time."""
    with _comments_lock:
        items = _load_comments(slug) if body.get("include_comments") else []
        pending = [c for c in items if not c["sent"]]
        try:
            send(_feedback(typed, pending))
        except urllib.error.HTTPError:
            raise
        except OSError:
            if pending:
                _mark(slug, items, pending, {**sent_as, "unconfirmed": True})
            raise
        if pending:
            _mark(slug, items, pending, sent_as)


def project_dirs() -> list[str]:
    """Project folders: the configured ones plus each repo (or folder with a .mcp.json) under ORCH_PROJECTS_ROOT."""
    found = [str(Path(r).resolve()) for r in REPOS if Path(r).is_dir()]
    if PROJECTS_ROOT and Path(PROJECTS_ROOT).is_dir():
        for d in sorted(Path(PROJECTS_ROOT).iterdir()):
            if d.is_dir() and not d.name.startswith(".") and ((d / ".git").exists() or (d / ".mcp.json").is_file()):
                found.append(str(d.resolve()))
    return list(dict.fromkeys(found))


def mcp_summary(project: str) -> list[dict]:
    """The servers in a project's .mcp.json: name, kind and what runs (no query strings, no env)."""
    try:
        found = json.loads((Path(project) / ".mcp.json").read_text()).get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for name, srv in found.items():
        if not isinstance(srv, dict):
            continue
        kind = srv.get("type") or ("http" if srv.get("url") else "stdio")
        what = (
            str(srv.get("url", "")).split("?")[0]
            if kind != "stdio"
            else " ".join([srv.get("command", ""), *srv.get("args", [])])
        )
        out.append({"name": name, "type": kind, "what": what[:120]})
    return out


CLAUDE = os.environ.get("ORCH_CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
MCP_CHECK_EVERY = 300  # seconds; a check starts every stdio server and calls every http one
STATUS = re.compile(r"^([A-Za-z0-9_-]+): .*? - ([√✓✔×✗✘⏸!⚠].*)$")
MISSING = re.compile(r"\[([A-Za-z0-9_-]+)\] mcpServers\.\S+: Missing environment variables: ([^\n]+)")
_mcp_status: dict[str, dict] = {}  # project path -> {at, servers: {name: {state, detail}}, error}
_mcp_lock = threading.Lock()


def _state(text: str) -> str:
    t = text.lower()
    if "connected" in t and "fail" not in t:
        return "connected"
    if "auth" in t:
        return "needs_auth"
    if "pending" in t:
        return "pending"
    return "failed"


def _committed(project: str) -> bool:
    """A check starts the project's stdio servers, so only config as committed counts (as in a run)."""
    for rel in (".mcp.json", ".claude/settings.json", ".claude/settings.local.json"):
        head = subprocess.run(["git", "-C", project, "show", f"HEAD:{rel}"], capture_output=True, check=False)
        f = Path(project) / rel
        if (head.stdout if head.returncode == 0 else None) != (f.read_bytes() if f.is_file() else None):
            return False
    return True


def check_mcp(project: str) -> dict:
    """Ask Claude Code itself, as the worker's user, whether each of the project's servers connects.
    The servers are approved for the check the same way a headless run approves them."""
    names = {s["name"] for s in mcp_summary(project)}
    res: dict = {"at": time.time(), "servers": {}, "error": None}
    if names and not _committed(project):
        res["error"] = (
            "Not checked: .mcp.json or .claude settings have uncommitted changes, so their servers aren't trusted yet."
        )
    elif names:
        try:
            out = subprocess.run(
                [CLAUDE, "--settings", '{"enableAllProjectMcpServers": true}', "mcp", "list"],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=90,
                stdin=subprocess.DEVNULL,
                env=step_env(),
                check=False,
            )
            for line in out.stdout.splitlines():
                if (m := STATUS.match(line.strip())) and m.group(1) in names:
                    status = m.group(2)[1:].strip()
                    res["servers"][m.group(1)] = {"state": _state(status), "detail": status[:200]}
            for name, missing in MISSING.findall(out.stdout + out.stderr):
                if name in res["servers"]:
                    res["servers"][name]["missing"] = [v.strip() for v in missing.split(",")]
        except (OSError, subprocess.SubprocessError) as e:
            res["error"] = f"couldn't run claude mcp list: {e}"
    with _mcp_lock:
        _mcp_status[project] = res
    return res


def _mcp_poll() -> None:
    while True:
        for d in project_dirs():
            try:
                check_mcp(d)
            except Exception:
                log.exception("MCP check failed for %s", d)
        time.sleep(MCP_CHECK_EVERY)


def projects() -> list[dict]:
    """What Studio shows per project: its MCP servers (names and missing variables, never values)."""
    return [
        {
            "name": Path(d).name,
            "path": d,
            "mcp": [
                {**srv, **_mcp_status.get(d, {}).get("servers", {}).get(srv["name"], {})} for srv in mcp_summary(d)
            ],
            "mcp_checked_at": _mcp_status.get(d, {}).get("at"),
            "mcp_error": _mcp_status.get(d, {}).get("error"),
            "has_mcp_json": (Path(d) / ".mcp.json").is_file(),
            "has_codex_config": (Path(d) / ".codex" / "config.toml").is_file(),
            "has_bindings": (Path(d) / ".orchestrator" / "bindings.yaml").is_file(),
        }
        for d in project_dirs()
    ]


def start_run(body: dict) -> dict:
    _need(body, "workflow", "slug", "ticket", "repo")
    slug, wf, repo = body["slug"], body["workflow"], body["repo"]
    if not SLUG_RE.match(slug):
        raise BadRequest("slug: letters, digits and - only")
    if wf not in workflows():
        raise BadRequest(f"unknown workflow {wf}")
    dirs = project_dirs()
    repo = str(Path(repo).resolve()) if repo else repo
    if repo not in dirs:
        raise BadRequest(f"project must be one of {', '.join(dirs) or '(none configured)'}")
    if (RUNS_ROOT / slug).exists() or any(r["slug"] == slug for r in cached_runs()):
        raise BadRequest(f"a run named {slug} already exists; pick another name")
    data = {
        "workflow": wf,
        "pack": str(PACK),
        "slug": slug,
        "repo_root": repo,
        "artifacts_dir": str(RUNS_ROOT / slug / "artifacts"),
        "ticket": str(body["ticket"]),
    }
    send_event("orchestrator/run.requested", data)
    return {"ok": True}


def decide_gate(slug: str, body: dict) -> dict:
    key = ("gate", slug, body.get("gate"), body.get("attempt"))
    try:
        return _decide_gate(slug, body, key)
    except AlreadySent:
        raise
    except urllib.error.HTTPError:
        _recent.pop(key, None)  # Inngest refused it: nothing went out, so they can try again
        raise
    except OSError:
        raise  # timed out: it may have arrived, so don't invite a second send
    except Exception:
        _recent.pop(key, None)
        raise


def _decide_gate(slug: str, body: dict, key: tuple) -> dict:
    _need(body, "gate", "attempt", "decision")
    if body["decision"] not in ("approved", "rejected"):
        raise BadRequest("decision must be approved or rejected")
    a = _run(slug, fresh=True).get("attention") or {}
    if a.get("kind") != "gate" or a.get("step_id") != body["gate"] or a.get("attempt") != body["attempt"]:
        raise BadRequest("this gate is no longer waiting; reload to see where the run is")
    _once(key)
    data = {
        "slug": slug,
        "gate": body["gate"],
        "attempt": body["attempt"],
        "decision": body["decision"],
        "by": "studio",
    }
    if body["decision"] == "approved":
        send_event("orchestrator/gate.decided", data)
        # Pending comments don't ride along with an approval (the person was warned);
        # close them so they don't surface in some later, unrelated review.
        with _comments_lock:
            items = _load_comments(slug)
            pending = [c for c in items if not c["sent"]]
            if pending:
                _mark(slug, items, pending, {"via": "approve", "step": body["gate"], "attempt": body["attempt"]})
        return {"ok": True}
    if body.get("reset_to"):
        data["reset_to"] = str(body["reset_to"])

    def send(note: str) -> None:
        if not note:
            raise BadRequest("say what should change, or leave a comment on a file")
        send_event("orchestrator/gate.decided", {**data, "note": note})

    sent_as = {"via": "gate", "step": body["gate"], "attempt": body["attempt"]}
    _with_comments(slug, body, str(body.get("note") or ""), send, sent_as)
    return {"ok": True}


def answer(slug: str, body: dict) -> dict:
    key = ("answer", slug, body.get("step_id"), body.get("wait"))
    try:
        return _answer(slug, body, key)
    except AlreadySent:
        raise
    except urllib.error.HTTPError:
        _recent.pop(key, None)
        raise
    except OSError:
        raise
    except Exception:
        _recent.pop(key, None)
        raise


def _answer(slug: str, body: dict, key: tuple) -> dict:
    _need(body, "step_id", "wait")
    a = _run(slug, fresh=True).get("attention") or {}
    if a.get("kind") != "question" or a.get("step_id") != body["step_id"] or a.get("wait") != body["wait"]:
        raise BadRequest("this question is no longer open; reload to see where the run is")
    _once(key)
    data = {"slug": slug, "step_id": body["step_id"], "wait": body["wait"], "by": "studio"}
    if body.get("reset_to"):
        data["reset_to"] = str(body["reset_to"])

    def send(text: str) -> None:
        if not (text or data.get("reset_to")):
            raise BadRequest("send an answer, a comment, or a step to go back to")
        send_event("orchestrator/input.provided", {**data, "answer": text})

    sent_as = {"via": "answer", "step": body["step_id"], "wait": body["wait"]}
    _with_comments(slug, body, str(body.get("answer") or ""), send, sent_as)
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "orch-studio"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # No framing: a page elsewhere can't overlay Studio to steer a click onto Approve.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _host_ok(self) -> bool:
        # Refuse other Host names, so a page on another site can't reach this
        # server through DNS rebinding.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in HOSTS

    def _guard(self, fn) -> None:
        if not self._host_ok():
            self._json(403, {"error": "unexpected Host header"})
            return
        try:
            fn()
        except BadRequest as e:
            self._json(400, {"error": str(e)})
        except urllib.error.HTTPError as e:
            self._json(502, {"error": f"Inngest answered {e.code}"})
        except (OSError, ValueError) as e:  # unreachable, timed out, or not JSON
            self._json(502, {"error": f"Inngest isn't responding: {e}"})
        except Exception as e:
            log.exception("request failed")
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self) -> None:
        self._guard(self._get)

    def do_POST(self) -> None:
        self._guard(self._post)

    def _get(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            # Answers from the last poll, so it's fast even when Inngest isn't.
            self._json(
                200,
                {
                    "ok": True,
                    "inngest": _snap["ok"],
                    "inngest_error": _snap["error"],
                    "ui_url": UI_URL,
                    "repos": project_dirs(),
                    "projects": projects(),
                    "workflows": sorted(workflows()),
                    "runs_root": str(RUNS_ROOT),
                    "pack": str(PACK),
                },
            )
        elif path == "/api/workflows":
            self._json(200, workflows())
        elif path == "/api/runs":
            if not _snap["ok"] and not _snap["at"]:
                self._json(502, {"error": f"Inngest isn't responding: {_snap['error']}"})
            else:
                self._json(200, {"runs": cached_runs(), "stale": not _snap["ok"], "error": _snap["error"]})
        elif m := re.match(r"^/api/runs/([^/]+)/comments$", path):
            self._json(200, {"comments": _load_comments(m.group(1))})
        elif m := re.match(r"^/api/runs/([^/]+)/artifacts/(.+)$", path):
            slug, rel = m.group(1), urllib.parse.unquote(m.group(2))
            art = next((r for r in cached_runs() if r["slug"] == slug), None)
            # Only files the run listing already exposed, so no path is read on request alone.
            if not art or rel not in {a["name"] for a in art["artifacts"]}:
                self._json(404, {"error": "no such artifact"})
                return
            base = _art_dirs.get(slug)
            p = base / rel if base else None
            if not (p and _under_root(p) and p.is_file()):
                self._json(404, {"error": "no such artifact"})
                return
            self._send(200, p.read_bytes(), "text/plain; charset=utf-8")
        else:
            self._static(path)

    def _static(self, path: str) -> None:
        root = DIST.resolve()
        p = (root / path.lstrip("/")).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            p = root / "index.html"  # single-page app
        if not p.is_file():
            self._json(404, {"error": f"Studio isn't built; run npm run build in studio/ ({DIST})"})
            return
        self._send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")

    def _post(self) -> None:
        if self.headers.get("X-Studio") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            self._json(403, {"error": "POSTs need X-Studio: 1 and a JSON body"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            raise BadRequest("body is not JSON") from None
        if not isinstance(body, dict):
            raise BadRequest("body must be an object")
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/runs":
            res = start_run(body)
        elif path == "/api/projects/check":
            dirs = project_dirs()
            if body.get("path") not in dirs:
                raise BadRequest("unknown project")
            check_mcp(body["path"])
            res = {"ok": True}
        elif m := re.match(r"^/api/runs/([^/]+)/comments$", path):
            res = add_comment(m.group(1), body)
        elif m := re.match(r"^/api/runs/([^/]+)/comments/([^/]+)/delete$", path):
            res = delete_comment(m.group(1), m.group(2))
        elif m := re.match(r"^/api/runs/([^/]+)/(gate|answer)$", path):
            res = (decide_gate if m.group(2) == "gate" else answer)(m.group(1), body)
        else:
            self._json(404, {"error": "not found"})
            return
        threading.Thread(target=refresh, kwargs={"quiet": True}, daemon=True).start()  # show the new state soon
        self._json(200, res)

    def log_message(self, fmt: str, *args) -> None:
        if not self.path.startswith("/api/runs") or self.command == "POST":
            super().log_message(fmt, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("runs %s · pack %s · repos %s · Inngest %s", RUNS_ROOT, PACK, ", ".join(project_dirs()) or "(none)", API)
    for what, path in (("pack", PACK / "workflows"), ("built UI", DIST / "index.html"), ("runs dir", RUNS_ROOT)):
        if not path.exists():
            log.warning("%s not found at %s", what, path)
    for repo in REPOS:
        if not Path(repo).is_dir():
            log.warning("repo %s doesn't exist; Studio won't offer it", repo)
    threading.Thread(target=_poll, daemon=True).start()
    threading.Thread(target=_mcp_poll, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
