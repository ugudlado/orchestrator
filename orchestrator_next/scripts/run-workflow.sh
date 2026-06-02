#!/usr/bin/env bash
# run-workflow.sh — shell-driven orchestrator dispatch loop
#
# Usage: run-workflow.sh <state.yaml> [TICKET-ID]
#
# Drives the full orchestrator next -> execute -> orchestrator done loop
# without an LLM in the dispatch path.
#
# Exit codes:
#   1  Workflow complete (complete_workflow)
#   2  Workflow blocked
#   3  Contract error (orchestrator next exit 3)
#   4  Unknown agent role in routing
#   5  Malformed COMPLETION block (parse-completion.py failed)
#   6  Tool subprocess non-zero exit (after recording failure)
#   7  Unexpected error
set -euo pipefail

STATE_YAML="${1:-}"
TICKET_ID="${2:-}"

if [ -z "$STATE_YAML" ]; then
  echo "ERROR: Usage: run-workflow.sh <state.yaml> [TICKET-ID]" >&2
  exit 7
fi

if [ ! -f "$STATE_YAML" ]; then
  echo "ERROR: state.yaml not found: $STATE_YAML" >&2
  exit 7
fi

STATE_YAML=$(cd "$(dirname "$STATE_YAML")" && pwd)/$(basename "$STATE_YAML")

# Resolve REPO_ROOT: walk up from state.yaml until spec/project.yaml is found.
_find_repo_root() {
  local dir
  dir="$(cd "$(dirname "$1")" && pwd)"
  while [ "$dir" != "/" ]; do
    if [ -f "$dir/spec/project.yaml" ]; then
      echo "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  return 1
}
if [ -z "${REPO_ROOT:-}" ] || [ ! -f "$REPO_ROOT/spec/project.yaml" ]; then
  REPO_ROOT="$(_find_repo_root "$STATE_YAML")" || REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$STATE_YAML")/../.." && pwd)}"
fi

# Resolve ORCHESTRATOR_HOME
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"

# Resolve script directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH_SCRIPTS_DIR="$SCRIPT_DIR"
STATE_INSPECT="$ORCH_SCRIPTS_DIR/lib/state_inspect.py"
DETECT_WORKFLOW_ISSUES="$ORCH_SCRIPTS_DIR/lib/detect-workflow-issues.sh"

# _merge_workflow_issues PAYLOAD ISSUES_JSON  →  payload with workflow_issues
# merged.  Best-effort: on jq failure prints PAYLOAD unchanged.  ISSUES_JSON
# is the JSON array stdout from detect-workflow-issues.sh.  Empty arrays are
# skipped (no field added).
_merge_workflow_issues() {
  local payload="$1" issues="$2"
  if [ -z "$issues" ] || [ "$issues" = "[]" ]; then
    printf '%s' "$payload"
    return 0
  fi
  local merged
  if merged=$(jq -c \
    --argjson new "$issues" \
    '.workflow_issues = ((.workflow_issues // []) + $new)' \
    <<<"$payload" 2>/dev/null); then
    printf '%s' "$merged"
  else
    printf '%s' "$payload"
  fi
}
# shellcheck source=lib/agent-routes.sh
source "$ORCH_SCRIPTS_DIR/lib/agent-routes.sh"

# Config files (tools.yaml, routes) come from the orchestrator repo in state.yaml,
# not the worktree checkout where state.yaml may live (worktree often has a stale
# config/ copy).
CONFIG_REPO_ROOT=$(python3 "$STATE_INSPECT" state-field "$STATE_YAML" repo_root 2>/dev/null || echo "")
if [ -z "$CONFIG_REPO_ROOT" ] || [ ! -f "$CONFIG_REPO_ROOT/spec/project.yaml" ]; then
  CONFIG_REPO_ROOT="$REPO_ROOT"
fi

# -----------------------------------------------------------------------
# Config resolution: .orchestrator override takes precedence over global
# -----------------------------------------------------------------------
resolve_config() {
  local name="$1"
  local repo_override="$CONFIG_REPO_ROOT/.orchestrator/config/$name"
  local repo_config="$CONFIG_REPO_ROOT/config/$name"
  local global_config="$ORCHESTRATOR_HOME/config/$name"

  if [ -f "$repo_override" ]; then
    echo "$repo_override"
  elif [ -f "$repo_config" ]; then
    echo "$repo_config"
  elif [ -f "$global_config" ]; then
    echo "$global_config"
  else
    echo ""
  fi
}

