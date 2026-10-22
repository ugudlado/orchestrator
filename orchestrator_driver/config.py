"""Driver configuration: a YAML file, with environment variables overriding it.

Precedence, highest first: environment variable, the file, the default. Unknown keys in the file are an
error, so a typo cannot silently fall back to a default."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

AGENTS = ("claude", "codex", "stub")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# key -> environment variable that overrides it
ENV = {
    "db": "ORCH_DB",
    "runs_root": "ORCH_RUNS_ROOT",
    "pack": "ORCH_PACK",
    "projects": "ORCH_PROJECTS",  # comma-separated
    "projects_root": "ORCH_PROJECTS_ROOT",
    "agent": "ORCH_AGENT",
    "model_override": "ORCH_MODEL_OVERRIDE",
    "sandbox_domains": "ORCH_SANDBOX_DOMAINS",  # comma-separated
    "step_env_keep": "ORCH_STEP_ENV_KEEP",  # comma-separated
    "notify_cmd": "ORCH_NOTIFY_CMD",
    "gate_timeout_days": "ORCH_GATE_TIMEOUT_DAYS",
    "poll_seconds": "ORCH_POLL_SECONDS",
    "stub_exec": "ORCH_STUB_EXEC",  # 1 or 0
}
# `agents` (the per-step map) has no environment form.


class ConfigError(ValueError):
    pass


def _path(v: str | Path, base: Path) -> Path:
    p = Path(v).expanduser()
    return p if p.is_absolute() else base / p


def _list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return [x for x in v if x]
    raise ConfigError(f"expected a list of strings, got {v!r}")


@dataclass(frozen=True)
class Config:
    db: Path = field(default_factory=lambda: Path("~/.orchestrator/driver.db").expanduser())
    runs_root: Path = field(default_factory=lambda: Path("~/.orchestrator/runs").expanduser())
    pack: Path | None = None
    projects: tuple[Path, ...] = ()
    projects_root: Path | None = None
    agent: str = "claude"
    agents: dict[str, str] = field(default_factory=dict)
    model_override: str = ""
    sandbox_domains: tuple[str, ...] = ()
    step_env_keep: tuple[str, ...] = ()
    notify_cmd: str = ""
    gate_timeout_days: float = 7
    poll_seconds: float = 0.5
    stub_exec: bool | None = None  # None: stub the exec scripts only when `agent` is stub

    # --- loading --------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None, env: dict | None = None) -> Config:
        """`path` is the --config argument; without it, $ORCH_DRIVER_CONFIG, then
        ~/.orchestrator/driver.yaml if it exists, else only defaults."""
        env = os.environ if env is None else env
        path = path or env.get("ORCH_DRIVER_CONFIG")
        if not path:
            default = Path("~/.orchestrator/driver.yaml").expanduser()
            path = default if default.is_file() else None
        raw, base = {}, Path.cwd()
        if path:
            file = Path(path).expanduser()
            if not file.is_file():
                raise ConfigError(f"config file not found: {file}")
            raw = yaml.safe_load(file.read_text()) or {}
            if not isinstance(raw, dict):
                raise ConfigError(f"{file}: expected a mapping at the top level")
            base = file.resolve().parent
        unknown = set(raw) - set(ENV) - {"agents"}
        if unknown:
            raise ConfigError(f"unknown config key(s): {', '.join(sorted(unknown))}")
        for key, var in ENV.items():
            if env.get(var) not in (None, ""):
                raw[key] = env[var]  # env wins; an empty variable counts as unset
        if not raw.get("pack") and env.get("ORCHESTRATOR_CONFIG"):
            raw["pack"] = env["ORCHESTRATOR_CONFIG"]  # the engine's own variable, as a last resort
        return cls._build(raw, base)

    @classmethod
    def _build(cls, raw: dict, base: Path) -> Config:
        d = cls()
        agent = str(raw.get("agent") or d.agent)
        agents = raw.get("agents") or {}
        if not isinstance(agents, dict):
            raise ConfigError("agents must map step ids to an agent")
        for a in (agent, *agents.values()):
            if a not in AGENTS:
                raise ConfigError(f"unknown agent {a!r} (one of: {', '.join(AGENTS)})")
        try:
            days, poll = (
                float(raw.get("gate_timeout_days", d.gate_timeout_days)),
                float(raw.get("poll_seconds", d.poll_seconds)),
            )
        except (TypeError, ValueError) as e:
            raise ConfigError(f"gate_timeout_days and poll_seconds must be numbers: {e}") from e
        if days <= 0 or poll <= 0:
            raise ConfigError("gate_timeout_days and poll_seconds must be positive")
        stub_exec = raw.get("stub_exec")
        if isinstance(stub_exec, str):
            if stub_exec.strip().lower() not in ("0", "1", "true", "false", "yes", "no"):
                raise ConfigError(f"stub_exec must be true or false, got {stub_exec!r}")
            stub_exec = stub_exec.strip().lower() in ("1", "true", "yes")
        elif stub_exec is not None and not isinstance(stub_exec, bool):
            raise ConfigError(f"stub_exec must be true or false, got {stub_exec!r}")
        pr = raw.get("projects_root")
        return cls(
            db=_path(raw["db"], base) if raw.get("db") else d.db,
            runs_root=_path(raw["runs_root"], base) if raw.get("runs_root") else d.runs_root,
            pack=_path(raw["pack"], base) if raw.get("pack") else None,
            projects=tuple(_path(p, base) for p in _list(raw.get("projects"))),
            projects_root=_path(pr, base) if pr else None,
            agent=agent,
            agents={str(k): str(v) for k, v in agents.items()},
            model_override=str(raw.get("model_override") or ""),
            sandbox_domains=tuple(_list(raw.get("sandbox_domains"))),
            step_env_keep=tuple(_list(raw.get("step_env_keep"))),
            notify_cmd=str(raw.get("notify_cmd") or ""),
            gate_timeout_days=days,
            poll_seconds=poll,
            stub_exec=stub_exec,
        )

    @property
    def exec_stubbed(self) -> bool:
        return self.agent == "stub" if self.stub_exec is None else self.stub_exec

    # --- the pack -------------------------------------------------------------

    def pack_dir(self) -> Path:
        if not self.pack:
            raise ConfigError("no pack: set `pack` in the config (or ORCH_PACK / ORCHESTRATOR_CONFIG)")
        if not (self.pack / "workflows").is_dir():
            raise ConfigError(f"pack {self.pack} has no workflows/ directory")
        return self.pack.resolve()

    def step_ids(self) -> set[str]:
        """Every step id any workflow in the pack names."""
        ids = set()
        for f in (self.pack_dir() / "workflows").glob("*.yaml"):
            for s in (yaml.safe_load(f.read_text()) or {}).get("steps") or []:
                sid = s if isinstance(s, str) else (s.get("id") or s.get("gate") if isinstance(s, dict) else None)
                if sid:
                    ids.add(str(sid).split()[0])
        return ids

    def check_pack(self) -> None:
        """The pack exists and the `agents` keys name its steps (a typo would silently use the default agent)."""
        unknown = set(self.agents) - self.step_ids()
        if unknown:
            raise ConfigError(f"agents names steps no workflow in {self.pack_dir()} has: {', '.join(sorted(unknown))}")

    def workflows(self) -> dict[str, dict]:
        """name -> {steps, gates, file} for each workflow file in the pack."""
        out = {}
        for f in sorted((self.pack_dir() / "workflows").glob("*.yaml")):
            steps = (yaml.safe_load(f.read_text()) or {}).get("steps") or []
            out[f.stem] = {
                "steps": len(steps),
                "gates": sum(1 for s in steps if isinstance(s, dict) and s.get("gate")),
                "file": str(f),
            }
        return out

    # --- projects -------------------------------------------------------------

    def allowed_projects(self) -> list[Path]:
        found = [p.resolve() for p in self.projects]
        if self.projects_root and self.projects_root.is_dir():
            found += sorted(p.resolve() for p in self.projects_root.iterdir() if (p / ".git").exists())
        return list(dict.fromkeys(found))

    def resolve_project(self, arg: str) -> Path:
        """A path, or the folder name of a configured project; it must be one the config allows."""
        allowed = self.allowed_projects()
        if not allowed:
            raise ConfigError("no projects configured: set `projects` or `projects_root`")
        by_path = Path(arg).expanduser().resolve()
        if by_path in allowed:
            return by_path
        named = [p for p in allowed if p.name == arg]
        if len(named) == 1:
            return named[0]
        names = ", ".join(str(p) for p in allowed)
        raise ConfigError(f"project {arg!r} is not one of the configured projects ({names})")

    def check_slug(self, slug: str) -> None:
        if not SLUG.fullmatch(slug):
            raise ConfigError(f"invalid slug {slug!r}: letters, digits, - and _ only, 1 to 64 characters")

    def run_dir(self, slug: str) -> Path:
        return self.runs_root / slug
