"""`jsonBlockOf` (mod/protocol.ts) parses a subagent's fenced JSON hand-back.

No TS test runner exists in this repo, but Node >=22.6's
`--experimental-strip-types` imports a `.ts` file directly (type syntax only
is stripped, no transform), so this shells out to the real `protocol.ts`
rather than re-implementing the parser in Python. Skipped when the
installed Node predates that flag.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

MOD_DIR = Path(__file__).resolve().parents[1] / "mod"


def _node_supports_strip_types() -> bool:
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    major, minor = (int(p) for p in out.lstrip("v").split(".")[:2])
    return (major, minor) >= (22, 6)


requires_node = pytest.mark.skipif(
    not _node_supports_strip_types(),
    reason="needs Node >=22.6 for --experimental-strip-types",
)


def _json_block_of(answer: str) -> dict | None:
    """Runs the real `jsonBlockOf` from mod/protocol.ts against `answer`."""
    script = (
        "import(\"./protocol.ts\").then(m => {"
        "  const r = m.jsonBlockOf(process.argv[1]);"
        "  process.stdout.write(JSON.stringify(r === undefined ? null : r));"
        "});"
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", script, "--", answer],
        cwd=MOD_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@requires_node
def test_column_zero_fence() -> None:
    answer = '```json\n{"discovery": "discovery.md", "reason": "done"}\n```'
    assert _json_block_of(answer) == {"discovery": "discovery.md", "reason": "done"}


@requires_node
def test_indented_hand_back_with_header_line() -> None:
    # What "[Subagent hand-back]" framing produces: a header line, then every
    # line of the quoted final message indented, fences included.
    answer = (
        "[Subagent hand-back]\n"
        "  Wrote discovery.md with the findings.\n"
        "\n"
        "  ```json\n"
        '  {"discovery": "discovery.md", "reason": "done"}\n'
        "  ```\n"
    )
    assert _json_block_of(answer) == {"discovery": "discovery.md", "reason": "done"}


@requires_node
def test_bare_fence_no_json_tag() -> None:
    answer = '```\n{"discovery": "discovery.md", "reason": "done"}\n```'
    assert _json_block_of(answer) == {"discovery": "discovery.md", "reason": "done"}


@requires_node
def test_trailing_prose_after_fence() -> None:
    answer = (
        '```json\n{"discovery": "discovery.md", "reason": "done"}\n```\n'
        "Let me know if you need anything else!"
    )
    assert _json_block_of(answer) == {"discovery": "discovery.md", "reason": "done"}


@requires_node
def test_no_object_returns_none() -> None:
    assert _json_block_of("just prose, no fence at all") is None