# ORC-105: tools + routes merged into config/agents.yaml. Resolve once; both
# vars point at the same file (tools: and agents:/models: blocks within it).
# Legacy fallback: config/tools.yaml for older installs.
TOOLS_YAML=$(resolve_config "agents.yaml")
if [ -z "$TOOLS_YAML" ]; then
  TOOLS_YAML=$(resolve_config "tools.yaml")
fi
if [ -z "$TOOLS_YAML" ] && [ -f "$SCRIPT_DIR/../../config/tools.yaml" ]; then
  TOOLS_YAML="$(cd "$SCRIPT_DIR/../../config" && pwd)/tools.yaml"
fi
ROUTES_YAML=$(resolve_config "agents.yaml")
if [ -z "$ROUTES_YAML" ]; then
  ROUTES_YAML=$(resolve_config "scripts/routes.yaml")
fi
# Runtime full-file override (orchestrator run --routes-override).
if [ -n "${ORCHESTRATOR_ROUTES_YAML:-}" ] && [ -f "$ORCHESTRATOR_ROUTES_YAML" ]; then
  ROUTES_YAML="$ORCHESTRATOR_ROUTES_YAML"
fi
if [ -n "${ORCHESTRATOR_AGENTS_CONFIG:-}" ] && [ -f "$ORCHESTRATOR_AGENTS_CONFIG" ]; then
  TOOLS_YAML="$ORCHESTRATOR_AGENTS_CONFIG"
  echo "agents config: $ORCHESTRATOR_AGENTS_CONFIG" >&2
fi
if [ -n "${ORCHESTRATOR_AGENT_ROUTE_OVERRIDES:-}" ] && [ "${ORCHESTRATOR_AGENT_ROUTE_OVERRIDES}" != "{}" ]; then
  echo "agent route overrides: $ORCHESTRATOR_AGENT_ROUTE_OVERRIDES" >&2
fi

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

run_pre_step_hook() {
  local hook="$REPO_ROOT/.orchestrator/hooks/pre-step.sh"
  if [ -f "$hook" ]; then
    bash "$hook" "$STATE_YAML" 2>/dev/null || true
  fi
}

# Resolve agent -> tool name (routes.yaml + ORCHESTRATOR_AGENT_ROUTE_OVERRIDES).
resolve_agent_tool() {
  local agent="$1"
  local subprocess=""

  if [ -n "$ROUTES_YAML" ] && [ -f "$ROUTES_YAML" ]; then
    subprocess=$(agent_routes_resolve_subprocess "$agent" "$ROUTES_YAML")
  fi

  if [ -n "$subprocess" ]; then
    echo "$subprocess"
    return 0
  fi

  return 1
}

# Resolve tool invocation shape from tools.yaml
# Returns: binary on stdout, sets TOOL_ARGS_TEMPLATE global
resolve_tool() {
  local tool_name="$1"
  local binary=""

  if [ -n "$TOOLS_YAML" ] && [ -f "$TOOLS_YAML" ]; then
    binary=$(yq -r ".tools.${tool_name}.binary // \"\"" "$TOOLS_YAML" 2>/dev/null)
  fi

  if [ -z "$binary" ]; then
    # Default: if tool name is an executable, use it directly
    binary="$tool_name"
  fi

  echo "$binary"
}

# Fetch ticket body for agent steps. Pending AC-3 (ticket.md replacement),
# reads backend from spec/project.yaml directly (ticket-common.sh removed).
fetch_ticket_context() {
  local ticket_id="$1"
  local backend
  backend=$(grep -E '^ticketing:' "$REPO_ROOT/spec/project.yaml" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' || echo "backlog")
  case "$backend" in
    backlog)
      if command -v backlog >/dev/null 2>&1; then
        (cd "$REPO_ROOT" && backlog task view "$ticket_id" --plain 2>/dev/null) || true
      fi
      ;;
  esac
}

