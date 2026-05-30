#!/usr/bin/env bash
# detect-workflow-issues.sh — single source of workflow-mechanics issue
# detection. Called by both the shell driver (orchestrator_next/scripts/run-workflow.sh) and
# the LLM driver (skills/orchestrate). Emits a JSON array on stdout; the
# caller merges it into the `workflow_issues` field of the `orchestrator
# done` payload.
#
#
# Categories emitted:
#   retry-success         — state.yaml step_history[-1].attempt > 1 && status == completed
#   script-warning        — inline run_step exited 10 (soft-fail)
#   script-failed         — inline run_step exited non-zero (not 10)
#   tool-crashed          — agent-tool invocation exited non-zero
#   manual-phase-advance  — LLM driver patched phase outside `orchestrator done`
#
# Flags (all optional):
#   --state-yaml PATH        Path to state.yaml. Required for retry detection.
#   --phase NAME             Phase name (for surfaced_at / dedup_key).
#   --step-id ID             Step id (for surfaced_at / dedup_key).
#   --script-exit N          Last inline-script exit code.
#   --script-stderr-file PATH  File holding script stderr (last 5 lines used as detail).
#   --tool-exit N            Last agent-tool exit code.
#   --attempt N              Current step attempt counter. When > 1 and the
#                            caller is recording a successful step, emits
#                            retry-success. Use this in the pre-`done` path
#                            instead of --state-yaml (state.yaml hasn't yet
#                            been updated with the success record).
#   --manual-phase-advance PHASE  LLM driver flag; emits manual-phase-advance issue.
#
# Always exits 0. On any internal failure, emits "[]" and warns on stderr.

set -uo pipefail

STATE_YAML=""
PHASE=""
STEP_ID=""
SCRIPT_EXIT=""
SCRIPT_STDERR_FILE=""
TOOL_EXIT=""
ATTEMPT=""
MANUAL_PHASE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --state-yaml)            STATE_YAML="${2:-}"; shift 2 ;;
    --phase)                 PHASE="${2:-}"; shift 2 ;;
    --step-id)               STEP_ID="${2:-}"; shift 2 ;;
    --script-exit)           SCRIPT_EXIT="${2:-}"; shift 2 ;;
    --script-stderr-file)    SCRIPT_STDERR_FILE="${2:-}"; shift 2 ;;
    --tool-exit)             TOOL_EXIT="${2:-}"; shift 2 ;;
    --attempt)               ATTEMPT="${2:-}"; shift 2 ;;
    --manual-phase-advance)  MANUAL_PHASE="${2:-}"; shift 2 ;;
    *)
      echo "detect-workflow-issues.sh: ignoring unknown arg: $1" >&2
      shift
      ;;
  esac
done

# Surface "[]" and warn on any internal failure rather than propagating.
emit_empty_and_exit() {
  echo "[]"
  exit 0
}

trap 'emit_empty_and_exit' ERR

python3 - "$STATE_YAML" "$PHASE" "$STEP_ID" "$SCRIPT_EXIT" "$SCRIPT_STDERR_FILE" "$TOOL_EXIT" "$ATTEMPT" "$MANUAL_PHASE" <<'PYEOF'
import json
import os
import sys

(
    _,
    state_yaml,
    phase,
    step_id,
    script_exit,
    script_stderr_file,
    tool_exit,
    attempt_arg,
    manual_phase,
) = sys.argv

issues = []
phase = phase or "unknown"
step_id = step_id or "unknown"
surfaced_at = f"{phase}/{step_id}"


def _stderr_tail(path, default):
    if not path or not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        tail = "\n".join(lines[-5:]).strip()
        return tail if tail else default
    except Exception as exc:
        print(f"detect-workflow-issues.sh: stderr read failed: {exc}", file=sys.stderr)
        return default


def _read_last_step_history(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:
        print(f"detect-workflow-issues.sh: state.yaml parse failed: {exc}", file=sys.stderr)
        return None
    hist = data.get("step_history") or []
    return hist[-1] if hist else None


# (1) retry-success — current attempt > 1 (pre-done path via --attempt),
# OR last completed step's attempt > 1 (post-done path via --state-yaml).
retry_attempt = None
retry_phase = phase
retry_step = step_id
try:
    a = int(attempt_arg) if attempt_arg not in ("", None) else None
except ValueError:
    a = None
if a is not None and a > 1:
    retry_attempt = a
else:
    last = _read_last_step_history(state_yaml)
    if last and last.get("status") == "completed":
        try:
            la = int(last.get("attempt") or 1)
        except (TypeError, ValueError):
            la = 1
        if la > 1:
            retry_attempt = la
            retry_phase = last.get("phase") or phase
            retry_step = last.get("step_id") or step_id

if retry_attempt is not None:
    issues.append({
        "category": "retry-success",
        "severity": "workaround-applied",
        "surfaced_at": f"{retry_phase}/{retry_step}",
        "detail": f"step succeeded on attempt {retry_attempt} after previous attempt(s) failed",
        "dedup_key": f"retry-success:{retry_phase}:{retry_step}",
    })

# (2) script-warning — soft-fail exit code 10
try:
    se = int(script_exit) if script_exit not in ("", None) else None
except ValueError:
    se = None
if se == 10:
    issues.append({
        "category": "script-warning",
        "severity": "workaround-applied",
        "surfaced_at": surfaced_at,
        "detail": _stderr_tail(script_stderr_file, "inline script exited 10 (soft-fail)"),
        "dedup_key": f"script-warning:{step_id}",
    })
elif se is not None and se != 0:
    issues.append({
        "category": "script-failed",
        "severity": "blocker",
        "surfaced_at": surfaced_at,
        "detail": _stderr_tail(
            script_stderr_file,
            f"inline script exited {se} (hard failure)",
        ),
        "dedup_key": f"script-failed:{phase}:{step_id}",
    })

# (3) tool-crashed — agent-tool non-zero exit
try:
    te = int(tool_exit) if tool_exit not in ("", None) else None
except ValueError:
    te = None
if te is not None and te != 0:
    issues.append({
        "category": "tool-crashed",
        "severity": "blocker",
        "surfaced_at": surfaced_at,
        "detail": f"agent-tool invocation exited {te}",
        "dedup_key": f"tool-crashed:{phase}:{step_id}",
    })

# (4) manual-phase-advance — LLM driver flag
if manual_phase:
    issues.append({
        "category": "manual-phase-advance",
        "severity": "workaround-applied",
        "surfaced_at": f"{manual_phase}/-",
        "detail": f"driver patched phase to {manual_phase} outside `orchestrator done`",
        "dedup_key": f"manual-phase-advance:{manual_phase}",
    })

json.dump(issues, sys.stdout)
PYEOF
echo  # trailing newline for shell-friendly stdout
