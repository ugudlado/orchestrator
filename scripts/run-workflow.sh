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

# Resolve script directory (find siblings like parse-completion.py, cost-report.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ticket-common.sh
source "$SCRIPT_DIR/lib/ticket-common.sh"

# -----------------------------------------------------------------------
# Config resolution: .orchestrator override takes precedence over global
# -----------------------------------------------------------------------
resolve_config() {
  local name="$1"
  local repo_override="$REPO_ROOT/.orchestrator/config/$name"
  local repo_config="$REPO_ROOT/config/$name"
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

TOOLS_YAML=$(resolve_config "tools.yaml")
ROUTES_YAML=$(resolve_config "scripts/routes.yaml")
# Fall back to worktree scripts/routes.yaml
if [ -z "$ROUTES_YAML" ] && [ -f "$SCRIPT_DIR/routes.yaml" ]; then
  ROUTES_YAML="$SCRIPT_DIR/routes.yaml"
fi

# -----------------------------------------------------------------------
# Ticket-driven entry: if TICKET-ID given, check Linear status first
# -----------------------------------------------------------------------
if [ -n "$TICKET_ID" ]; then
  TICKET_CHECK="$SCRIPT_DIR/ticket-status-check.sh"
  if [ ! -f "$TICKET_CHECK" ]; then
    echo "ERROR: ticket-status-check.sh not found at $TICKET_CHECK" >&2
    exit 7
  fi

  TICKET_RESULT=$(bash "$TICKET_CHECK" "$TICKET_ID" "$REPO_ROOT" 2>&1)
  TICKET_ACTION=$(echo "$TICKET_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('action','skip'))" 2>/dev/null || echo "skip")

  case "$TICKET_ACTION" in
    halt)
      TICKET_REASON=$(echo "$TICKET_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reason',''))" 2>/dev/null || echo "")
      echo "ERROR: Ticket check halted: $TICKET_REASON" >&2
      CHECKLIST=$(echo "$TICKET_RESULT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
cl=d.get('checklist',[])
for item in cl: print('  -', item)
" 2>/dev/null || echo "")
      if [ -n "$CHECKLIST" ]; then
        echo "Setup checklist:" >&2
        echo "$CHECKLIST" >&2
      fi
      exit 6
      ;;
    skip)
      # No action needed — proceed with state.yaml
      ;;
    init|resume)
      # Proceed — state.yaml-driven loop handles the rest
      ;;
  esac

  # Persist ticket id + status on state (shell loop owns ticket_* fields)
  _TICKET_BACKEND=$(ticket_read_backend "$REPO_ROOT")
  _TICKET_STATUS=$(bash "$SCRIPT_DIR/ticket-fetch-status.sh" "$TICKET_ID" "$REPO_ROOT" 2>/dev/null || true)
  python3 - "$TICKET_ID" "$_TICKET_BACKEND" "$_TICKET_STATUS" <<'PY' | bash "$SCRIPT_DIR/ticket-state-update.sh" "$STATE_YAML" 2>/dev/null || true
import json, sys
p = {"ticket_id": sys.argv[1], "ticketing": sys.argv[2]}
if sys.argv[3]:
    p["ticket_status"] = sys.argv[3]
print(json.dumps(p))
PY
fi

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

sync_ticket_after_step() {
  local step_id="$1"
  local sync_script="$SCRIPT_DIR/ticket-sync.sh"
  if [ -f "$sync_script" ]; then
    bash "$sync_script" "$STATE_YAML" "$step_id" 2>/dev/null || true
  fi
}

reconcile_ticket_before_next() {
  local reconcile_script="$SCRIPT_DIR/ticket-reconcile.sh"
  if [ ! -f "$reconcile_script" ]; then
    return 0
  fi
  local result
  result=$(bash "$reconcile_script" "$STATE_YAML" 2>/dev/null || echo '{"action":"skip"}')
  local action
  action=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action','skip'))" 2>/dev/null || echo "skip")
  if [ "$action" = "rework" ]; then
    echo "ticket-reconcile: review rework detected — ticket returned to In Progress; flags.rework_from_review set on state.yaml" >&2
  fi
}

