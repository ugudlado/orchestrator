"""Fixtures: a driver config on the stub agent, a throwaway git project, a real worker process, the CLI.

The pack is the workflows repo: $ORCH_TEST_PACK, else /home/user/workflows/config. Without it the tests skip."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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
    def __init__(self, tmp: Path, pack: str):
        self.tmp, self.pack, self.workers = tmp, pack, []
        self.project = git_repo(tmp / "projects" / "app")
        self.config = tmp / "driver.yaml"
        self.config.write_text(
            yaml.safe_dump(
                {
                    "db": str(tmp / "driver.db"),
                    "runs_root": str(tmp / "runs"),
                    "pack": pack,
                    "projects": [str(self.project)],
                    "agent": "stub",
                    "poll_seconds": 0.1,
                }
            )
        )
        self.env = {**os.environ, "ORCH_DRIVER_CONFIG": str(self.config)}
        self.db = DB(tmp / "driver.db")

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
def env(tmp_path, pack):
    e = Env(tmp_path, pack)
    yield e
    for w in e.workers:
        if w.poll() is None:
            w.send_signal(signal.SIGKILL)
            w.wait()
    for r in e.db.runs():  # no orphaned step processes after a failed test
        if r["pid"]:
            try:
                os.killpg(r["pid"], signal.SIGKILL)
            except OSError:
                pass
