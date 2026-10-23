"""Fixtures: a driver config on the stub agent, a throwaway git project, a real worker process, the CLI.

The pack is the workflows repo: $ORCH_TEST_PACK, else /home/user/workflows/config. Without it the tests skip.
The database is Postgres: $ORCH_TEST_DATABASE_URL (any server; each test gets its own database on it), else a
throwaway cluster started from the local Postgres binaries (initdb, pg_ctl) for the session."""

from __future__ import annotations

import glob
import itertools
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
import yaml

from orchestrator_driver import cli
from orchestrator_driver.config import ENV
from orchestrator_driver.db import DB

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACK = Path("/home/user/workflows/config")


def pack_dir() -> Path | None:
    p = Path(os.environ.get("ORCH_TEST_PACK") or DEFAULT_PACK)
    return p if (p / "workflows").is_dir() else None


def pack_workflows() -> list[str]:
    p = pack_dir()
    return sorted(f.stem for f in (p / "workflows").glob("*.yaml")) if p else []


@pytest.fixture(scope="session")
def pack() -> str:
    p = pack_dir()
    if p is None:
        pytest.skip(
            f"workflow pack not found: set ORCH_TEST_PACK to its config dir "
            f"(default {DEFAULT_PACK}; clone ugudlado/workflows there)"
        )
    return str(p)


def pg_bin() -> Path | None:
    found = shutil.which("initdb")
    cands = [Path(found).parent] if found else [Path(d) for d in sorted(glob.glob("/usr/lib/postgresql/*/bin"))[::-1]]
    return next((d for d in cands if (d / "initdb").exists() and (d / "pg_ctl").exists()), None)


@pytest.fixture(scope="session")
def pg_server():
    """A server URL (with a maintenance database) to create per-test databases on."""
    if url := os.environ.get("ORCH_TEST_DATABASE_URL"):
        yield url
        return
    bindir = pg_bin()
    if bindir is None:
        pytest.fail(
            "no Postgres for the tests: set ORCH_TEST_DATABASE_URL (a server where the role may CREATE DATABASE), "
            "or install Postgres so initdb and pg_ctl are on PATH or in /usr/lib/postgresql/*/bin"
        )
    root = Path(tempfile.mkdtemp(prefix="orch-pg-"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # postgres refuses to run as root: then the cluster belongs to the postgres user
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(root, "postgres")
    data = root / "data"
    run = lambda *a: subprocess.run([*as_pg, *map(str, a)], check=True, capture_output=True)
    run(bindir / "initdb", "-D", data, "-U", "postgres", "-A", "trust", "-E", "UTF8")
    opts = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories= -c fsync=off"
    run(bindir / "pg_ctl", "-D", data, "-w", "-l", root / "log", "-o", opts, "start")
    try:
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        run(bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop")
        shutil.rmtree(root, ignore_errors=True)


_dbs = itertools.count()


@pytest.fixture
def database_url(pg_server):
    """A fresh, empty database for one test."""
    name = f"orch_test_{os.getpid()}_{next(_dbs)}"
    with psycopg.connect(pg_server, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    u = urlsplit(pg_server)
    yield urlunsplit(u._replace(path=f"/{name}"))
    with psycopg.connect(pg_server, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """The developer's own ORCH_* settings must not leak into config tests or the worker."""
    for var in (*ENV.values(), "ORCH_DRIVER_CONFIG", "ORCHESTRATOR_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(Path(os.environ.get("TMPDIR", "/tmp"))))  # no ~/.orchestrator/driver.yaml


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    (path / "README.md").write_text("# project\n")
    for cmd in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *cmd], check=True, env=env)
    return path


class Env:
    def __init__(self, tmp: Path, pack: str, database_url: str, **cfg):
        self.tmp, self.pack, self.workers = tmp, pack, []
        self.project = git_repo(tmp / "projects" / "app")
        self.config = tmp / "driver.yaml"
        self.config.write_text(
            yaml.safe_dump(
                {
                    "database_url": database_url,
                    "runs_root": str(tmp / "runs"),
                    "pack": pack,
                    "projects": [str(self.project)],
                    "agent": "stub",
                    "poll_seconds": 0.1,
                    **cfg,
                }
            )
        )
        self.env = {
            **os.environ,
            "ORCH_DRIVER_CONFIG": str(self.config),
            # the pack's scripts call a bare python3 that must import pyyaml, and put worktrees under tmp
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "WORKTREE_BASE_DIR": str(tmp / "worktrees"),
        }
        self.db = DB(database_url)

    def cli(self, *args: str) -> None:
        assert cli.main([*map(str, args), "--config", str(self.config)]) == 0

    def cfg_workflows(self) -> dict:
        from orchestrator_driver.config import Config

        return Config.load(self.config).workflows()

    def run_dir(self, slug: str) -> Path:
        return self.tmp / "runs" / slug

    def submit(self, slug: str, choices: dict | None = None, workflow: str = "dev") -> None:
        import json

        self.cli(
            "submit", "--workflow", workflow, "--project", "app", "--slug", slug, "--ticket", "build a thing",
            "--stub-choices", json.dumps(choices or {}),
        )  # fmt: skip

    def start(self) -> subprocess.Popen:
        w = subprocess.Popen(
            [sys.executable, "-m", "orchestrator_driver", "serve", "--config", str(self.config)],
            cwd=ROOT,
            env=self.env,
        )
        self.workers.append(w)
        return w

    def run(self, slug: str) -> dict:
        return self.db.run(slug)

    def cleanup(self) -> None:
        for w in self.workers:
            if w.poll() is None:
                w.send_signal(signal.SIGKILL)
                w.wait()
        for r in self.db.runs():  # no orphaned step processes after a failed test
            if r["pid"]:
                try:
                    os.killpg(r["pid"], signal.SIGKILL)
                except OSError:
                    pass
        self.db.close()

    def wait(self, slug: str, pred, timeout: float = 120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = self.run(slug)
            if pred(r):
                return r
            if r["status"] in ("failed", "stopped") and not pred({**r, "status": "x"}):
                raise AssertionError(f"run ended {r['status']}: {str(r['outcome'])[:600]}")
            time.sleep(0.1)
        raise AssertionError(f"timeout; run is {r['status']} waiting={r['waiting']} step={r['current_step']}")

    def at_gate(self, slug: str, gate: str, attempt: int = 1) -> dict:
        return self.wait(
            slug,
            lambda r: (
                r["status"] == "waiting" and r["waiting"]["step_id"] == gate and r["waiting"]["attempt"] == attempt
            ),
        )

    def approve(self, slug: str, gate: str, attempt: int = 1) -> None:
        self.at_gate(slug, gate, attempt)
        self.cli("decide", slug, gate, attempt, "approved")

    def kinds(self, slug: str) -> list[str]:
        return [e["kind"] for e in self.db.tail(slug, 10_000)]


@pytest.fixture
def make_env(tmp_path, pack, database_url):
    """Env factory: make_env(gate_timeout_days=0.00001, stub_exec=False, ...) sets config keys."""
    made = []

    def make(**cfg) -> Env:
        made.append(Env(tmp_path, cfg.pop("pack", pack), database_url, **cfg))
        return made[-1]

    yield make
    for e in made:
        e.cleanup()


@pytest.fixture
def env(make_env):
    return make_env()
