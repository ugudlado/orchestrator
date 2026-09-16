"""PII redaction for step history — plan phase 3.3.

A contract may declare::

    pii: [customer_email, api_key]

naming keys of that step's ``in``/``out``. Before the engine writes a step's
history entry (which lands in ``runs.doc`` and the derived ``step_history``
index), those named values are replaced with ``"[redacted]"``.

Artifact *paths* stay — an artifact's contents are never stored in the DB in
the first place, only its name, path and sha256, so the path is the thing that
lets a human go find the file under the access controls of the filesystem.

Intended call site (``record.py``, which is the only module that holds both the
contract and the finished entry)::

    from orchestrator_next.redact import redact_entry
    entry = redact_entry(entry, getattr(contract, "pii", []) or [])
    # ... then save_doc(...) as before

The function is pure: it returns a copy and never mutates its argument.
"""
from __future__ import annotations

from typing import Any

REDACTED = "[redacted]"

#: Sub-dicts of a step_history entry whose keys are contract-declared names.
#: `outputs` is the spelling record.py actually writes; `out` is the protocol
#: v2 payload spelling. Both are scanned so either shape redacts. `evidence`
#: nests a second copy of the same outputs under `evidence.outputs`, so it has
#: to be scanned too — redact_mapping recurses from there.
_SCANNED_SECTIONS = ("outputs", "out", "in", "edits", "evidence")

#: Keys inside an artifact record that must survive redaction untouched.
_ARTIFACT_KEYS = frozenset({"artifact", "path", "sha256"})


def _is_artifact_ref(value: Any) -> bool:
    """True for ``{name, path, sha256}``-shaped artifact records."""
    return isinstance(value, dict) and bool(_ARTIFACT_KEYS & set(value))


def _redact_value(value: Any) -> Any:
    if _is_artifact_ref(value):
        return value
    return REDACTED


def redact_mapping(data: dict[str, Any], pii_keys: list[str] | set[str]) -> dict[str, Any]:
    """Return a copy of ``data`` with every ``pii_keys`` entry redacted.

    Recurses into nested plain dicts so a value buried under a grouping key is
    still caught. Artifact references are left as-is (paths stay).
    """
    names = set(pii_keys or ())
    if not names:
        return dict(data)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in names:
            out[key] = _redact_value(value)
        elif isinstance(value, dict):
            out[key] = redact_mapping(value, names)
        else:
            out[key] = value
    return out


def redact_entry(entry: dict[str, Any], pii_keys: list[str] | set[str]) -> dict[str, Any]:
    """Redact a whole ``step_history`` entry's ``out`` / ``in`` / ``edits``."""
    names = set(pii_keys or ())
    if not names:
        return dict(entry)
    out = dict(entry)
    for section in _SCANNED_SECTIONS:
        value = out.get(section)
        if isinstance(value, dict):
            out[section] = redact_mapping(value, names)
    return out