# Build prompt string from instruction + step_context (+ optional ticket body)
build_prompt() {
  local instruction="$1"
  local step_context="$2"
  local ticket_context="${3:-}"
  local workflow_meta="${4:-}"
  local completion_contract
  completion_contract=$(cat <<'BLOCK'

---
You MUST end your stdout with a COMPLETION: block. Fields must be indented under COMPLETION: with two spaces — do NOT write them at column 0 and do NOT wrap in code fences.

IMPORTANT: Output values are parsed as YAML. If a value contains a colon (:), quote the entire value with double quotes.

Success form:
COMPLETION:
  step_id: <this-step-id>
  status: completed
  outputs:
    key: value

Failure/skip form:
COMPLETION:
  step_id: <this-step-id>
  status: abandoned
  outputs:
    reason: "why this step could not complete (quote if the reason contains a colon)"
BLOCK
)
  if [ -n "$ticket_context" ]; then
    printf '%s\n\n%s\n\nTicket / bug report (%s):\n%s\n\nStep context:\n%s\n%s\n' \
      "$instruction" "$workflow_meta" "$TICKET_ID" "$ticket_context" "$step_context" "$completion_contract"
  else
    printf '%s\n\n%s\n\nStep context:\n%s\n%s\n' \
      "$instruction" "$workflow_meta" "$step_context" "$completion_contract"
  fi
}

