"""Judgment worker for the Inngest spike: one fresh Codex process per step.

    codex_worker.py <payload.json> <result.json>

Same brief as claude_worker.py (charter inlined, fenced ticket text, result JSON
handback), run with `codex exec` in Codex's own workspace-write sandbox: writes stay
in the worktree plus the run's artifacts and results, and the network is off.
Unlike the Claude sandbox, Codex's sandbox does not hide files from reads, so run
this worker as a user whose home holds nothing but Codex's sign-in.

Env: as claude_worker.py, plus ORCH_CODEX_MODEL (default: Codex's configured model)
and ORCH_CODEX_ARGS (extra CLI args, e.g. `-c model_reasoning_effort=medium`).
The project's MCP servers (REPO_ROOT's .mcp.json, see project_mcp.py) go in as `-c` overrides.
Prints one JSON line: {model, cost_usd, num_turns, duration_ms, session_id, is_error, tokens}.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import project_mcp
from claude_worker import ENV_KEEP, SECRET_ENV, brief

SIGN_IN = {"OPENAI_API_KEY", "CODEX_API_KEY"}
KEEP = ENV_KEEP + ["OPENAI_", "CODEX_"]


def step_env() -> dict:
    keep = KEEP + [k for k in os.environ.get("ORCH_STEP_ENV_KEEP", "").split(",") if k]

    def kept(name: str) -> bool:
        return any(name.upper().startswith(k) if k.endswith("_") else name.upper() == k for k in keep)

    return {k: v for k, v in os.environ.items() if kept(k) and (k in SIGN_IN or not SECRET_ENV.search(k))}


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text())
    result_file = sys.argv[2]
    model = os.environ.get("ORCH_CODEX_MODEL", "")
    argv = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        "sandbox_workspace_write.network_access=false",
        "--cd",
        os.getcwd(),
        "--add-dir",
        os.environ["ORCHESTRATOR_ARTIFACTS_DIR"],
        "--add-dir",
        str(Path(result_file).parent),
        *(["--model", model] if model else []),
        *project_mcp.codex_args(os.environ.get("REPO_ROOT") or os.getcwd()),
        *shlex.split(os.environ.get("ORCH_CODEX_ARGS", "")),
        brief(payload, result_file),
    ]
    start = time.monotonic()
    proc = subprocess.run(argv, capture_output=True, text=True, check=False, env=step_env())
    tokens: dict = {}
    turns, thread, last, error = 0, None, "", None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "thread.started":
            thread = ev.get("thread_id")
        elif kind == "turn.completed":
            turns += 1
            for k, v in (ev.get("usage") or {}).items():
                tokens[k] = tokens.get(k, 0) + v
        elif kind == "item.completed" and ev.get("item", {}).get("type") == "agent_message":
            last = ev["item"].get("text", "")
        elif kind in ("turn.failed", "error"):
            error = json.dumps(ev)[-600:]
    print(
        json.dumps(
            {
                "model": model or "codex-default",
                "cost_usd": None,  # Codex reports tokens, not dollars
                "num_turns": turns,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "session_id": thread,
                "is_error": proc.returncode != 0 or error is not None,
                "tokens": tokens,
                "result": (error or last or re.sub(r"\s+", " ", proc.stderr))[-600:],
            }
        )
    )
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
