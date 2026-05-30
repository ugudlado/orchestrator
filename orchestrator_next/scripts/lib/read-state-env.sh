#!/usr/bin/env bash
# read-state-env.sh — load selected vars from state.yaml into the caller's shell.
# Used by CLI drivers (orchestrator complete teardown) that resolve state.yaml
# themselves. Workflow step scripts must use env injected by bin/orchestrator.
#
# RESOLVERS in the embedded Python is the single allowlist. Unknown var names
# exit non-zero. Values are shlex.quote'd; the shell evals Python output as-is.
# Usage (after source):
#   read_state_env "$STATE_YAML_PATH" CHANGE_ID ARCHIVE_PATH ...

read_state_env() {
  local _yaml=$1
  shift
  [[ -n "$_yaml" && -f "$_yaml" ]] || return 0
  local _want=("$@")
  local _output
  _output=$(python3 - "$_yaml" "${_want[@]}" <<'PY'
import shlex
import sys
from pathlib import Path

import yaml

path = sys.argv[1]
want = sys.argv[2:]
raw = yaml.safe_load(Path(path).read_text()) or {}

RESOLVERS = {
    "CHANGE_ID": lambda r: r.get("change_id") or r.get("slug") or "",
    "ARCHIVE_PATH": lambda r: r.get("archive_path") or "",
    "WORKTREE_ROOT": lambda r: r.get("worktree_path") or "",
    "WORKTREE_PATH": lambda r: r.get("worktree_path") or "",
    "REPO_ROOT": lambda r: r.get("repo_root") or "",
    "BRANCH": lambda r: r.get("branch") or "",
}

for var in want:
    resolver = RESOLVERS.get(var)
    if resolver is None:
        print(
            f"read_state_env: unknown var {var!r} "
            f"(allowed: {sorted(RESOLVERS)})",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"{var}={shlex.quote(str(resolver(raw)))}")
PY
  ) || return 1
  while IFS= read -r _line; do
    [[ -n "$_line" ]] || continue
    eval "$_line"
  done <<< "$_output"
}
