"""``orchestrator init`` — an interactive first-run wizard over ``orchestrator.toml``.

``config init``/``config set`` already write the file; this module only adds
the conversation on top: ask each setting on a TTY (defaults shown in
brackets, Enter keeps the default), skip straight to defaults with ``--yes``
or off a TTY, and write only the keys that changed. It then offers to pull a
workflow pack when the repo has none, using the trust list it just wrote so
no ``ORCHESTRATOR_TRUST_ALL`` escape hatch is needed.

``config init`` becomes a thin alias for ``init --yes`` (kept for scripts that
already call it).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from orchestrator_next import settings

USAGE = "usage: orchestrator init [--global|--repo] [--yes]\n"


class _Question:
    """One prompt: dotted key, label, and how raw input becomes a value."""

    def __init__(self, dotted: str, label: str, *, secret: bool = False) -> None:
        self.dotted = dotted
        self.label = label
        self.secret = secret


# Order matters: this is the order the wizard asks in.
_QUESTIONS: tuple[_Question, ...] = (
    _Question("state.url", "State store URL (blank = sqlite at ~/.orchestrator/orchestrator.db)"),
    _Question("run.max_parallel", "Steps to run concurrently"),
    _Question("headless.backend", "Headless model backend (claude-cli|anthropic, blank = none)"),
    _Question("headless.step_budget_usd", "Per-step spend ceiling in USD (0 = none)"),
    _Question("backlog.url", "Backlog API base URL (blank = skip ticket sync)"),
    _Question("backlog.project", "Backlog project key"),
    _Question("backlog.token_env", "Env var holding the backlog token"),
    _Question("trust.allow", "Pack sources this machine may pull from (comma list)"),
    _Question("trust.require_signed", "Require a verifiable git signature on pulled packs"),
)

# backlog.url/.project/.token_env are skipped together when the url is left blank.
_BACKLOG_GROUP = ("backlog.url", "backlog.project", "backlog.token_env")


def _default_trust_allow(repo_root: Path | None) -> list[str]:
    """The source of any pack already pulled in this repo, else the upstream default."""
    if repo_root is not None:
        from orchestrator_next.config_pull import read_lock
        from orchestrator_next.paths import list_config_packs

        sources = []
        for _name, pack_dir in list_config_packs(repo_root):
            lock = read_lock(pack_dir)
            source = str(lock.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)
        if sources:
            return sources
    return ["https://github.com/ugudlado/*"]


def _fmt_default(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _ask(prompt: str, default: str, *, input_fn: Callable[[str], str]) -> str:
    shown = f"{prompt} [{default}]: " if default else f"{prompt} []: "
    raw = input_fn(shown)
    return raw.strip() or default


def run_wizard(
    *,
    is_global: bool,
    assume_yes: bool,
    repo_root: Path | None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    interactive: bool | None = None,
) -> int:
    path = settings.global_file() if is_global else settings.repo_file(repo_root)
    if path is None:
        print_fn("error: no repo root — run inside a git repo or pass --global")
        return 3

    if interactive is None:
        interactive = assume_yes is False and sys.stdin.isatty() and sys.stdout.isatty()

    if path.is_file():
        current = settings.load(repo_root=repo_root)
        if interactive:
            print_fn(f"{path} already exists.")
            answer = input_fn("Overwrite? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print_fn("aborted: nothing written")
                return 3
    else:
        current = settings.load(repo_root=repo_root)

    changes: dict[str, Any] = {}
    if interactive:
        defaults_for_trust = _default_trust_allow(repo_root)
        skip_backlog = False
        for q in _QUESTIONS:
            if q.dotted in _BACKLOG_GROUP[1:] and skip_backlog:
                continue
            spec = settings.spec_for(q.dotted)
            existing = current.get(q.dotted)
            default = (
                defaults_for_trust if q.dotted == "trust.allow" and existing == spec.default
                else existing
            )
            raw = _ask(q.label, _fmt_default(default), input_fn=input_fn)
            try:
                value = settings._coerce(spec, raw, "<init wizard>")  # noqa: SLF001
            except settings.SettingsError as exc:
                print_fn(f"error: {exc}")
                return 3
            if q.dotted == "backlog.url" and not raw.strip():
                skip_backlog = True
            if value != spec.default or spec.dotted in changes:
                changes[q.dotted] = value
    else:
        # --yes / non-TTY: defaults only, nothing written unless a pack lock
        # already implies a trust source worth recording.
        pass

    if changes:
        doc = settings._read_toml(path) if path.is_file() else {}  # noqa: SLF001
        clean: dict[str, dict[str, Any]] = {
            sec: dict(body) for sec, body in doc.items() if isinstance(body, dict)
        }
        for dotted, value in changes.items():
            spec = settings.spec_for(dotted)
            clean.setdefault(spec.section, {})[spec.key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings.dump(clean), encoding="utf-8")
    elif not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings.template(), encoding="utf-8")

    print_fn(f"wrote {path}")

    _maybe_pull_pack(
        repo_root=repo_root, interactive=interactive, input_fn=input_fn, print_fn=print_fn,
    )

    from orchestrator_next.settings_cli import show_cmd

    show_cmd([])
    return 0


def _maybe_pull_pack(
    *,
    repo_root: Path | None,
    interactive: bool,
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
) -> None:
    from orchestrator_next.config_pull import default_pack_name, pull, resolve_repo_root
    from orchestrator_next.paths import WORKFLOW_CONFIG_GIT_URL, list_config_packs
    from orchestrator_next.trust import TrustError

    root = resolve_repo_root(str(repo_root) if repo_root else None)
    if list_config_packs(root):
        return
    if not interactive:
        return

    source = _ask("Pack source", WORKFLOW_CONFIG_GIT_URL, input_fn=input_fn)
    if not source.strip():
        return
    try:
        lock = pull(
            repo_root=root,
            source=source.strip(),
            pack_name=default_pack_name(source.strip()),
            ref=None,
            export_skills=False,
        )
    except (OSError, RuntimeError, FileNotFoundError, ValueError, TrustError) as exc:
        print_fn(f"error: pack pull failed: {exc}")
        return
    print_fn(f"pulled pack {lock.get('pack')}")


_hint_shown = False


def maybe_print_first_run_hint() -> None:
    """Once per process: nudge toward ``orchestrator init`` when no settings
    file exists anywhere in the layer chain. Never blocks — a missing file
    just means defaults are in effect, which is a valid way to run."""
    global _hint_shown
    if _hint_shown:
        return
    _hint_shown = True
    if settings.global_file().is_file():
        return
    from orchestrator_next.cli import _default_repo_root_env

    _default_repo_root_env()
    rpath = settings.repo_file()
    if rpath is not None and rpath.is_file():
        return
    print(
        "hint: no orchestrator.toml yet — run `orchestrator init` "
        "(defaults are in effect)",
        file=sys.stderr,
    )


def init_main(argv: list[str]) -> int:
    is_global = "--global" in argv
    is_repo = "--repo" in argv
    assume_yes = "--yes" in argv
    if is_global and is_repo:
        sys.stderr.write("error: --global and --repo are mutually exclusive\n")
        return 3

    from orchestrator_next.cli import _default_repo_root_env
    import os

    _default_repo_root_env()
    repo_root = Path(os.environ.get("REPO_ROOT") or os.getcwd())

    return run_wizard(
        is_global=is_global,
        assume_yes=assume_yes,
        repo_root=None if is_global else repo_root,
    )
