"""Engine settings — one ``orchestrator.toml`` instead of scattered env vars.

Why
---
Every knob the engine grew arrived as its own ``ORCHESTRATOR_*`` environment
variable. That is fine for one or two and untenable at a dozen: nothing lists
them, nothing validates them, and the only way to make one stick is to edit a
shell profile. A setting that lives in a file can be read, diffed, committed
next to the pack it configures, and printed back with its source.

Layering
--------
Later wins, and every key remembers where it came from::

    defaults
      < ~/.orchestrator/orchestrator.toml        (machine)
      < <repo>/.orchestrator/orchestrator.toml   (repo)
      < ORCHESTRATOR_* environment variable      (override)
      < CLI flag                                 (this invocation)

Env still overrides both files, so nothing breaks for anyone already exporting
vars and no existing test that monkeypatches the environment has to change.
``ORCHESTRATOR_HOME_DIR`` keeps pointing at the machine file's directory.

Not in the file
---------------
``config_root`` is resolved from the pack layout (``paths.config_root``), and
``ORCHESTRATOR_CONFIG`` stays the explicit override for it. A settings file
that could also name the config root would give two answers to one question.

Secrets are referenced, never stored: ``[backlog] token_env = "BACKLOG_TOKEN"``
names the variable to read, so the file is safe to commit.
"""
from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

FILENAME = "orchestrator.toml"


class SettingsError(RuntimeError):
    """A settings value is present but unusable (wrong type, bad literal)."""


class Spec(NamedTuple):
    """One setting: where it lives, what it is, and what replaced it."""

    section: str
    key: str
    type: str          # "str" | "int" | "float" | "bool" | "list" | "table"
    default: Any
    env: str           # the ORCHESTRATOR_*/other var this key replaces ("" = none)
    help: str

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.key}"


#: The whole schema. Adding a knob means adding a row here and reading it
#: through ``get()`` — never a bare ``os.environ`` lookup.
SCHEMA: tuple[Spec, ...] = (
    Spec("state", "url", "str", "", "ORCHESTRATOR_STATE_URL",
         "Store new runs are created in (sqlite:///…, postgresql://…)."),
    Spec("state", "backend", "str", "sqlite", "ORCHESTRATOR_STATE_BACKEND",
         "Default store backend: sqlite or file."),
    Spec("state", "tenant", "str", "default", "ORCHESTRATOR_TENANT",
         "Tenant id new runs are written under."),

    Spec("run", "max_parallel", "int", 1, "ORCHESTRATOR_MAX_PARALLEL",
         "Steps to run concurrently; 1 = serial dispatch (default)."),
    Spec("run", "stale_after_hours", "float", 24.0, "ORCHESTRATOR_STALE_AFTER_HOURS",
         "Hours of no activity after which an active run counts as stale."),
    Spec("run", "disable_worktree_lock", "bool", False,
         "ORCHESTRATOR_DISABLE_WORKTREE_LOCK",
         "Skip the per-worktree git lock (safe only when strictly serial)."),

    Spec("headless", "backend", "str", "", "ORCHESTRATOR_HEADLESS_BACKEND",
         "Headless model backend: claude-cli or anthropic."),
    Spec("headless", "step_budget_usd", "float", 0.0, "ORCHESTRATOR_STEP_BUDGET_USD",
         "Per-step spend ceiling passed to the claude CLI; 0 = no ceiling."),
    Spec("headless", "claude_bin", "str", "", "ORCHESTRATOR_CLAUDE_BIN",
         "Executable to use instead of `claude` on PATH."),

    Spec("backlog", "url", "str", "", "BACKLOG_URL",
         "Backlog API base URL for ticket sync; unset = ticket steps no-op."),
    Spec("backlog", "project", "str", "", "BACKLOG_PROJECT",
         "Backlog project key."),
    Spec("backlog", "token_env", "str", "BACKLOG_TOKEN", "",
         "NAME of the env var holding the backlog token — never the token."),

    Spec("trust", "allow", "list", [], "",
         "Pack sources this machine may pull from (fnmatch globs)."),
    Spec("trust", "require_signed", "bool", False, "",
         "Demand a verifiable git signature on every pulled pack."),
    Spec("trust", "trust_all", "bool", False, "ORCHESTRATOR_TRUST_ALL",
         "Bypass every trust check (dev/test escape hatch)."),

    Spec("serve", "port", "int", 8765, "",
         "Port `orchestrator serve` binds for the local web UI."),

    Spec("models", "config", "str", "", "ORCHESTRATOR_MODELS_CONFIG",
         "models.yaml to layer above the pack's (also --models-config)."),
    Spec("models", "route_overrides", "table", {},
         "ORCHESTRATOR_MODEL_ROUTE_OVERRIDES",
         "Per-alias route overrides, e.g. {designer = {model_id = \"…\"}}."),
)

