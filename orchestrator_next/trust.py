"""Pack trust — which sources this machine will pull workflow packs from.

Plan phase 3.2: "``~/.orchestrator/trust.toml``: allowed repos/orgs + signing
keys. Engine refuses unlisted/unsigned packs."

A pack is executable content: its steps carry shell scripts and agent charters
that run against the consumer repo. Pulling one from an arbitrary URL is
equivalent to `curl | sh`, so a remote pull must name a source the machine
owner has already listed.

File format (stdlib ``tomllib``, Python >= 3.11)::

    require_signed = false            # optional, default false

    [[allow]]
    source = "https://github.com/ugudlado/*"

    [[keys]]
    fingerprint = "ABCD1234..."       # informational; gpg holds the keyring

Rules:

* ``ORCHESTRATOR_TRUST_ALL=1`` bypasses every check (dev / test escape hatch).
* A **local path** source is always allowed — trust.toml governs the network,
  not your own filesystem.
* A **remote URL** needs ``~/.orchestrator/trust.toml`` to exist and to hold an
  ``[[allow]]`` entry whose ``source`` glob (fnmatch) matches the URL.
* ``require_signed = true`` additionally demands a verifiable git signature on
  the pulled ref (``git verify-tag`` / ``git verify-commit``); a missing ``gpg``
  is a refusal in that mode. Otherwise an unsigned pack only warns on stderr.
"""
from __future__ import annotations

import fnmatch
import os
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
    """Where the trust list lives (``ORCHESTRATOR_HOME_DIR`` aware, for tests)."""
    home = Path(os.environ.get("ORCHESTRATOR_HOME_DIR", "~/.orchestrator")).expanduser()
    return home / "trust.toml"


def trust_all_enabled() -> bool:
    return (os.environ.get(ENV_TRUST_ALL) or "").strip().lower() in ("1", "true", "yes")


def load_trust(path: Path | None = None) -> dict[str, Any] | None:
    """Parse trust.toml. ``None`` when the file does not exist."""
    p = path or trust_file()
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
        f"  mkdir -p {path.parent}\n"
        f"  cat >> {path} <<'EOF'\n"
        f"  [[allow]]\n"
        f'  source = "{source}"\n'
        f"  EOF\n\n"
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
    trust = load_trust(path)
    if trust is None:
        raise TrustError(_missing_trust_message(source, path))
    if not source_allowed(source, trust):
        patterns = ", ".join(allow_patterns(trust)) or "(none)"
        raise TrustError(
            f"refusing to pull {source!r}: no [[allow]] entry in {path} matches it "
            f"(allowed: {patterns}). Add:\n\n"
            f"  [[allow]]\n  source = \"{source}\"\n"
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
    trust = load_trust(path)
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
