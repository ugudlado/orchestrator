"""Pack trust — which sources this machine will pull workflow packs from.

Plan phase 3.2: "``~/.orchestrator/trust.toml``: allowed repos/orgs + signing
keys. Engine refuses unlisted/unsigned packs."

A pack is executable content: its steps carry shell scripts and agent charters
that run against the consumer repo. Pulling one from an arbitrary URL is
equivalent to `curl | sh`, so a remote pull must name a source the machine
owner has already listed.

The list lives in ``[trust]`` of ``~/.orchestrator/orchestrator.toml``::

    [trust]
    allow = ["https://github.com/ugudlado/*"]
    require_signed = false            # optional, default false

The pre-settings ``~/.orchestrator/trust.toml`` (``[[allow]]`` array-of-tables)
is still read as the lowest layer, with a one-time deprecation warning.

Rules:

* ``ORCHESTRATOR_TRUST_ALL=1`` bypasses every check (dev / test escape hatch).
* A **local path** source is always allowed — trust.toml governs the network,
  not your own filesystem.
* A **remote URL** needs a ``trust.allow`` glob (fnmatch) that matches it.
* ``trust.require_signed`` additionally demands a verifiable git signature on
  the pulled ref (``git verify-tag`` / ``git verify-commit``); a missing ``gpg``
  is a refusal in that mode. Otherwise an unsigned pack only warns on stderr.
"""
from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ENV_TRUST_ALL = "ORCHESTRATOR_TRUST_ALL"


class TrustError(RuntimeError):
    """The pack source is not trusted — the pull must not happen."""


def trust_file() -> Path:
    """Where the trust list lives: ``orchestrator.toml`` once it exists, else
    the legacy ``trust.toml`` (both ``ORCHESTRATOR_HOME_DIR`` aware)."""
    from orchestrator_next import settings
    legacy = settings.home_dir() / "trust.toml"
    settings_path = settings.global_file()
    if settings_path.is_file() or not legacy.is_file():
        return settings_path
    return legacy


def trust_all_enabled() -> bool:
    from orchestrator_next import settings
    return bool(settings.get("trust.trust_all"))


def load_trust(path: Path | None = None) -> dict[str, Any] | None:
    """The effective trust list, in this module's ``[[allow]]`` shape.

    Normally this comes from ``orchestrator.toml``'s ``[trust]`` section, which
    already folds in the legacy ``~/.orchestrator/trust.toml`` (with a
    deprecation warning) as its lowest layer. ``None`` means no trust list was
    configured anywhere — the refusal path.

    An explicit ``path`` bypasses settings entirely and parses that one file,
    which is what the tests and ``--trust-file`` style call sites want.
    """
    if path is not None:
        return _parse_trust_file(path)
    from orchestrator_next import settings
    cfg = settings.load()
    allow = cfg.get("trust.allow")
    if not allow and cfg.source("trust.require_signed") == "default":
        return None
    return {
        "allow": [{"source": s} for s in allow],
        "require_signed": cfg.get("trust.require_signed"),
    }


def _parse_trust_file(p: Path) -> dict[str, Any] | None:
    if not p.is_file():
        return None
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TrustError(f"{p} is unreadable: {exc}") from exc


def is_local_source(source: str) -> bool:
    """A source that already exists on disk is local; everything else is remote."""
    try:
        return Path(source).exists()
    except OSError:
        return False


def allow_patterns(trust: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in trust.get("allow") or []:
        if isinstance(entry, dict) and entry.get("source"):
            out.append(str(entry["source"]))
    return out


def key_fingerprints(trust: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in trust.get("keys") or []:
        if isinstance(entry, dict) and entry.get("fingerprint"):
            out.append(str(entry["fingerprint"]))
    return out


def source_allowed(source: str, trust: dict[str, Any]) -> bool:
    return any(fnmatch.fnmatch(source, pat) for pat in allow_patterns(trust))


def _missing_trust_message(source: str, path: Path) -> str:
    return (
        f"refusing to pull {source!r}: no trust list at {path}.\n"
        f"A pack runs code in this repo — list the source first:\n\n"
        f"  orchestrator config set trust.allow '{source}' --global\n\n"
        f"or edit {path} directly:\n\n"
        f"  [trust]\n"
        f'  allow = ["{source}"]\n\n'
        f"Globs work too (e.g. \"https://github.com/ugudlado/*\"). "
        f"Set {ENV_TRUST_ALL}=1 to bypass trust checks entirely."
    )


def check_source(source: str, *, trust_path: Path | None = None) -> str:
    """Raise :class:`TrustError` unless ``source`` may be pulled.

    Returns a short reason string describing why it was allowed, for logging.
    """
    if trust_all_enabled():
        return f"{ENV_TRUST_ALL}=1 (all trust checks bypassed)"
    if is_local_source(source):
        return "local path (trust list applies to remote sources only)"

    path = trust_path or trust_file()
    # trust_path names one file to read; otherwise the layered settings decide.
    trust = load_trust(trust_path)
    if trust is None:
        raise TrustError(_missing_trust_message(source, path))
    if not source_allowed(source, trust):
        patterns = ", ".join(allow_patterns(trust)) or "(none)"
        raise TrustError(
            f"refusing to pull {source!r}: no trust.allow entry in {path} matches it "
            f"(allowed: {patterns}). Add:\n\n"
            f"  orchestrator config set trust.allow \"{source}\" --global\n"
        )
    return f"allowed by {path}"


def require_signed(trust: dict[str, Any] | None) -> bool:
    return bool((trust or {}).get("require_signed"))


def verify_signature(checkout: Path, ref: str | None, *, trust_path: Path | None = None) -> str:
    """Best-effort signature check on a freshly cloned pack.

    Returns a status line. Raises :class:`TrustError` only when the trust file
    sets ``require_signed = true`` and verification cannot be established —
    a deliberate stub with a real refusal path, not a PKI.
    """
    if trust_all_enabled():
        return "signature check skipped (trust-all)"
    path = trust_path or trust_file()
    trust = load_trust(trust_path)
    strict = require_signed(trust)

    if shutil.which("gpg") is None:
        if strict:
            raise TrustError(
                f"{path} sets require_signed = true but gpg is not on PATH — "
                f"install gpg or unset require_signed"
            )
        return "unsigned: gpg not available"

    target = ref or "HEAD"
    verb = "verify-tag" if ref and _is_tag(checkout, ref) else "verify-commit"
    proc = subprocess.run(
        ["git", "-C", str(checkout), verb, target],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return f"signature verified ({verb} {target})"
    if strict:
        raise TrustError(
            f"{path} sets require_signed = true but `git {verb} {target}` failed: "
            f"{(proc.stderr or proc.stdout).strip() or 'no signature'}"
        )
    print(
        f"config pull: warning: unsigned pack ({verb} {target} failed)",
        file=sys.stderr,
    )
    return "unsigned (warning only)"


def _is_tag(checkout: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", f"refs/tags/{ref}"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0