_BY_DOTTED = {s.dotted: s for s in SCHEMA}
_BY_ENV = {s.env: s for s in SCHEMA if s.env}


def spec_for(dotted: str) -> Spec:
    try:
        return _BY_DOTTED[dotted]
    except KeyError:
        raise SettingsError(
            f"unknown setting {dotted!r} — known: {', '.join(sorted(_BY_DOTTED))}"
        ) from None


# ---------------------------------------------------------------------------
# file locations
# ---------------------------------------------------------------------------
def home_dir() -> Path:
    """``~/.orchestrator`` unless ``ORCHESTRATOR_HOME_DIR`` says otherwise."""
    return Path(os.environ.get("ORCHESTRATOR_HOME_DIR", "~/.orchestrator")).expanduser()


def global_file() -> Path:
    return home_dir() / FILENAME


def repo_file(repo_root: str | Path | None = None) -> Path | None:
    """``<repo>/.orchestrator/orchestrator.toml``, or None with no repo root."""
    if repo_root is None:
        raw = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT")
        if not raw:
            return None
        repo_root = raw
    return Path(repo_root).expanduser() / ".orchestrator" / FILENAME


# ---------------------------------------------------------------------------
# coercion
# ---------------------------------------------------------------------------
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off", "")


def _coerce(spec: Spec, value: Any, origin: str) -> Any:
    """Bring ``value`` to the spec's type, or explain why it cannot be."""
    where = f"{spec.dotted} in {origin}"
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise SettingsError(f"{where}: expected a boolean, got {value!r}")
    if spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, (int, str, float)):
            raise SettingsError(f"{where}: expected an integer, got {value!r}")
        try:
            return int(str(value).strip())
        except ValueError:
            raise SettingsError(f"{where}: expected an integer, got {value!r}") from None
    if spec.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SettingsError(f"{where}: expected a number, got {value!r}")
        try:
            return float(str(value).strip())
        except ValueError:
            raise SettingsError(f"{where}: expected a number, got {value!r}") from None
    if spec.type == "list":
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        raise SettingsError(f"{where}: expected a list of strings, got {value!r}")
    if spec.type == "table":
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value or "{}")
            except json.JSONDecodeError as exc:
                raise SettingsError(f"{where}: expected a JSON object — {exc}") from None
            if not isinstance(parsed, dict):
                raise SettingsError(f"{where}: expected a JSON object, got {value!r}")
            return parsed
        raise SettingsError(f"{where}: expected a table, got {value!r}")
    # str
    if isinstance(value, (dict, list, tuple)):
        raise SettingsError(f"{where}: expected a string, got {value!r}")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
class Resolved(NamedTuple):
    value: Any
    source: str


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path} is not valid TOML: {exc}") from None
    except OSError as exc:
        raise SettingsError(f"{path} is unreadable: {exc}") from None


def _layer_from_file(path: Path, unknown: list[str]) -> dict[str, Any]:
    """Flatten one file to {dotted: raw}, collecting unknown keys as warnings."""
    doc = _read_toml(path)
    out: dict[str, Any] = {}
    for section, body in doc.items():
        if not isinstance(body, dict):
            unknown.append(f"{path}: {section} (expected a [{section}] table)")
            continue
        for key, value in body.items():
            dotted = f"{section}.{key}"
            if dotted in _BY_DOTTED:
                out[dotted] = value
            else:
                unknown.append(f"{path}: {dotted}")
    return out


class Settings:
    """Resolved settings plus, for every key, the layer that supplied it."""

    def __init__(
        self,
        values: dict[str, Resolved],
        files: list[Path],
        unknown: list[str],
        deprecated: list[str],
    ) -> None:
        self._values = values
        self.files = files
        self.unknown = unknown
        self.deprecated = deprecated

    def get(self, dotted: str) -> Any:
        return self._values[spec_for(dotted).dotted].value

    def source(self, dotted: str) -> str:
        return self._values[spec_for(dotted).dotted].source

    def items(self) -> list[tuple[Spec, Resolved]]:
        return [(s, self._values[s.dotted]) for s in SCHEMA]


