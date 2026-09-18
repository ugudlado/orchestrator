"""`orchestrator serve` — a local web page over the same protocol verbs the
Claude Code pane uses.

The pane and this page are two front ends on one engine: every route below is a
thin JSON wrapper around a `protocol` function, so the browser sees exactly what
`orchestrator status --json` prints. Nothing here re-derives a metric, a row
order, or a staleness rule — `docs/pane-ux.md` is the screen spec, and
`protocol` is the only place those decisions live.

Security: this binds 127.0.0.1 and refuses any other host unless `--host` is
passed explicitly. There is no authentication beyond that loopback bind — the
page can approve gates and cancel runs, so do not expose it to a network you do
not control. Writes additionally require a same-origin `Origin` header, which
stops a page in another tab from POSTing to the server on your behalf.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from orchestrator_next import protocol

UI_FILE = Path(__file__).parent / "data" / "ui" / "index.html"

#: POST bodies bigger than this are refused — every write here is a small JSON
#: object, so a large body is a mistake or an attack, never a real request.
MAX_BODY = 1 << 20


def _config_payload() -> dict:
    """Effective settings with their sources, plus the doctor's check lines.

    Both halves are the CLI's own: `settings.load()` is what `config show`
    prints, and the doctor checks are the rows of its table, reported as data
    rather than as the formatted string so the page can style them.
    """
    from orchestrator_next import doctor, settings
    from orchestrator_next.paths import ConfigRootError, config_root_with_source

    try:
        cfg = settings.load()
        rows = [
            {"key": spec.dotted, "value": settings._fmt(res.value),
             "source": res.source, "help": spec.help}
            for spec, res in cfg.items()
        ]
        files = [str(p) for p in cfg.files]
    except settings.SettingsError as exc:
        rows, files = [], []
        return {"settings": rows, "files": files, "doctor": [],
                "error": str(exc)}

    try:
        config_root, config_source = config_root_with_source()
        orch_home = config_root.parent
        repo_root = doctor._repo_root_from_env(orch_home)
        checks = [
            doctor.check_config_source(config_source, config_root),
            doctor.check_git_repo(repo_root),
            doctor.check_config_root(config_root),
            doctor.check_workflow_steps_resolve(config_root),
            doctor.check_step_dispatch_kind(config_root),
            doctor.check_settings(),
            doctor.check_run_store(),
        ]
        lines = [{"name": c.name, "status": c.status, "detail": c.detail}
                 for c in checks]
    except (ConfigRootError, OSError, ValueError) as exc:
        lines = [{"name": "config root", "status": "FAIL", "detail": str(exc)}]

    lines.append({"name": "web ui", "status": "PASS",
                  "detail": "served by `orchestrator serve`"})
    return {"settings": rows, "files": files, "doctor": lines}


class Handler(BaseHTTPRequestHandler):
    """One request. Every route is `protocol.<verb>` plus JSON framing."""

    server_version = "orchestrator-serve"
    origin: str = ""  # set per-server by `serve()`; the only accepted Origin

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.command} {self.path} — {fmt % args}\n")

    # -- framing ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode(),
                   "application/json")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        if not length:
            return {}
        doc = json.loads(self.rfile.read(length).decode())
        if not isinstance(doc, dict):
            # A 400, not a 500: the client sent the wrong shape. `_post` maps
            # ValueError to 400, which is why this is not a TypeError.
            raise ValueError("body must be a JSON object")  # noqa: TRY004
        return doc

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
                return
            self._json(self._get(path, query))
        except protocol.ProtocolError as exc:
            self._error(404, str(exc))
        except FileNotFoundError:
            self._error(404, "not found")
        except Exception as exc:  # noqa: BLE001 — a dead route must not kill the server
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _get(self, path: str, query: dict) -> object:
        parts = [p for p in path.strip("/").split("/") if p]
        if parts == ["api", "recipes"]:
            rows, _ = protocol.recipes()
            return rows
        if parts == ["api", "config"]:
            return _config_payload()
        if parts == ["api", "runs"]:
            limit = int((query.get("limit") or [protocol.DEFAULT_RUN_LIMIT])[0])
            rows, _ = protocol.runs(limit)
            return rows
        if len(parts) == 3 and parts[:2] == ["api", "runs"]:
            result, _ = protocol.status(parts[2])
            return result
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "events":
            step = (query.get("step") or [""])[0]
            rows, _ = protocol.events(parts[2], step=step)
            return rows
        raise FileNotFoundError(path)

    def do_POST(self) -> None:
        # Same-origin only. A browser sends `Origin` on every cross-site POST,
        # so refusing anything but our own address is what keeps a page in
        # another tab from approving a gate through this server.
        sent = self.headers.get("Origin")
        if sent is not None and sent != self.origin:
            self._error(403, f"cross-origin POST refused (Origin: {sent})")
            return
        try:
            body = self._body()
            self._json(self._post(urlparse(self.path).path, body))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
        except protocol.ProtocolError as exc:
            self._error(409, str(exc))
        except FileNotFoundError:
            self._error(404, "not found")
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _post(self, path: str, body: dict) -> object:
        parts = [p for p in path.strip("/").split("/") if p]
        if parts == ["api", "runs"]:
            # Seeding only. The engine never drives itself from a web request:
            # a run started here is picked up by the Claude Code mod or by
            # `orchestrator headless <slug>`.
            result, _ = protocol.start(
                str(body.get("recipe") or ""),
                str(body.get("slug") or ""),
                inputs=body.get("inputs") or None,
                ticket_id=str(body.get("ticket_id") or ""),
            )
            return result
        if len(parts) == 4 and parts[:2] == ["api", "runs"]:
            ref, action = parts[2], parts[3]
            if action == "approve":
                result, _ = protocol.approve(
                    ref, str(body.get("token") or ""), edits=body.get("edits"))
                return result
            if action == "cancel":
                result, _ = protocol.cancel(ref)
                return result
            if action == "retry":
                result, _ = protocol.reset_step(ref, str(body.get("step_id") or ""))
                return result
            if action == "resume":
                result, _ = protocol.resume(ref, str(body.get("text") or ""))
                return result
        raise FileNotFoundError(path)


def serve(host: str, port: int, *, open_browser: bool = False) -> int:
    """Run the server until interrupted. Returns the process exit code."""
    handler = type("BoundHandler", (Handler,),
                   {"origin": f"http://{host}:{port}"})
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"orchestrator serve — {url}  (ctrl-c to stop)", file=sys.stderr)
    print("loopback only, no auth: anyone who can reach this port can "
          "approve and cancel runs.", file=sys.stderr)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


#: Hosts that are safe without an explicit `--host`. Anything else binds an
#: interface other machines can reach, and this server has no authentication.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def main(argv: list[str]) -> int:
    from orchestrator_next import settings

    host, explicit_host, open_browser = "127.0.0.1", False, False
    try:
        port = int(settings.get("serve.port"))
    except (settings.SettingsError, ValueError):
        port = 8765

    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--port":
            port = int(args.pop(0))
        elif arg == "--host":
            host, explicit_host = args.pop(0), True
        elif arg == "--open":
            open_browser = True
        elif arg in ("-h", "--help"):
            print("usage: orchestrator serve [--port N] [--host H] [--open]")
            return 0
        else:
            print(f"error: unknown argument {arg!r}", file=sys.stderr)
            return 3

    if host not in LOOPBACK and not explicit_host:
        print(f"error: refusing to bind {host} — pass --host explicitly",
              file=sys.stderr)
        return 3
    if host not in LOOPBACK:
        print(f"warning: {host} is reachable off this machine and serve has "
              "no authentication.", file=sys.stderr)
    return serve(host, port, open_browser=open_browser)
