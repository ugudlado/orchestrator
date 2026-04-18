#!/usr/bin/env bash
# HL-287 M3 stub. Inline step — inputs arrive as env vars; outputs go as
# a JSON dict on the last stdout line. Full port TBD (see contract YAML).
set -euo pipefail
echo "stub: not yet implemented" >&2
echo "{\"error\": \"stub\"}"
exit 64
