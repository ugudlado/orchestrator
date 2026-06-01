# ticket-common.sh — shared helpers for ticket shell scripts (source, do not execute)
# Resolves repo paths and ticketing backend from spec/project.yaml.

ticket_repo_root() {
  local candidate="${1:-}"
  if [ -n "$candidate" ] && [ -d "$candidate" ]; then
    (cd "$candidate" && pwd)
    return 0
  fi
  if [ -n "${REPO_ROOT:-}" ] && [ -d "$REPO_ROOT" ]; then
    (cd "$REPO_ROOT" && pwd)
    return 0
  fi
  return 1
}

ticket_read_backend() {
  local repo_root="$1"
  local project_yaml="$repo_root/spec/project.yaml"
  local backend="backlog"
  if [ -f "$project_yaml" ]; then
    local from_yaml
    from_yaml=$(grep -E '^ticketing:' "$project_yaml" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' || true)
    if [ -n "$from_yaml" ]; then
      backend="$from_yaml"
    fi
  fi
  case "$backend" in
    backlog|linear) echo "$backend" ;;
    *) echo "backlog" ;;
  esac
}

ticket_resolve_config() {
  local name="$1"
  local repo_root="$2"
  local orch="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
  if [ -f "$repo_root/.orchestrator/config/$name" ]; then
    echo "$repo_root/.orchestrator/config/$name"
  elif [ -f "$repo_root/config/$name" ]; then
    echo "$repo_root/config/$name"
  elif [ -f "$orch/config/$name" ]; then
    echo "$orch/config/$name"
  fi
}
