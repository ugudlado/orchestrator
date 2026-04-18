#!/usr/bin/env python3
# requires: pyyaml, ruamel.yaml
# claude_discoverer.py — reference adapter for the `explore` step.
#
# Reads ORCHESTRATOR_* env vars set by `orchestrator next`, constructs the
# discoverer prompt from the step contract's instruction + rules, invokes
# `claude -p --output-format json` via subprocess, parses token/cost usage
# from the CLI's JSON output, and atomically appends a completed step_history
# entry to $ORCHESTRATOR_WORKFLOW_DIR/state.yaml.
#
# Exit codes:
#   0  — success (step_history entry written)
#   1  — error (with diagnostic on stderr; state.yaml NOT modified)
#
# Failure gate: state.yaml is only written AFTER a successful Claude invocation
# and successful JSON parse. Partial writes cannot occur because os.replace()
# is used (POSIX atomic).
#
# Usage (normally called by the orchestrator caller, not directly):
#   ORCHESTRATOR_CHANGE_ID=my-feature \
#   ORCHESTRATOR_PHASE=specify \
#   ORCHESTRATOR_STEP_ID=explore \
#   ORCHESTRATOR_ATTEMPT=1 \
#   ORCHESTRATOR_WORKFLOW_DIR=/path/to/.workflows/my-feature \
#   ORCHESTRATOR_REPO_ROOT=/path/to/code/orchestrator \
#   python3 config/scripts/adapters/claude_discoverer.py
"""Reference Claude adapter for the explore step."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path plumbing: allow importing orchestrator_next from the worktree root
# ---------------------------------------------------------------------------
_ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_ADAPTER_DIR, ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from ruamel.yaml import YAML as _RuamelYAML  # type: ignore
except ImportError:
    print("error: ruamel.yaml is required — install via: pip install --user ruamel.yaml", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_REQUIRED_ENV = [
    "ORCHESTRATOR_CHANGE_ID",
    "ORCHESTRATOR_PHASE",
    "ORCHESTRATOR_STEP_ID",
    "ORCHESTRATOR_ATTEMPT",
    "ORCHESTRATOR_WORKFLOW_DIR",
    "ORCHESTRATOR_REPO_ROOT",
]

_AGENT_NAME = "discoverer"


# ---------------------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------------------

def _load_env() -> dict[str, str]:
    """Load and validate required ORCHESTRATOR_* environment variables."""
    env: dict[str, str] = {}
    missing: list[str] = []
    for key in _REQUIRED_ENV:
        val = os.environ.get(key)
        if val is None:
            missing.append(key)
        else:
            env[key] = val
    if missing:
        print(
            f"error: missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return env


# ---------------------------------------------------------------------------
# Step contract loading
# ---------------------------------------------------------------------------

def _load_contract(step_id: str, workflow_dir: str, repo_root: str) -> dict[str, Any]:
    """
    Load the step contract YAML for the given step_id.

    Search order mirrors parser._contract_search_dirs:
      1. ORCHESTRATOR_HOME env var (canonical install)
      2. workflow_dir/config/steps/ (repo-local override)
      3. repo_root/config/steps/ (worktree steps)

    Returns the raw dict from the YAML file.
    Raises FileNotFoundError if no contract found.
    """
    import yaml

    # Build search dirs in priority order
    search_dirs: list[str] = []
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if home:
        search_dirs.append(os.path.join(home, "config", "steps"))
    if workflow_dir:
        search_dirs.append(os.path.join(workflow_dir, "config", "steps"))
    if repo_root:
        search_dirs.append(os.path.join(repo_root, "config", "steps"))

    for d in search_dirs:
        candidate = os.path.join(d, f"{step_id}.yaml")
        if os.path.isfile(candidate):
            with open(candidate, "r") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}

    raise FileNotFoundError(
        f"Step contract not found for '{step_id}'. Searched: {search_dirs}"
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(instruction: str, rules: list[str]) -> str:
    """
    Construct the discoverer prompt from instruction and rules.

    The prompt mirrors what the inline dispatcher passes to the agent:
    the step's instruction block preceded by applicable rules.
    """
    parts: list[str] = []

    if rules:
        parts.append("## Rules\n")
        for rule in rules:
            parts.append(f"- {rule}")
        parts.append("")

    if instruction:
        parts.append("## Instruction\n")
        parts.append(instruction.strip())

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude invocation
# ---------------------------------------------------------------------------

def _invoke_claude(prompt: str) -> tuple[dict[str, Any], int]:
    """
    Invoke `claude -p --output-format json <prompt>` via subprocess.

    Returns (parsed_json_dict, duration_ms).
    Raises SystemExit(1) on failure:
      - claude binary not found on PATH
      - non-zero exit from claude
      - stdout is not valid JSON
    """
    if not shutil.which("claude"):
        print("error: 'claude' CLI is not available on PATH. Install Claude Code.", file=sys.stderr)
        sys.exit(1)

    cmd = ["claude", "-p", "--output-format", "json", prompt]

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"error: failed to invoke claude CLI — {exc}", file=sys.stderr)
        sys.exit(1)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        stderr_snippet = result.stderr.strip()[:300] if result.stderr else "(no stderr)"
        print(
            f"error: claude exited with code {result.returncode}.\n"
            f"stderr: {stderr_snippet}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        stdout_snippet = result.stdout.strip()[:200] if result.stdout else "(empty)"
        print(
            f"error: claude stdout is not valid JSON — {exc}\n"
            f"stdout (first 200 chars): {stdout_snippet}",
            file=sys.stderr,
        )
        sys.exit(1)

    return payload, elapsed_ms


# ---------------------------------------------------------------------------
# Usage extraction
# ---------------------------------------------------------------------------

def _extract_usage(payload: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    """
    Extract token/cost usage from the claude --output-format json payload.

    The Claude CLI (--output-format json) returns a single JSON object with:
      - usage.input_tokens
      - usage.output_tokens
      - usage.cache_read_input_tokens
      - usage.cache_creation_input_tokens (not captured in state.yaml schema)
      - total_cost_usd
      - duration_ms
      - modelUsage: {<model_name>: {...}}  (for deriving the model name)

    We prefer the CLI's own fields. No ccusage subprocess is invoked.

    Per design.md: tool_calls is listed in the schema but the CLI JSON does
    not expose per-tool counts. We emit an empty dict to preserve the field
    for forward-compatibility.
    """
    usage_raw: dict[str, Any] = payload.get("usage", {})

    input_tokens: int | None = usage_raw.get("input_tokens")
    output_tokens: int | None = usage_raw.get("output_tokens")
    cache_read: int | None = usage_raw.get("cache_read_input_tokens")

    # Cost: total_cost_usd at the top level
    cost_usd: float | None = payload.get("total_cost_usd")

    # Duration: CLI provides duration_ms; fall back to our measured wall time
    duration_ms: int = payload.get("duration_ms", elapsed_ms)

    # Model: derive from modelUsage dict keys (first key alphabetically is stable)
    model: str | None = None
    model_usage: dict[str, Any] = payload.get("modelUsage", {})
    if model_usage:
        model = sorted(model_usage.keys())[0]

    usage: dict[str, Any] = {}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if cache_read is not None:
        usage["cache_read_input_tokens"] = cache_read
    if cost_usd is not None:
        usage["cost_usd"] = cost_usd
    # tool_calls: CLI doesn't expose per-tool breakdown; emit empty dict
    # so the field exists for forward-compatibility with future adapters
    # that use stream-json and CAN capture per-tool counts.
    usage["tool_calls"] = {}
    if model:
        usage["model"] = model
    usage["duration_ms"] = duration_ms

    if not any(k in usage for k in ("input_tokens", "output_tokens", "cost_usd")):
        # No usage data available — mark explicitly so callers know
        usage["usage_capture"] = "unavailable"

    return usage


# ---------------------------------------------------------------------------
# state.yaml atomic append
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_step_history(
    state_yaml_path: str,
    env: dict[str, str],
    usage: dict[str, Any],
    started_at: str,
) -> None:
    """
    Atomically append a completed step_history entry to state.yaml.

    Uses ruamel.yaml RoundTripLoader to preserve comments, ordering, and
    block-scalar style in the rest of the file. Writes to a sibling temp
    file in the same directory, then os.replace() for POSIX atomicity.

    The entry shape mirrors the design.md § state.yaml step_history[] entry:
      step_id, phase, status, agent, attempt, started_at, ended_at, usage
    """
    ryaml = _RuamelYAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # prevent ruamel from reflowing long lines

    state_path = Path(state_yaml_path)

    with open(state_path, "r", encoding="utf-8") as f:
        doc = ryaml.load(f)

    if not isinstance(doc, dict):
        print(f"error: state.yaml is not a mapping: {state_yaml_path}", file=sys.stderr)
        sys.exit(1)

    # Build the new entry as a plain dict (ruamel will serialize it correctly)
    ended_at = _now_iso()
    new_entry: dict[str, Any] = {
        "step_id": env["ORCHESTRATOR_STEP_ID"],
        "phase": env["ORCHESTRATOR_PHASE"],
        "status": "completed",
        "agent": _AGENT_NAME,
        "attempt": int(env["ORCHESTRATOR_ATTEMPT"]),
        "started_at": started_at,
        "ended_at": ended_at,
        "usage": usage,
    }

    # Ensure step_history exists and is a list
    if "step_history" not in doc or doc["step_history"] is None:
        doc["step_history"] = []
    doc["step_history"].append(new_entry)

    # Atomic write: write to temp file in same directory, then os.replace()
    state_dir = state_path.parent
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".yaml.tmp", dir=str(state_dir))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
            ryaml.dump(doc, tmp_f)
        os.replace(tmp_path, state_yaml_path)
    except Exception:
        # Clean up the temp file if replace fails
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    started_at = _now_iso()

    # 1. Validate environment
    env = _load_env()

    workflow_dir = env["ORCHESTRATOR_WORKFLOW_DIR"]
    repo_root = env["ORCHESTRATOR_REPO_ROOT"]
    step_id = env["ORCHESTRATOR_STEP_ID"]
    state_yaml_path = os.path.join(workflow_dir, "state.yaml")

    # 2. Load step contract (instruction + rules)
    try:
        contract = _load_contract(step_id, workflow_dir, repo_root)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    instruction: str = contract.get("instruction", "")
    rules: list[str] = contract.get("rules", [])

    # 3. Build prompt
    prompt = _build_prompt(instruction, rules)

    # 4. Check claude binary presence (gate before state.yaml write)
    if not shutil.which("claude"):
        print("error: 'claude' CLI is not available on PATH. Install Claude Code.", file=sys.stderr)
        sys.exit(1)

    # 5. Invoke Claude (exits non-zero on failure — no state.yaml write happens)
    payload, elapsed_ms = _invoke_claude(prompt)

    # 6. Extract usage
    usage = _extract_usage(payload, elapsed_ms)

    # 7. Atomic append to state.yaml (only reached on successful Claude invocation)
    try:
        _append_step_history(state_yaml_path, env, usage, started_at)
    except Exception as exc:
        print(f"error: failed to write state.yaml — {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"explore step completed. "
        f"input_tokens={usage.get('input_tokens', 'N/A')}, "
        f"output_tokens={usage.get('output_tokens', 'N/A')}, "
        f"cost_usd={usage.get('cost_usd', 'N/A')}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