# Resolve agent -> tool binary
# Looks up routes.yaml .agents.<agent>.subprocess, falls back to 'claude'
resolve_agent_tool() {
  local agent="$1"
  local subprocess=""

  # Try routes.yaml first
  if [ -n "$ROUTES_YAML" ] && [ -f "$ROUTES_YAML" ]; then
    subprocess=$(yq ".agents.${agent}.subprocess // \"\"" "$ROUTES_YAML" 2>/dev/null | tr -d '"')
  fi

  if [ -n "$subprocess" ]; then
    echo "$subprocess"
    return 0
  fi

  # Unknown agent
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

# Build prompt string from instruction + step_context
build_prompt() {
  local instruction="$1"
  local step_context="$2"
  printf '%s\n\nStep context:\n%s\n\nYou MUST end stdout with a valid COMPLETION: YAML block (see config/steps/contracts/done-payload.md).\n' \
    "$instruction" "$step_context"
}

# Run tool binary using config/tools.yaml args_template ({prompt}, {prompt_file})
invoke_tool() {
  local tool_name="$1"
  local tool_binary="$2"
  local prompt="$3"
  local prompt_file="$4"
  local stdout_path="$5"
  local stderr_path="$6"

  python3 - "$tool_name" "$tool_binary" "$prompt" "$prompt_file" "$stdout_path" "$stderr_path" "$TOOLS_YAML" <<'PY'
import json, subprocess, sys
from pathlib import Path
import yaml

tool_name, binary, prompt, prompt_file, stdout_path, stderr_path, tools_path = sys.argv[1:8]
template = []
if tools_path and Path(tools_path).is_file():
    with open(tools_path) as f:
        cfg = yaml.safe_load(f) or {}
    template = (cfg.get("tools") or {}).get(tool_name, {}).get("args_template") or []

argv = [binary]
for arg in template:
    if arg == "{prompt}":
        argv.append(prompt)
    elif arg == "{prompt_file}":
        argv.append(prompt_file)
    else:
        argv.append(str(arg))

if len(argv) == 1:
    if tool_name == "claude":
        argv.extend(["-p", prompt])
    elif tool_name == "pi":
        argv.extend(["run", "--prompt-file", prompt_file])
    else:
        argv.append(prompt)

with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
    proc = subprocess.run(argv, stdout=out, stderr=err)
sys.exit(proc.returncode)
PY
}

# -----------------------------------------------------------------------
# Main dispatch loop
# -----------------------------------------------------------------------
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

while true; do
  # Poll ticket lane before each dispatch (reviewer may have moved ticket back)
  reconcile_ticket_before_next

  # Call orchestrator next
  ACTION_JSON=$(orchestrator next "$STATE_YAML" 2>/tmp/orch_next_stderr) || {
    EXIT_CODE=$?
    case "$EXIT_CODE" in
      1)
        # complete_workflow
        echo "Workflow complete." >&2
        # Emit cost report if available
        COST_REPORT_SH=$(find "$SCRIPT_DIR" "$REPO_ROOT/scripts" "$REPO_ROOT/config/scripts" \
          -maxdepth 2 -name "cost-report.sh" 2>/dev/null | head -1 || true)
        if [ -n "$COST_REPORT_SH" ] && [ -f "$COST_REPORT_SH" ]; then
          CHANGE_ID=$(python3 -c "
import yaml, sys
with open('$STATE_YAML') as f: d=yaml.safe_load(f)
print(d.get('change_id',''))
" 2>/dev/null || echo "")
          if [ -n "$CHANGE_ID" ]; then
            bash "$COST_REPORT_SH" --change-id "$CHANGE_ID" 2>/dev/null || true
          fi
        fi
        exit 1
        ;;
      2)
        echo "Workflow blocked." >&2
        cat /tmp/orch_next_stderr >&2 2>/dev/null || true
        exit 2
        ;;
      3)
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

  # Extract fields from action JSON
  STEP_ID=$(echo "$ACTION_JSON" | jq -r '.step_id // empty')
  PHASE=$(echo "$ACTION_JSON" | jq -r '.phase // "main"')
  KIND=$(echo "$ACTION_JSON" | jq -r '.kind // "run_inline"')
  AGENT=$(echo "$ACTION_JSON" | jq -r '.agent // "developer"')
  ATTEMPT=$(echo "$ACTION_JSON" | jq -r '.attempt // 1')
  STARTED_AT=$(echo "$ACTION_JSON" | jq -r '.started_at // empty')

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
        ENV_ARGS=$(echo "$ENV_BLOCK" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.items():
    print(f'{k}={v}')
" 2>/dev/null | tr '\n' ' ')
      fi

      SCRIPT_EXIT=0
      if [ -n "$ENV_ARGS" ]; then
        env $ENV_ARGS bash "$SCRIPT_PATH" >"$TMP_DIR/script_stdout" 2>"$TMP_DIR/script_stderr" || SCRIPT_EXIT=$?
      else
        bash "$SCRIPT_PATH" >"$TMP_DIR/script_stdout" 2>"$TMP_DIR/script_stderr" || SCRIPT_EXIT=$?
      fi

      if [ "$SCRIPT_EXIT" -eq 0 ]; then
        STATUS="completed"
      else
        STATUS="failed"
      fi

      DONE_PAYLOAD=$(python3 -c "
import json, sys
payload = {
    'step_id': '$STEP_ID',
    'phase': '$PHASE',
    'status': '$STATUS',
    'outputs': {},
    'usage': {'input_tokens': 0, 'output_tokens': 0, 'model': 'none'},
}
if '$STARTED_AT':
    payload['started_at'] = '$STARTED_AT'
print(json.dumps(payload))
")
      if echo "$DONE_PAYLOAD" | orchestrator done "$STATE_YAML"; then
        if [ "$STATUS" = "completed" ]; then
          sync_ticket_after_step "$STEP_ID"
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

      # Build the prompt
      PROMPT=$(build_prompt "$INSTRUCTION" "$STEP_CONTEXT")
      PROMPT_FILE="$TMP_DIR/prompt_${STEP_ID}.txt"
      echo "$PROMPT" > "$PROMPT_FILE"

      TOOL_EXIT=0
      TOOL_STDOUT="$TMP_DIR/tool_stdout_${STEP_ID}.txt"
      invoke_tool "$TOOL_NAME" "$TOOL_BINARY" "$PROMPT" "$PROMPT_FILE" \
        "$TOOL_STDOUT" "$TMP_DIR/tool_stderr_${STEP_ID}.txt" || TOOL_EXIT=$?

      if [ "$TOOL_EXIT" -ne 0 ]; then
        echo "WARN: tool '$TOOL_BINARY' exited $TOOL_EXIT" >&2
        # Record failure via orchestrator done
        DONE_PAYLOAD=$(python3 -c "
import json
payload = {
    'step_id': '$STEP_ID',
    'phase': '$PHASE',
    'status': 'failed',
    'agent': '$AGENT',
    'outputs': {'task_execution_result': {'status': 'failed', 'exit_code': $TOOL_EXIT}},
    'usage': {'input_tokens': 0, 'output_tokens': 0, 'model': 'none'},
}
print(json.dumps(payload))
")
        echo "$DONE_PAYLOAD" | orchestrator done "$STATE_YAML" || true
        continue
      fi

      # Parse COMPLETION block from tool stdout
      PARSE_SCRIPT="$SCRIPT_DIR/parse-completion.py"
      COMPLETION_JSON=""
      PARSE_EXIT=0
      COMPLETION_JSON=$(python3 "$PARSE_SCRIPT" "$TOOL_STDOUT" 2>"$TMP_DIR/parse_stderr") || PARSE_EXIT=$?

      if [ "$PARSE_EXIT" -ne 0 ]; then
        echo "ERROR: Malformed COMPLETION block from tool '$TOOL_BINARY'" >&2
        echo "--- parse-completion.py stderr ---" >&2
        cat "$TMP_DIR/parse_stderr" >&2
        echo "--- Last 50 lines of tool stdout ---" >&2
        tail -50 "$TOOL_STDOUT" >&2
        exit 5
      fi

      # Build done payload from COMPLETION JSON + dispatch context
      DONE_PAYLOAD=$(echo "$COMPLETION_JSON" | python3 -c "
import sys, json
completion = json.load(sys.stdin)
payload = dict(completion)
payload['step_id'] = '$STEP_ID'
payload['phase'] = '$PHASE'
payload['agent'] = '$AGENT'
if not payload.get('usage'):
    payload['usage'] = {'input_tokens': 0, 'output_tokens': 0, 'model': 'none'}
if '$STARTED_AT':
    payload['started_at'] = '$STARTED_AT'
print(json.dumps(payload))
")

      if echo "$DONE_PAYLOAD" | orchestrator done "$STATE_YAML"; then
        DONE_STATUS=$(echo "$DONE_PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','completed'))" 2>/dev/null || echo "completed")
        if [ "$DONE_STATUS" = "completed" ] || [ "$DONE_STATUS" = "recovered" ]; then
          sync_ticket_after_step "$STEP_ID"
        fi
      else
        DONE_EXIT=$?
        echo "ERROR: orchestrator done exited $DONE_EXIT" >&2
        cat /tmp/orch_done_stderr 2>/dev/null || true
        exit 7
      fi
      ;;

    *)
      echo "ERROR: Unknown action kind '$KIND'" >&2
      exit 7
      ;;
  esac
done
