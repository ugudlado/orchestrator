"""Pure-stdlib Nostr event verifier (NIP-01 id + BIP-340 Schnorr signature).

Lives in the workflow pack — no orchestrator_next imports, no third-party
dependencies required (PyYAML is used for roster parsing when available,
with a minimal built-in fallback parser for the roster's restricted shape).

API:
    verify_event(event: dict) -> None
        Raises ValueError (with a reason) on malformed fields, recomputed-id
        mismatch, or an invalid signature.

    verify_from_roster(event: dict, roster_path: str, step_id: str) -> str
        verify_event() plus a check that event["pubkey"] matches the roster
        pubkey pinned for the step's role. Returns the role name.

Self-test / CLI:
    python3 verify_event.py <event.json> [--roster roster.yaml --step <id>]
"""
from __future__ import annotations

import hashlib
import json

# --- secp256k1 / BIP-340 (correctness over speed; pure Python) -------------

_P = 2**256 - 2**32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

Point = "tuple[int, int] | None"  # None is the point at infinity


def point_add(p1, p2):
    """Affine point addition on secp256k1 (None = infinity)."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1) * pow(2 * y1, _P - 2, _P) % _P
    else:
        lam = (y2 - y1) * pow(x2 - x1, _P - 2, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return (x3, (lam * (x1 - x3) - y1) % _P)


def point_mul(k: int, point):
    """Scalar multiplication via double-and-add."""
    result = None
    addend = point
    while k:
        if k & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        k >>= 1
    return result


def lift_x(x: int):
    """BIP-340 lift_x: the curve point with x-coord x and even y, or None."""
    if x >= _P:
        return None
    y_sq = (pow(x, 3, _P) + 7) % _P
    y = pow(y_sq, (_P + 1) // 4, _P)
    if y * y % _P != y_sq:
        return None
    return (x, y if y % 2 == 0 else _P - y)


def tagged_hash(tag: str, data: bytes) -> bytes:
    tag_digest = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_digest + tag_digest + data).digest()


def schnorr_verify(pubkey32: bytes, msg32: bytes, sig64: bytes) -> bool:
    """BIP-340 verification of sig64 over msg32 for x-only pubkey32."""
    if len(pubkey32) != 32 or len(msg32) != 32 or len(sig64) != 64:
        return False
    r = int.from_bytes(sig64[:32], "big")
    s = int.from_bytes(sig64[32:], "big")
    if r >= _P or s >= _N:
        return False
    pub = lift_x(int.from_bytes(pubkey32, "big"))
    if pub is None:
        return False
    e = (
        int.from_bytes(
            tagged_hash("BIP0340/challenge", sig64[:32] + pubkey32 + msg32), "big"
        )
        % _N
    )
    # R = s*G - e*P
    rpt = point_add(point_mul(s, _G), point_mul(_N - e, pub))
    return rpt is not None and rpt[1] % 2 == 0 and rpt[0] == r


# --- Nostr event verification ----------------------------------------------

_REQUIRED = ("id", "pubkey", "created_at", "kind", "tags", "content", "sig")


def _is_hex(value, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def compute_event_id(event: dict) -> str:
    """NIP-01 canonical id: sha256 of [0, pubkey, created_at, kind, tags, content]."""
    payload = json.dumps(
        [
            0,
            event["pubkey"].lower(),
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_event(event: dict) -> None:
    """Raise ValueError with a reason unless event is well-formed and signed."""
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    for field in _REQUIRED:
        if field not in event:
            raise ValueError(f"event missing required field '{field}'")
    if not _is_hex(event["pubkey"], 64):
        raise ValueError("event pubkey must be 64 hex characters")
    if not _is_hex(event["id"], 64):
        raise ValueError("event id must be 64 hex characters")
    if not _is_hex(event["sig"], 128):
        raise ValueError("event sig must be 128 hex characters")
    if not isinstance(event["created_at"], int) or isinstance(event["created_at"], bool):
        raise ValueError("event created_at must be an integer")
    if not isinstance(event["kind"], int) or isinstance(event["kind"], bool):
        raise ValueError("event kind must be an integer")
    if not isinstance(event["content"], str):
        raise ValueError("event content must be a string")
    tags = event["tags"]
    if not isinstance(tags, list) or any(
        not isinstance(t, list) or any(not isinstance(v, str) for v in t) for t in tags
    ):
        raise ValueError("event tags must be a list of lists of strings")

    expected_id = compute_event_id(event)
    if expected_id != event["id"].lower():
        raise ValueError(
            f"event id mismatch: declared {event['id']}, recomputed {expected_id}"
        )
    if not schnorr_verify(
        bytes.fromhex(event["pubkey"]),
        bytes.fromhex(expected_id),
        bytes.fromhex(event["sig"]),
    ):
        raise ValueError("invalid BIP-340 signature for event id under event pubkey")


# --- Roster binding ---------------------------------------------------------


def _parse_simple_yaml(text: str) -> dict:
    """Minimal fallback parser for the roster's restricted two-level shape.

    Supports nested mappings by indentation and scalar `key: value` lines.
    No sequences, anchors, or multiline scalars — enough for roster.yaml.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = line.strip().partition(":")
        if not sep:
            raise ValueError(f"roster: unparseable line: {raw_line!r}")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        value = value.strip().strip("'\"")
        if value:
            parent[key] = value
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def _load_roster(roster_path: str) -> dict:
    try:
        with open(roster_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise ValueError(f"Buzz roster not readable: {roster_path}: {exc}") from exc
    try:
        import yaml  # optional; engine interpreter has it

        data = yaml.safe_load(text)
    except ImportError:
        data = _parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"Buzz roster {roster_path} is not a mapping")
    return data


