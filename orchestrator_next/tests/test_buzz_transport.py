"""Tests for the buzz transport trust gate (verify_event + await_reply).

Schnorr SIGNING is implemented here (test-only) so the tests can mint signed
events and prove verify_event accepts genuine events and rejects tampering.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess  # noqa: F401 — used by the CLI self-test
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_LIB = _REPO / ".orchestrator" / "workflows" / "lib" / "buzz" / "verify_event.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ve = _load("verify_event_under_test", _LIB)

_N = ve._N


# --- test-only BIP-340 signing ---------------------------------------------


def schnorr_sign(msg32: bytes, seckey: int, aux: bytes = b"\x00" * 32) -> tuple[str, str]:
    """Return (pubkey_hex, sig_hex) per BIP-340 with default aux randomness."""
    d0 = seckey % _N
    assert d0 != 0
    pub = ve.point_mul(d0, ve._G)
    d = d0 if pub[1] % 2 == 0 else _N - d0
    pk = pub[0].to_bytes(32, "big")
    t = (d ^ int.from_bytes(ve.tagged_hash("BIP0340/aux", aux), "big")).to_bytes(32, "big")
    k0 = int.from_bytes(ve.tagged_hash("BIP0340/nonce", t + pk + msg32), "big") % _N
    assert k0 != 0
    rpt = ve.point_mul(k0, ve._G)
    k = k0 if rpt[1] % 2 == 0 else _N - k0
    e = (
        int.from_bytes(
            ve.tagged_hash(
                "BIP0340/challenge", rpt[0].to_bytes(32, "big") + pk + msg32
            ),
            "big",
        )
        % _N
    )
    sig = rpt[0].to_bytes(32, "big") + ((k + e * d) % _N).to_bytes(32, "big")
    return pk.hex(), sig.hex()


COMPLETION_CONTENT = (
    "Task finished.\n\n"
    "```completion\n"
    "status: success\n"
    "outputs:\n"
    "  summary: \"implemented the thing\"\n"
    "reason: null\n"
    "usage:\n"
    "  input_tokens: 120\n"
    "  output_tokens: 45\n"
    "  cost: 0.002\n"
    "```\n"
)


def mint_event(seckey: int = 7, content: str = COMPLETION_CONTENT) -> dict:
    event = {
        "pubkey": "",
        "created_at": int(time.time()),
        "kind": 1,
        "tags": [["e", "0" * 64], ["p", "1" * 64]],
        "content": content,
    }
    pk_hex, _ = schnorr_sign(b"\x00" * 32, seckey)  # derive pubkey
    event["pubkey"] = pk_hex
    event_id = ve.compute_event_id(event)
    _, sig_hex = schnorr_sign(bytes.fromhex(event_id), seckey)
    event["id"] = event_id
    event["sig"] = sig_hex
    return event


def write_roster(tmp_path: Path, pubkey: str, step_id: str = "implement") -> Path:
    roster = tmp_path / "roster.yaml"
    roster.write_text(
        "version: 1\n"
        "\n"
        "agents:\n"
        "  implementer:\n"
        "    name: Implementer\n"
        f"    pubkey: {pubkey}\n"
        "    snapshot: .agents/agents/implementer.agent.json\n"
        "\n"
        "steps:\n"
        f"  {step_id}: implementer\n",
        encoding="utf-8",
    )
    return roster


# --- BIP-340 correctness (official test vector 0) ---------------------------


def test_bip340_official_vector_sign_and_verify():
    seckey = 3
    msg = b"\x00" * 32
    pk_hex, sig_hex = schnorr_sign(msg, seckey)
    assert pk_hex.upper() == (
        "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9"
    )
    assert sig_hex.upper() == (
        "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA8215"
        "25F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0"
    )
    assert ve.schnorr_verify(bytes.fromhex(pk_hex), msg, bytes.fromhex(sig_hex))


def test_schnorr_verify_rejects_bit_flip():
    msg = b"\x11" * 32
    pk_hex, sig_hex = schnorr_sign(msg, 42)
    sig = bytearray(bytes.fromhex(sig_hex))
    sig[40] ^= 0x01
    assert not ve.schnorr_verify(bytes.fromhex(pk_hex), msg, bytes(sig))


# --- verify_event ------------------------------------------------------------


def test_verify_event_accepts_minted_event():
    ve.verify_event(mint_event())  # no raise


def test_verify_event_rejects_tampered_content():
    event = mint_event()
    event["content"] = event["content"].replace("success", "failed")
    with pytest.raises(ValueError, match="id mismatch"):
        ve.verify_event(event)


def test_verify_event_rejects_recomputed_id_but_wrong_sig():
    # Attacker rewrites content AND recomputes the id — sig no longer matches.
    event = mint_event()
    event["content"] = "malicious"
    event["id"] = ve.compute_event_id(event)
    with pytest.raises(ValueError, match="invalid BIP-340 signature"):
        ve.verify_event(event)


def test_verify_event_rejects_wrong_pubkey():
    # Claim a different author for a genuinely signed event.
    event = mint_event(seckey=7)
    other_pk, _ = schnorr_sign(b"\x00" * 32, 99)
    event["pubkey"] = other_pk
    with pytest.raises(ValueError):
        ve.verify_event(event)


def test_verify_event_rejects_malformed_fields():
    event = mint_event()
    del event["sig"]
    with pytest.raises(ValueError, match="missing required field 'sig'"):
        ve.verify_event(event)
    with pytest.raises(ValueError, match="pubkey"):
        ve.verify_event({**mint_event(), "pubkey": "zz" * 32})
    with pytest.raises(ValueError, match="created_at"):
        ve.verify_event({**mint_event(), "created_at": "yesterday"})
    with pytest.raises(ValueError, match="tags"):
        ve.verify_event({**mint_event(), "tags": [["e", 5]]})


# --- verify_from_roster ------------------------------------------------------


def test_verify_from_roster_accepts_pinned_pubkey(tmp_path):
    event = mint_event(seckey=7)
    roster = write_roster(tmp_path, event["pubkey"])
    assert ve.verify_from_roster(event, str(roster), "implement") == "implementer"


def test_verify_from_roster_rejects_pubkey_mismatch(tmp_path):
    event = mint_event(seckey=7)
    other_pk, _ = schnorr_sign(b"\x00" * 32, 1234)
    roster = write_roster(tmp_path, other_pk)
    with pytest.raises(ValueError, match="does not match roster pubkey"):
        ve.verify_from_roster(event, str(roster), "implement")


def test_verify_from_roster_rejects_unmapped_step(tmp_path):
    event = mint_event(seckey=7)
    roster = write_roster(tmp_path, event["pubkey"])
    with pytest.raises(ValueError, match="no role for step"):
        ve.verify_from_roster(event, str(roster), "design")


def test_minimal_roster_parser_matches_shape():
    text = (
        "version: 1\n"
        "agents:\n"
        "  implementer:\n"
        "    name: Implementer\n"
        f"    pubkey: {'ab' * 32}\n"
        "steps:\n"
        "  implement: implementer\n"
    )
    data = ve._parse_simple_yaml(text)
    assert data["steps"]["implement"] == "implementer"
    assert data["agents"]["implementer"]["pubkey"] == "ab" * 32


# --- transport gate: await_reply.accept_event -------------------------------


ar = _load("await_reply_under_test", _LIB.parent / "await_reply.py")


def test_accept_event_returns_block_for_genuine_reply():
    event = mint_event()
    block = ar.accept_event(event, event["pubkey"])
    assert block is not None and "status: success" in block


def test_accept_event_rejects_wrong_author():
    event = mint_event()
    assert ar.accept_event(event, "2" * 64) is None


def test_accept_event_rejects_forged_signature(capsys):
    event = mint_event()
    event["content"] = COMPLETION_CONTENT + "\n(tampered)"
    assert ar.accept_event(event, event["pubkey"]) is None
    assert "rejected reply" in capsys.readouterr().err


def test_accept_event_ignores_fence_less_chatter():
    event = mint_event(content="just prose, no fence")
    assert ar.accept_event(event, event["pubkey"]) is None


def test_verify_event_cli_self_test(tmp_path):
    event = mint_event(seckey=7)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_LIB), str(event_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
