# agent-routes.sh — resolve agent subprocess/model with optional runtime overrides.
#
# Precedence (highest wins per field):
#   1. ORCHESTRATOR_AGENT_ROUTE_OVERRIDES JSON (from agent.<role>.<field>= CLI flags)
#   2. ORCHESTRATOR_AGENTS_CONFIG YAML file (agents: block, same as routes.yaml)
#   3. routes YAML (config/agents.yaml, --routes-override, or ORCHESTRATOR_ROUTES_YAML)
#
# CLI examples:
#   orchestrator run ORC-84 --agents-config ./agents.config.yaml
#   orchestrator run ORC-84 agents.config=./agents.config.yaml
#   orchestrator run ORC-84 agent.developer.subprocess=cursor

agent_routes_merge_flag_into_json() {
  local json="$1"
  local flag="$2"
  python3 - "$json" "$flag" <<'PY'
import json, re, sys
data = json.loads(sys.argv[1] or "{}")
flag = sys.argv[2]
m = re.fullmatch(r"agent\.([a-zA-Z0-9_-]+)\.(subprocess|model)=(.+)", flag)
if not m:
    sys.exit(1)
role, field, value = m.group(1), m.group(2), m.group(3)
entry = data.setdefault(role, {})
entry[field] = value
print(json.dumps(data))
PY
}

agent_routes_build_overrides_from_flags() {
  local json="{}"
  local flag
  for flag in "$@"; do
    if [[ "$flag" =~ ^agent\.[a-zA-Z0-9_-]+\.(subprocess|model)= ]]; then
      json=$(agent_routes_merge_flag_into_json "$json" "$flag") || true
    fi
  done
  printf '%s' "$json"
}

agent_routes_resolve_field() {
  local agent="$1"
  local routes_yaml="$2"
  local field="$3"
  local overrides_json="${ORCHESTRATOR_AGENT_ROUTE_OVERRIDES:-"{}"}"
  local agents_config="${ORCHESTRATOR_AGENTS_CONFIG:-}"
  python3 - "$agent" "$routes_yaml" "$field" "$overrides_json" "$agents_config" <<'PY'
import json, os, sys
import yaml

agent, routes_path, field, overrides_raw, agents_config_path = sys.argv[1:6]
overrides = json.loads(overrides_raw or "{}")

def agents_map(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    agents = data.get("agents")
    return agents if isinstance(agents, dict) else {}

entry: dict = {}
entry.update(agents_map(routes_path).get(agent) or {})
entry.update(agents_map(agents_config_path).get(agent) or {})
ov = overrides.get(agent) or {}
value = ov.get(field) or entry.get(field) or ""
print(value)
PY
}

agent_routes_resolve_subprocess() {
  agent_routes_resolve_field "$1" "$2" "subprocess"
}

agent_routes_resolve_model() {
  agent_routes_resolve_field "$1" "$2" "model"
}

agent_routes_abs_path() {
  local p="$1"
  if [ -z "$p" ]; then
    return 1
  fi
  if [ -f "$p" ]; then
    echo "$(cd "$(dirname "$p")" && pwd)/$(basename "$p")"
    return 0
  fi
  return 1
}