def roster_pubkey_for_step(roster_path: str, step_id: str) -> "tuple[str, str]":
    """Return (role, pubkey) pinned for step_id, mirroring buzz_adapter's shape."""
    raw = _load_roster(roster_path)
    steps = raw.get("steps")
    agents = raw.get("agents")
    if not isinstance(steps, dict) or not isinstance(agents, dict):
        raise ValueError(
            f"Buzz roster {roster_path} requires 'agents' and 'steps' mappings"
        )
    role = steps.get(step_id)
    if not isinstance(role, str) or not role.strip():
        raise ValueError(f"Buzz roster {roster_path} has no role for step '{step_id}'")
    role = role.strip()
    binding = agents.get(role)
    if not isinstance(binding, dict):
        raise ValueError(
            f"Buzz roster {roster_path} has no agent binding for role '{role}'"
        )
    pubkey = binding.get("pubkey")
    if not isinstance(pubkey, str) or not _is_hex(pubkey.strip(), 64):
        raise ValueError(
            f"Buzz roster role '{role}' requires a 64-character hex pubkey"
        )
    return role, pubkey.strip().lower()


def verify_from_roster(event: dict, roster_path: str, step_id: str) -> str:
    """verify_event() plus roster pubkey pinning. Returns the resolved role."""
    verify_event(event)
    role, pubkey = roster_pubkey_for_step(roster_path, step_id)
    if event["pubkey"].lower() != pubkey:
        raise ValueError(
            f"event pubkey {event['pubkey'].lower()} does not match roster pubkey "
            f"{pubkey} for step '{step_id}' (role '{role}')"
        )
    return role


# --- CLI / self-test --------------------------------------------------------


def _main(argv: "list[str]") -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Verify a signed Nostr event JSON file")
    ap.add_argument("event_json", help="path to the event as JSON")
    ap.add_argument("--roster", help="roster.yaml to pin the sender pubkey against")
    ap.add_argument("--step", help="step id being verified (required with --roster)")
    args = ap.parse_args(argv)

    with open(args.event_json, "r", encoding="utf-8") as f:
        event = json.load(f)
    try:
        if args.roster:
            if not args.step:
                raise ValueError("--step is required when --roster is given")
            role = verify_from_roster(event, args.roster, args.step)
            print(f"OK: event {event['id']} verified for role '{role}'")
        else:
            verify_event(event)
            print(f"OK: event {event['id']} verified")
    except ValueError as exc:
        print(f"FAIL: {exc}", file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
