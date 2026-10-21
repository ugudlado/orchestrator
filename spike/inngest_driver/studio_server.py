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
ORCH_STUDIO_REPOS (comma-separated repo roots a run may target, default
/home/orch/toy), ORCH_STUDIO_DIST (built UI, default <repo>/studio/dist),
ORCH_STUDIO_PORT (default 8790), ORCH_STUDIO_HOSTS (extra Host header values
to accept, comma-separated).

API (JSON; POSTs need `X-Studio: 1`, which a cross-site form can't send):
    GET  /api/health, /api/workflows, /api/runs, /api/runs/<slug>/artifacts/<path>
    POST /api/runs                {workflow, slug, ticket, repo}
    POST /api/runs/<slug>/gate    {gate, attempt, decision, note?, reset_to?}
    POST /api/runs/<slug>/answer  {step_id, wait, answer, reset_to?}
"""

from __future__ import annotations

import datetime
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
API = os.environ.get("INNGEST_API_BASE_URL", "http://127.0.0.1:8288").rstrip("/")
UI_URL = os.environ.get("INNGEST_UI_URL", "http://localhost:8288").rstrip("/")
SIGNING_KEY = os.environ.get("INNGEST_SIGNING_KEY", "")
EVENT_KEY = os.environ.get("INNGEST_EVENT_KEY", "")
RUNS_ROOT = Path(os.environ.get("ORCH_RUNS_ROOT", "/home/orch/runs")).resolve()
PACK = Path(os.environ.get("ORCH_PACK", "/home/orch/workflows/config"))
REPOS = [r for r in os.environ.get("ORCH_STUDIO_REPOS", "/home/orch/toy").split(",") if r]
DIST = Path(os.environ.get("ORCH_STUDIO_DIST", str(HERE.parents[1] / "studio" / "dist")))
PORT = int(os.environ.get("ORCH_STUDIO_PORT", "8790"))
HOSTS = {"localhost", "127.0.0.1", *(h for h in os.environ.get("ORCH_STUDIO_HOSTS", "").split(",") if h)}
LOOKBACK = datetime.timedelta(days=30)
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
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def send_event(name: str, data: dict) -> None:
    req = urllib.request.Request(
        f"{API}/e/{EVENT_KEY}",
        data=json.dumps({"name": name, "data": data}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def _events(name: str) -> list[dict]:
    """Recent events of one name, newest first. The list endpoint returns
    nothing without received_after; 100 per name is plenty for one box."""
    since = (datetime.datetime.now(datetime.UTC) - LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _inngest("/v1/events", {"name": name, "received_after": since, "limit": 100}).get("data") or []


_run_cache: dict[str, dict] = {}  # event id -> finished run (never changes again)


def _run_for_event(event_id: str) -> dict | None:
    if event_id in _run_cache:
        return _run_cache[event_id]
    runs = _inngest(f"/v1/events/{event_id}/runs").get("data") or []
    if not runs:
        return None
    run = runs[0]
    if run.get("status") in ("Completed", "Failed", "Cancelled"):
        # The per-event list has no output (and ended_at stays null); the run itself has both.
        try:
            run = {**run, **(_inngest(f"/v1/runs/{run['run_id']}").get("data") or {})}
        except urllib.error.URLError:
            return run
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
        e["stdout_tail"] = out.read_text(errors="replace")[-TAIL:]
    return e


def _artifacts(d: Path) -> list[dict]:
    if not (d.is_dir() and _under_root(d)):
        return []
    files = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and _under_root(p):
            st = p.stat()
            mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.UTC).isoformat()
            files.append({"name": str(p.relative_to(d)), "size": st.st_size, "mtime": mtime})
    return files[:200]


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
    keys = ("kind", "step_id", "attempt", "wait", "show", "question")
    return {**{k: a[k] for k in keys if a.get(k) is not None}, "at": at}


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
            status = "running"  # queued: the event is in, the run hasn't started
        ticket = str(d.get("ticket") or "")
        out.append(
            {
                "slug": slug,
                "workflow": d.get("workflow"),
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
    return out


# --- workflows -------------------------------------------------------------


def _summary(step_dir: Path, step_id: str, kind: str) -> str | None:
    """One plain sentence for a step: its SKILL.md description up to "Use when"."""
    skill = step_dir / "SKILL.md"
    if skill.is_file():
        m = re.match(r"---\n(.*?)\n---", skill.read_text(errors="replace"), re.S)
        try:
            desc = (yaml.safe_load(m.group(1)) or {}).get("description") if m else None
        except yaml.YAMLError:  # some frontmatter isn't strict YAML; take the line as written
            line = re.search(r'^description:\s*"?(.*?)"?\s*$', m.group(1), re.M) if m else None
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

_lock = threading.Lock()
_runs_cache: tuple[float, list] = (0.0, [])


def cached_runs() -> list[dict]:
    """Studio polls every few seconds; one Inngest round per 2 s is enough for every tab."""
    global _runs_cache
    with _lock:
        if time.monotonic() - _runs_cache[0] > 2:
            _runs_cache = (time.monotonic(), list_runs())
        return _runs_cache[1]


class BadRequest(Exception):
    pass


def _need(body: dict, *keys: str) -> None:
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        raise BadRequest(f"missing {', '.join(missing)}")


def _run(slug: str) -> dict:
    run = next((r for r in cached_runs() if r["slug"] == slug), None)
    if not run:
        raise BadRequest(f"no run {slug}")
    return run


def start_run(body: dict) -> dict:
    _need(body, "workflow", "slug", "ticket", "repo")
    slug, wf, repo = body["slug"], body["workflow"], body["repo"]
    if not SLUG_RE.match(slug):
        raise BadRequest("slug: letters, digits and - only")
    if wf not in workflows():
        raise BadRequest(f"unknown workflow {wf}")
    if repo not in REPOS:
        raise BadRequest(f"repo must be one of {', '.join(REPOS)}")
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
    _need(body, "gate", "attempt", "decision")
    if body["decision"] not in ("approved", "rejected"):
        raise BadRequest("decision must be approved or rejected")
    a = _run(slug).get("attention") or {}
    if a.get("kind") != "gate" or a.get("step_id") != body["gate"] or a.get("attempt") != body["attempt"]:
        raise BadRequest("this gate is no longer waiting; reload to see where the run is")
    data = {"slug": slug, "gate": body["gate"], "attempt": body["attempt"], "decision": body["decision"], "by": "studio"}
    if body["decision"] == "rejected":
        data["note"] = str(body.get("note") or "")
        if body.get("reset_to"):
            data["reset_to"] = str(body["reset_to"])
    send_event("orchestrator/gate.decided", data)
    return {"ok": True}


def answer(slug: str, body: dict) -> dict:
    _need(body, "step_id", "wait")
    if not (body.get("answer") or body.get("reset_to")):
        raise BadRequest("send an answer or a step to go back to")
    a = _run(slug).get("attention") or {}
    if a.get("kind") != "question" or a.get("step_id") != body["step_id"] or a.get("wait") != body["wait"]:
        raise BadRequest("this question is no longer open; reload to see where the run is")
    data = {"slug": slug, "step_id": body["step_id"], "wait": body["wait"], "answer": str(body.get("answer") or ""), "by": "studio"}
    if body.get("reset_to"):
        data["reset_to"] = str(body["reset_to"])
    send_event("orchestrator/input.provided", data)
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "orch-studio"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        except urllib.error.URLError as e:
            self._json(502, {"error": f"Inngest unreachable: {e}"})

    def do_GET(self) -> None:
        self._guard(self._get)

    def do_POST(self) -> None:
        self._guard(self._post)

    def _get(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            try:
                _inngest("/v1/events", {"limit": 1})
                ok = True
            except urllib.error.URLError:
                ok = False
            self._json(200, {"ok": True, "inngest": ok, "ui_url": UI_URL, "repos": REPOS, "workflows": sorted(workflows())})
        elif path == "/api/workflows":
            self._json(200, workflows())
        elif path == "/api/runs":
            self._json(200, {"runs": cached_runs()})
        elif m := re.match(r"^/api/runs/([^/]+)/artifacts/(.+)$", path):
            slug, rel = m.group(1), urllib.parse.unquote(m.group(2))
            art = next((r for r in cached_runs() if r["slug"] == slug), None)
            # Only files the run listing already exposed, so no path is read on request alone.
            if not art or rel not in {a["name"] for a in art["artifacts"]}:
                self._json(404, {"error": "no such artifact"})
                return
            p = RUNS_ROOT / slug / "artifacts" / rel
            if not (_under_root(p) and p.is_file()):
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
        elif m := re.match(r"^/api/runs/([^/]+)/(gate|answer)$", path):
            res = (decide_gate if m.group(2) == "gate" else answer)(m.group(1), body)
        else:
            self._json(404, {"error": "not found"})
            return
        global _runs_cache
        _runs_cache = (0.0, [])  # the next poll shows the new state
        self._json(200, res)

    def log_message(self, fmt: str, *args) -> None:
        if not self.path.startswith("/api/runs") or self.command == "POST":
            super().log_message(fmt, *args)


def main() -> None:
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
