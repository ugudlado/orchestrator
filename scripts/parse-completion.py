#!/usr/bin/env python3
"""Parse a COMPLETION block from agent stdout and emit done-payload JSON.

Usage:
    parse-completion.py [file]      # reads file argument or stdin
    echo "$output" | parse-completion.py

Exit codes:
    0  Success — valid COMPLETION block found, JSON written to stdout
    1  Error   — no COMPLETION block found or block is invalid (diagnostic on stderr)
"""
from __future__ import annotations

import json
import sys
from typing import Any

import yaml

VALID_STATUSES = frozenset({"completed", "recovered", "abandoned"})


def _indent_under_completion(header: str, body_lines: list[str]) -> str:
    """Build a YAML block with body fields nested under COMPLETION."""
    out = [header]
    for line in body_lines:
        if not line.strip():
            out.append("")
            continue
        out.append(line if line.startswith((" ", "\t")) else f"  {line}")
    return "\n".join(out)


def find_completion_block(text: str) -> str | None:
    """Find the COMPLETION: block in text and return the raw YAML string.

    Searches for 'COMPLETION:' at the start of a line, then captures either:
    - a fenced ```yaml ... ``` block (common in agent stdout), or
    - subsequent indented content until a non-indented line or EOF.
    """
    lines = text.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "COMPLETION:" or line.startswith("COMPLETION:"):
            start_idx = i
            break

    if start_idx is None:
        return None

    header = lines[start_idx]
    j = start_idx + 1
    while j < len(lines) and not lines[j].strip():
        j += 1

    # Fenced form: COMPLETION:\n```yaml\n  status: ...\n```
    if j < len(lines) and lines[j].strip().startswith("```"):
        inner: list[str] = []
        for k in range(j + 1, len(lines)):
            if lines[k].strip().startswith("```"):
                break
            inner.append(lines[k])
        return _indent_under_completion(header, inner)

    # Indented YAML form (done-payload contract)
    block_lines = [header]
    for line in lines[start_idx + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("\t"):
            break
        block_lines.append(line)

    while block_lines and not block_lines[-1].strip():
        block_lines.pop()

    return "\n".join(block_lines)


def parse_completion(text: str) -> dict[str, Any]:
    """Extract and validate the COMPLETION block, returning the parsed dict."""
    block = find_completion_block(text)
    if block is None:
        raise ValueError("No COMPLETION block found in input")

    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError as e:
        raise ValueError(f"Malformed YAML in COMPLETION block: {e}") from e

    if not isinstance(parsed, dict) or "COMPLETION" not in parsed:
        raise ValueError(f"Expected COMPLETION mapping, got: {type(parsed)}")

    completion = parsed["COMPLETION"]
    if not isinstance(completion, dict):
        raise ValueError(f"COMPLETION value must be a mapping, got: {type(completion)}")

    status = completion.get("status")
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of: {sorted(VALID_STATUSES)}"
        )

    return completion


def main() -> int:
    # Read from file argument or stdin
    if len(sys.argv) > 1:
        path = sys.argv[1]
        try:
            with open(path) as f:
                text = f.read()
        except OSError as e:
            print(f"ERROR: Cannot read file {path!r}: {e}", file=sys.stderr)
            return 1
    else:
        text = sys.stdin.read()

    try:
        completion = parse_completion(text)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(json.dumps(completion, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
