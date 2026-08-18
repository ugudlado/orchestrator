#!/usr/bin/env bash
# verify-completion — verify the previous remote agent step's signed Nostr
# reply: BIP-340 signature + roster pubkey pinning + ```completion fence.
# Thin wrapper; all logic lives in verify_completion.py (invoked with the
# engine's interpreter per repo rules — ORCHESTRATOR_PYTHON).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
exec "${ORCHESTRATOR_PYTHON:-python3}" "${HERE}/verify_completion.py"
