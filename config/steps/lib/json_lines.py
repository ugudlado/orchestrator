#!/usr/bin/env python3
"""Extract the last JSON object line from a stream (archive step output)."""
from __future__ import annotations

import json
import sys


def extract_record(text: str, key: str) -> dict:
    found: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found = obj
    if key:
        val = found.get(key, {})
        return val if isinstance(val, dict) else {}
    return found


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    text = sys.stdin.read()
    print(json.dumps(extract_record(text, key)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