def load(
    *,
    repo_root: str | Path | None = None,
    flags: dict[str, Any] | None = None,
) -> Settings:
    """Resolve every setting through the layer chain. Never cached: env is a
    layer, and tests (and ``config set``) move it underneath us."""
    files: list[Path] = []
    unknown: list[str] = []
    deprecated: list[str] = []
    layers: list[tuple[str, dict[str, Any]]] = []

    gpath = global_file()
    if gpath.is_file():
        files.append(gpath)
        layers.append((str(gpath), _layer_from_file(gpath, unknown)))

    rpath = repo_file(repo_root)
    if rpath is not None and rpath.is_file() and rpath != gpath:
        files.append(rpath)
        layers.append((str(rpath), _layer_from_file(rpath, unknown)))

    # Legacy ~/.orchestrator/trust.toml, folded in below the new file's [trust].
    legacy = _legacy_trust_layer(deprecated)
    if legacy:
        layers.insert(0, (str(home_dir() / "trust.toml"), legacy))

    env_layer = {
        spec.dotted: os.environ[spec.env]
        for spec in SCHEMA
        if spec.env and os.environ.get(spec.env) is not None
    }
    if env_layer:
        layers.append(("env", env_layer))

    flag_layer = {spec_for(k).dotted: v for k, v in (flags or {}).items() if v is not None}
    if flag_layer:
        layers.append(("flag", flag_layer))

    values: dict[str, Resolved] = {
        s.dotted: Resolved(s.default, "default") for s in SCHEMA
    }
    for origin, layer in layers:
        for dotted, raw in layer.items():
            spec = _BY_DOTTED[dotted]
            label = f"${spec.env}" if origin == "env" else origin
            values[dotted] = Resolved(_coerce(spec, raw, label), label)
    return Settings(values, files, unknown, deprecated)


def _legacy_trust_layer(deprecated: list[str]) -> dict[str, Any]:
    """Read the pre-settings ``~/.orchestrator/trust.toml`` shape.

    Its ``[[allow]]`` is an array of tables with a ``source`` key; the settings
    file flattens that to a list of glob strings. Warned about once per process
    so a long run does not repeat it.
    """
    path = home_dir() / "trust.toml"
    if not path.is_file():
        return {}
    doc = _read_toml(path)
    out: dict[str, Any] = {}
    allow = [
        str(e["source"]) for e in (doc.get("allow") or [])
        if isinstance(e, dict) and e.get("source")
    ]
    if allow:
        out["trust.allow"] = allow
    if "require_signed" in doc:
        out["trust.require_signed"] = doc["require_signed"]
    if out:
        deprecated.append(str(path))
        _warn_legacy_trust(path)
    return out


_warned_legacy_trust = False


def _warn_legacy_trust(path: Path) -> None:
    global _warned_legacy_trust
    if _warned_legacy_trust:
        return
    _warned_legacy_trust = True
    print(
        f"warning: {path} is deprecated — move [trust] into "
        f"{global_file()} (allow = [\"…\"], require_signed = …)",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# the convenience front door
# ---------------------------------------------------------------------------
def get(dotted: str, *, repo_root: str | Path | None = None, flag: Any = None) -> Any:
    """One setting, fully layered. The engine's replacement for os.environ.get."""
    flags = {dotted: flag} if flag is not None else None
    return load(repo_root=repo_root, flags=flags).get(dotted)


# ---------------------------------------------------------------------------
# writing — a minimal TOML emitter for flat sections
# ---------------------------------------------------------------------------
def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k} = {_fmt(v)}" for k, v in value.items()) + "}"
    return json.dumps(str(value))


def dump(doc: dict[str, dict[str, Any]]) -> str:
    """Render ``{section: {key: value}}`` as TOML. Flat sections only, which is
    the whole schema — nested tables appear as inline values."""
    chunks: list[str] = []
    for section in sorted(doc):
        body = doc[section]
        if not body:
            continue
        lines = [f"[{section}]"]
        lines += [f"{k} = {_fmt(v)}" for k, v in body.items()]
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def set_value(dotted: str, raw: str, *, path: Path) -> Any:
    """Write one key into ``path``, preserving every other key in the file."""
    spec = spec_for(dotted)
    value = _coerce(spec, raw, f"<{dotted} argument>")
    doc = _read_toml(path) if path.is_file() else {}
    # Drop keys we cannot round-trip rather than silently mangling them.
    clean: dict[str, dict[str, Any]] = {
        sec: dict(body) for sec, body in doc.items() if isinstance(body, dict)
    }
    clean.setdefault(spec.section, {})[spec.key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(clean), encoding="utf-8")
    return value


TEMPLATE_HEADER = """\
# orchestrator settings. Every key is optional; the value shown is the default.
# Precedence: this file < ORCHESTRATOR_* env var < CLI flag.
# A repo file (<repo>/.orchestrator/orchestrator.toml) beats the machine file
# (~/.orchestrator/orchestrator.toml).
#
# The config root is NOT set here — it comes from the pack layout, with
# ORCHESTRATOR_CONFIG as the explicit override.
"""


def template() -> str:
    """A commented file listing every setting at its default, all commented out."""
    out = [TEMPLATE_HEADER]
    for section in dict.fromkeys(s.section for s in SCHEMA):
        out.append(f"[{section}]")
        for spec in (s for s in SCHEMA if s.section == section):
            out.append(f"# {spec.help}")
            if spec.env:
                out.append(f"# env override: {spec.env}")
            out.append(f"# {spec.key} = {_fmt(spec.default)}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"