# Run tool binary using config/tools.yaml args_template ({prompt}, {prompt_file}).
# pi_settings_json (arg 8) carries the resolved pi defaults JSON from the caller
# so settings.json is read exactly once per agent step.
invoke_tool() {
  local tool_name="$1"
  local tool_binary="$2"
  local prompt="$3"
  local prompt_file="$4"
  local stdout_path="$5"
  local stderr_path="$6"
  local work_dir="${7:-}"
  local pi_settings_json="${8:-}"
  local model_tier="${9:-}"

  PI_SETTINGS_JSON="$pi_settings_json" \
  ORCHESTRATOR_MODEL_TIER="$model_tier" \
  python3 - "$tool_name" "$tool_binary" "$prompt" "$prompt_file" "$stdout_path" "$stderr_path" "$TOOLS_YAML" "$work_dir" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
import yaml

tool_name, binary, prompt, prompt_file, stdout_path, stderr_path, tools_path, work_dir = sys.argv[1:9]
template = []
if tools_path and Path(tools_path).is_file():
    with open(tools_path) as f:
        cfg = yaml.safe_load(f) or {}
    template = (cfg.get("tools") or {}).get(tool_name, {}).get("args_template") or []


def _expand_arg(arg: str) -> str:
    if "{prompt_file}" in arg:
        return arg.replace("{prompt_file}", prompt_file)
    if "{model_tier}" in arg:
        tier = os.environ.get("ORCHESTRATOR_MODEL_TIER") or "auto"
        return arg.replace("{model_tier}", tier)
    if arg == "{prompt}":
        return prompt
    return str(arg)


def _pi_flags() -> list[str]:
    """Convert PI_SETTINGS_JSON ({provider,model,thinking}) into pi CLI flags."""
    try:
        settings = json.loads(os.environ.get("PI_SETTINGS_JSON") or "{}")
    except json.JSONDecodeError:
        return []
    flags: list[str] = []
    for field, flag in (("provider", "--provider"), ("model", "--model"), ("thinking", "--thinking")):
        val = settings.get(field)
        if val:
            flags.extend([flag, str(val)])
    return flags


argv = [binary]
for arg in template:
    argv.append(_expand_arg(arg))

if len(argv) == 1:
    if tool_name in ("claude", "pi"):
        argv.extend(["-p", prompt])
    else:
        argv.append(prompt)

# Pi reads provider/model from saved settings.json by default in interactive mode;
# subprocess mode needs them as explicit flags. Prepend if the template hasn't
# already supplied --provider (user override wins).
if tool_name == "pi" and "--provider" not in argv:
    flags = _pi_flags()
    if flags:
        argv = [argv[0]] + flags + argv[1:]

cwd = work_dir if work_dir and Path(work_dir).is_dir() else None
env = os.environ.copy()
env.setdefault("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))

with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
    proc = subprocess.run(argv, stdout=out, stderr=err, cwd=cwd, env=env)
sys.exit(proc.returncode)
PY
}

# -----------------------------------------------------------------------
# Main dispatch loop
# -----------------------------------------------------------------------
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

_log_ts() {
  # Local time (respects TZ); ORC-84 AC-1.
  date +%H:%M:%S
}

# Millisecond wall clock (portable; macOS date lacks %3N). ORC-121.
_now_ms() {
  python3 -c 'import time; print(int(time.time() * 1000))'
}

# Print usage line for the last terminal step_history row matching step_id/phase.
_log_step_usage() {
  local step_id="$1"
  local phase="${2:-main}"
  python3 "$STATE_INSPECT" log-step-usage "$STATE_YAML" "$step_id" "$phase" 2>&1 >&2 || true
}

_emit_feature_rollup() {
  # workflow-report step already printed cost + workflow_issues during the run.
  # This hook only fires if the workflow exits before workflow-report ran (e.g.
  # state already archived). In that case, emit whatever tail_summary we can find.
  local change_id="$1"
  local tail_line
  tail_line=$(python3 - "$STATE_YAML" "$REPO_ROOT" "$change_id" <<'PY' 2>/dev/null || true
import sys, yaml, glob, os

def load_state(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return None

s = load_state(sys.argv[1])
if s is None:
    for p in sorted(glob.glob(os.path.join(sys.argv[2], "spec", "changes", "archive", f"*{sys.argv[3]}", "state.yaml"))):
        s = load_state(p)
        if s is not None:
            break
if not s:
    sys.exit(0)

for e in reversed(s.get("step_history") or []):
    if e.get("step_id") not in ("workflow-report", "cost-report"):
        continue
    if e.get("status") != "completed":
        continue
    ev = e.get("evidence") or {}
    tail = (ev.get("outputs") or e.get("outputs") or {}).get("tail_summary") or ""
    if tail:
        print(tail)
    break
PY
)
  if [ -n "$tail_line" ]; then
    echo "[$(_log_ts)] feature complete: $tail_line" >&2
  fi
}

# Already-archived reruns are refused upstream by orchestrator-run.sh's probe
# gate (before this script is reached), so no archive check is needed here.

WORKFLOW_CHANGE_ID=$(python3 "$STATE_INSPECT" state-field "$STATE_YAML" change_id --fallback slug 2>/dev/null || echo "")

# Operator rerun after spawn_failure_cap: clear zero-token failures and unblock.
PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" \
  python3 -m orchestrator_next.spawn_resume "$STATE_YAML" || true

while true; do
  # Run pre-step hook if present (e.g. ticket reconcile, custom checks)
  run_pre_step_hook

  # Call orchestrator next
  ACTION_JSON=$(orchestrator next "$STATE_YAML" 2>/tmp/orch_next_stderr) || {
    EXIT_CODE=$?
    case "$EXIT_CODE" in
      1)
        # complete_workflow
        echo "Workflow complete." >&2
        if [ -n "$WORKFLOW_CHANGE_ID" ]; then
          _emit_feature_rollup "$WORKFLOW_CHANGE_ID"
        fi
        exit 1
        ;;
      2)
        echo "Workflow blocked." >&2
        cat /tmp/orch_next_stderr >&2 2>/dev/null || true
        exit 2
        ;;
      3)
        if [ ! -f "$STATE_YAML" ] && grep -q 'state.yaml not found' /tmp/orch_next_stderr 2>/dev/null; then
          echo "Workflow complete (state archived)." >&2
          if [ -n "$WORKFLOW_CHANGE_ID" ]; then
            _emit_feature_rollup "$WORKFLOW_CHANGE_ID"
          fi
          exit 1
        fi
        echo "Contract error from orchestrator next." >&2
        cat /tmp/orch_next_stderr >&2 2>/dev/null || true
        exit 3
        ;;
      *)
        echo "ERROR: orchestrator next exited $EXIT_CODE" >&2
        exit 7
        ;;
    esac
  }

  # Inline script steps run inside bin/orchestrator (exit 0, no JSON on stdout).
  # bin/orchestrator logs →/run/✓ on stderr; here we add usage from step_history.
  if [ -z "$(printf '%s' "$ACTION_JSON" | tr -d '[:space:]')" ]; then
    LAST_STEP=$(python3 "$STATE_INSPECT" last-terminal-step "$STATE_YAML" 2>/dev/null || echo "{}")
    INLINE_STEP_ID=$(echo "$LAST_STEP" | jq -r '.step_id // empty')
    INLINE_PHASE=$(echo "$LAST_STEP" | jq -r '.phase // "main"')
    INLINE_STATUS=$(echo "$LAST_STEP" | jq -r '.status // empty')
    if [ -n "$INLINE_STEP_ID" ] && [ -n "$INLINE_STATUS" ]; then
      # orchestrator next runs inline scripts inside the CLI; progress lines go to
      # stderr and were captured in orch_next_stderr — surface them on success too.
      if [ -s /tmp/orch_next_stderr ]; then
        cat /tmp/orch_next_stderr >&2
      else
        echo "[$(_log_ts)] ✓ $INLINE_STEP_ID  done  status=$INLINE_STATUS" >&2
      fi
      _log_step_usage "$INLINE_STEP_ID" "$INLINE_PHASE"
    else
      echo "[$(_log_ts)]   orchestrator next returned no action; continuing loop" >&2
    fi
    # If state.yaml is still missing after archive relocation, the workflow is done.
    if [ ! -f "$STATE_YAML" ]; then
      echo "Workflow complete (state archived)." >&2
      if [ -n "$WORKFLOW_CHANGE_ID" ]; then
        _emit_feature_rollup "$WORKFLOW_CHANGE_ID"
      fi
      exit 1
    fi
    continue
  fi

  # Extract fields from action JSON
  STEP_ID=$(echo "$ACTION_JSON" | jq -r '.step_id // empty')
  PHASE=$(echo "$ACTION_JSON" | jq -r '.phase // "main"')
  KIND=$(echo "$ACTION_JSON" | jq -r '
    if (.run | type) == "string" and (.run | length) > 0 then "run_step"
    elif (.kind | type) == "string" and (.kind | length) > 0 then .kind
    else "run_inline"
    end
  ')
  AGENT=$(echo "$ACTION_JSON" | jq -r '.agent // ""')
  ATTEMPT=$(echo "$ACTION_JSON" | jq -r '.attempt // 1')
  STARTED_AT=$(echo "$ACTION_JSON" | jq -r '.started_at // empty')

  if [ "$KIND" = "run_step" ]; then
    KIND_LABEL="shell script"
  else
    KIND_LABEL="agent"
  fi
  AGENT_SUFFIX=""
  if [ -n "$AGENT" ]; then
    AGENT_SUFFIX="  agent=$AGENT"
  fi
  echo "[$(_log_ts)] → $STEP_ID  phase=$PHASE  kind=$KIND_LABEL${AGENT_SUFFIX}  attempt=$ATTEMPT" >&2
  COST_SO_FAR=$(echo "$ACTION_JSON" | jq -r '.cost_so_far // 0' 2>/dev/null || echo "0")
  if awk "BEGIN{exit !(${COST_SO_FAR:-0}>0)}" 2>/dev/null; then
    printf '[%s]   cost so far: $%.4f\n' "$(_log_ts)" "$COST_SO_FAR" >&2
  fi

  # Wall-clock start for duration_ms in done-payload (ORC-121).
  STEP_START_MS=$(_now_ms)

  # -----------------------------------------------------------------------
  # Dispatch on kind
  # -----------------------------------------------------------------------
  case "$KIND" in
    run_step)
      # Execute a script step
      SCRIPT_PATH=$(echo "$ACTION_JSON" | jq -r '.run // empty')
      ENV_BLOCK=$(echo "$ACTION_JSON" | jq -r '.env // {}')

      if [ -z "$SCRIPT_PATH" ]; then
        echo "ERROR: run_step action missing 'run' field" >&2
        exit 7
      fi

      # Build env vars from env block
      ENV_ARGS=""
      if [ "$ENV_BLOCK" != "{}" ] && [ "$ENV_BLOCK" != "null" ]; then
        ENV_ARGS=$(echo "$ENV_BLOCK" | jq -r 'to_entries[] | "\(.key)=\(.value)"' 2>/dev/null | tr '\n' ' ')
      fi

      echo "[$(_log_ts)]   run: $SCRIPT_PATH" >&2

      SCRIPT_EXIT=0
      if [ -n "$ENV_ARGS" ]; then
        env $ENV_ARGS bash "$SCRIPT_PATH" >"$TMP_DIR/script_stdout" 2>"$TMP_DIR/script_stderr" || SCRIPT_EXIT=$?
      else
        bash "$SCRIPT_PATH" >"$TMP_DIR/script_stdout" 2>"$TMP_DIR/script_stderr" || SCRIPT_EXIT=$?
      fi

      # Exit 10 = soft-fail (workflow-issues.md): step still completed, but the
      # driver records a script-warning entry built from script stderr.
      if [ "$SCRIPT_EXIT" -eq 0 ] || [ "$SCRIPT_EXIT" -eq 10 ]; then
        STATUS="completed"
      else
        STATUS="failed"
      fi

      DURATION_MS=$(($( _now_ms ) - STEP_START_MS))
      DONE_PAYLOAD=$(python3 "$STATE_INSPECT" build-payload script \
        --step-id "$STEP_ID" --phase "$PHASE" --status "$STATUS" \
        --started-at "$STARTED_AT" --duration-ms "$DURATION_MS")

      # Workflow-issues detection (script-warning on exit 10).
      WFI_JSON=$(bash "$DETECT_WORKFLOW_ISSUES" \
        --phase "$PHASE" --step-id "$STEP_ID" \
        --script-exit "$SCRIPT_EXIT" \
        --script-stderr-file "$TMP_DIR/script_stderr" 2>/dev/null || echo "[]")
      DONE_PAYLOAD=$(_merge_workflow_issues "$DONE_PAYLOAD" "$WFI_JSON")

      if echo "$DONE_PAYLOAD" | orchestrator done "$STATE_YAML"; then
        echo "[$(_log_ts)] ✓ $STEP_ID  done  status=$STATUS" >&2
        _log_step_usage "$STEP_ID" "$PHASE"
      fi

      # archive-completed-change moves state.yaml to the archive path.
      # Update STATE_YAML so subsequent steps (merge-to-main, remove-worktree)
      # dispatch against the archived location.
      if [ "$STEP_ID" = "archive-completed-change" ] && [ "$STATUS" = "completed" ]; then
        _archive_path=$(python3 -c "
import sys, json
try:
    d = json.load(open('$TMP_DIR/script_stdout'))
    print(d.get('archive_record', {}).get('archive_path') or '')
except Exception:
    print('')
" 2>/dev/null || true)
        if [ -n "$_archive_path" ]; then
          _new_state="${REPO_ROOT}/${_archive_path}/state.yaml"
          if [ -f "$_new_state" ]; then
            STATE_YAML="$_new_state"
            echo "[$(_log_ts)]   state relocated: $STATE_YAML" >&2
          fi
        fi
      fi
      ;;

    run_inline|resume_step)
      # Execute an agent step
      INSTRUCTION=$(echo "$ACTION_JSON" | jq -r '.instruction // ""')
      STEP_CONTEXT=$(echo "$ACTION_JSON" | jq -c '.step_context // {}')

      # Resolve agent -> tool binary
      TOOL_NAME=$(resolve_agent_tool "$AGENT") || {
        echo "ERROR: no route for agent '$AGENT'" >&2
        exit 4
      }

      TOOL_BINARY=$(resolve_tool "$TOOL_NAME")

      # Check if tool binary is available (PATH name or absolute path from tools.yaml)
      if [[ "$TOOL_BINARY" == */* ]]; then
        if [ ! -x "$TOOL_BINARY" ]; then
          echo "ERROR: tool binary not executable: $TOOL_BINARY (agent='$AGENT', tool='$TOOL_NAME')" >&2
          exit 4
        fi
      elif ! command -v "$TOOL_BINARY" >/dev/null 2>&1; then
        echo "ERROR: tool binary '$TOOL_BINARY' not found in PATH (agent='$AGENT', tool='$TOOL_NAME')" >&2
        exit 4
      fi

      MODEL_TIER=""
      if [ -n "$ROUTES_YAML" ] && [ -f "$ROUTES_YAML" ]; then
        MODEL_TIER=$(agent_routes_resolve_model "$AGENT" "$ROUTES_YAML")
      fi
      # Pi defaults from ~/.pi/agent/settings.json: resolved once here, reused
      # by both the log line below and invoke_tool (passed as 8th arg).
      PI_SETTINGS_JSON=""
      if [ "$TOOL_NAME" = "pi" ]; then
        PI_SETTINGS_JSON=$(python3 "$STATE_INSPECT" pi-settings 2>/dev/null || echo "{}")
      fi
      # routes.yaml "model": Claude billing tier (opus/sonnet/haiku) or Cursor CLI id (auto).
      if [ -n "$MODEL_TIER" ] && [ "$TOOL_NAME" = "claude" ]; then
        echo "[$(_log_ts)]   invoking $TOOL_NAME ($TOOL_BINARY)  tier=$MODEL_TIER" >&2
      elif [ -n "$MODEL_TIER" ] && [ "$TOOL_NAME" = "cursor" ]; then
        echo "[$(_log_ts)]   invoking $TOOL_NAME ($TOOL_BINARY)  model=$MODEL_TIER" >&2
      elif [ "$TOOL_NAME" = "pi" ]; then
        PI_SUFFIX=$(echo "$PI_SETTINGS_JSON" \
          | jq -r 'if .provider and .model then "  provider=\(.provider)  model=\(.model)" else "" end' \
          2>/dev/null || echo "")
        echo "[$(_log_ts)]   invoking $TOOL_NAME ($TOOL_BINARY)${PI_SUFFIX}" >&2
      else
        echo "[$(_log_ts)]   invoking $TOOL_NAME ($TOOL_BINARY)" >&2
      fi

      # Build the prompt (ticket body + change_id so diagnose/implement agents have a target)
      TICKET_CONTEXT=""
      if [ -n "$TICKET_ID" ]; then
        TICKET_CONTEXT=$(fetch_ticket_context "$TICKET_ID")
      fi
      WORKFLOW_META=$(python3 "$STATE_INSPECT" workflow-meta "$STATE_YAML" 2>/dev/null || true)
      PROMPT=$(build_prompt "$INSTRUCTION" "$STEP_CONTEXT" "$TICKET_CONTEXT" "$WORKFLOW_META")
      AGENT_OVERLAY=$(python3 -m orchestrator_next.agent_overlay "$REPO_ROOT" "$AGENT" 2>/dev/null || true)
      if [ -n "$AGENT_OVERLAY" ]; then
        PROMPT="$PROMPT"$'\n'"$AGENT_OVERLAY"
      fi
      PROMPT_FILE="$TMP_DIR/prompt_${STEP_ID}.txt"
      echo "$PROMPT" > "$PROMPT_FILE"

      AGENT_WORK_DIR="$REPO_ROOT"
      WORKTREE_PATH=$(echo "$WORKFLOW_META" | sed -n 's/^worktree_path=//p' | head -1)
      if [ -n "$WORKTREE_PATH" ] && [ -d "$WORKTREE_PATH" ]; then
        AGENT_WORK_DIR="$WORKTREE_PATH"
      fi

      TOOL_EXIT=0
      TOOL_STDOUT="$TMP_DIR/tool_stdout_${STEP_ID}.txt"
      invoke_tool "$TOOL_NAME" "$TOOL_BINARY" "$PROMPT" "$PROMPT_FILE" \
        "$TOOL_STDOUT" "$TMP_DIR/tool_stderr_${STEP_ID}.txt" "$AGENT_WORK_DIR" \
        "$PI_SETTINGS_JSON" "$MODEL_TIER" || TOOL_EXIT=$?

      if [ "$TOOL_EXIT" -ne 0 ]; then
        echo "WARN: tool '$TOOL_BINARY' exited $TOOL_EXIT" >&2
        if [ -s "$TMP_DIR/tool_stderr_${STEP_ID}.txt" ]; then
          echo "[$(_log_ts)]   tool stderr (last 8 lines):" >&2
          tail -8 "$TMP_DIR/tool_stderr_${STEP_ID}.txt" >&2
        fi
        # Record failure via orchestrator done
        DURATION_MS=$(($( _now_ms ) - STEP_START_MS))
        DONE_PAYLOAD=$(python3 "$STATE_INSPECT" build-payload failed \
          --step-id "$STEP_ID" --phase "$PHASE" --agent "$AGENT" \
          --exit-code "$TOOL_EXIT" --duration-ms "$DURATION_MS")
        WFI_JSON=$(bash "$DETECT_WORKFLOW_ISSUES" \
          --phase "$PHASE" --step-id "$STEP_ID" \
          --tool-exit "$TOOL_EXIT" 2>/dev/null || echo "[]")
        DONE_PAYLOAD=$(_merge_workflow_issues "$DONE_PAYLOAD" "$WFI_JSON")
        echo "$DONE_PAYLOAD" | orchestrator done "$STATE_YAML" || true
        continue
      fi

      # Split structured stdout into assistant text + normalized usage (ORC-111).
      TOOL_TEXT="$TMP_DIR/tool_text_${STEP_ID}.txt"
      TOOL_USAGE="$TMP_DIR/tool_usage_${STEP_ID}.json"
      ADAPTER_TOOL="$TOOL_NAME"
      if [ "$TOOL_NAME" = "cursor" ]; then
        ADAPTER_TOOL="cursor-agent"
      fi
      PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" \
        python3 - "$ADAPTER_TOOL" "$TOOL_STDOUT" "$TOOL_TEXT" "$TOOL_USAGE" "$MODEL_TIER" <<'PY'
import json
import sys
from pathlib import Path

from orchestrator_next.usage_adapters import split_stdout

tool, stdout_path, text_path, usage_path, route_model = sys.argv[1:6]
route = route_model if route_model else None
result = split_stdout(tool, stdout_path, route_model=route)
Path(text_path).write_text(result.get("assistant_text") or "", encoding="utf-8")
usage = {k: v for k, v in result.items() if k != "assistant_text"}
Path(usage_path).write_text(json.dumps(usage), encoding="utf-8")
PY

      # Parse COMPLETION block from extracted assistant text (not raw JSON stdout).
      PARSE_SCRIPT="$ORCH_SCRIPTS_DIR/lib/parse-completion.py"
      COMPLETION_JSON=""
      PARSE_EXIT=0
      COMPLETION_JSON=$(python3 "$PARSE_SCRIPT" "$TOOL_TEXT" 2>"$TMP_DIR/parse_stderr") || PARSE_EXIT=$?

      if [ "$PARSE_EXIT" -ne 0 ]; then
        echo "ERROR: Malformed COMPLETION block from tool '$TOOL_BINARY'" >&2
        echo "--- parse-completion.py stderr ---" >&2
        cat "$TMP_DIR/parse_stderr" >&2
        echo "--- Last 50 lines of tool assistant text ---" >&2
        tail -50 "$TOOL_TEXT" >&2
        exit 5
      fi

      # Build done payload from COMPLETION JSON + dispatch context
      DURATION_MS=$(($( _now_ms ) - STEP_START_MS))
      DONE_PAYLOAD=$(python3 "$STATE_INSPECT" build-payload agent \
        --step-id "$STEP_ID" --phase "$PHASE" --agent "$AGENT" \
        --stdout-file "$TOOL_STDOUT" --usage-file "$TOOL_USAGE" \
        --cwd "$AGENT_WORK_DIR" \
        --started-at "$STARTED_AT" --duration-ms "$DURATION_MS" <<<"$COMPLETION_JSON")

      # Workflow-issues detection (retry-success when this attempt > 1).
      WFI_JSON=$(bash "$DETECT_WORKFLOW_ISSUES" \
        --phase "$PHASE" --step-id "$STEP_ID" \
        --attempt "$ATTEMPT" 2>/dev/null || echo "[]")
      DONE_PAYLOAD=$(_merge_workflow_issues "$DONE_PAYLOAD" "$WFI_JSON")

      DONE_STDERR="$TMP_DIR/orch_done_stderr_${STEP_ID}.txt"
      if echo "$DONE_PAYLOAD" | orchestrator done "$STATE_YAML" 2>"$DONE_STDERR"; then
        DONE_STATUS=$(echo "$DONE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','completed'))" 2>/dev/null || echo "completed")
        echo "[$(_log_ts)] ✓ $STEP_ID  done  status=$DONE_STATUS" >&2
        _log_step_usage "$STEP_ID" "$PHASE"
      else
        DONE_EXIT=$?
        # Exit 3 = payload validation failure (bad output shape from agent).
        # Record it as failed so on_failure routing can retry; don't abort.
        if [ "$DONE_EXIT" -eq 3 ]; then
          echo "WARN: orchestrator done rejected payload for $STEP_ID (exit 3) — recording as failed" >&2
          cat "$DONE_STDERR" >&2 2>/dev/null || true
          FALLBACK_PAYLOAD=$(python3 "$STATE_INSPECT" build-payload failed \
            --step-id "$STEP_ID" --phase "$PHASE" --agent "$AGENT" \
            --exit-code "$DONE_EXIT" --duration-ms "$DURATION_MS")
          echo "$FALLBACK_PAYLOAD" | orchestrator done "$STATE_YAML" || true
        else
          echo "ERROR: orchestrator done exited $DONE_EXIT" >&2
          cat "$DONE_STDERR" >&2 2>/dev/null || true
          exit 7
        fi
      fi
      ;;

    *)
      echo "ERROR: Unknown action kind '$KIND'" >&2
      exit 7
      ;;
  esac
done
