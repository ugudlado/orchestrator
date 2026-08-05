#!/usr/bin/env bash
# Repo-local install (no shell-profile edits):
#   1. Symlink CLI → ~/.local/bin/orchestrator
#   2. Vendor workflows pack into this repo's config/
#   3. Ensure skills/operator (workflow creator) is present in-repo
#   4. doctor
#
# Usage:
#   ./install.sh                 # full install
#   ./install.sh --skip-doctor
#   ./install.sh --refresh-config  # re-copy workflows pack into config/
set -euo pipefail

ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_INSTALL_BIN="${ORCHESTRATOR_INSTALL_BIN:-${HOME}/.local/bin}"
WORKFLOW_CONFIG_GIT_URL="${WORKFLOW_CONFIG_GIT_URL:-https://github.com/ugudlado/workflows.git}"
PACK_NAME="${ORCHESTRATOR_PACK_NAME:-workflows}"

SKIP_DOCTOR=0
REFRESH_CONFIG=0

die() {
  echo "error: $*" >&2
  exit 1
}

safe_ln() {
  local src="$1"
  local dst="$2"
  [ -e "$src" ] || return 0
  mkdir -p "$(dirname "$dst")"
  if [ ! -L "$dst" ] || [ "$(readlink "$dst")" != "$src" ]; then
    ln -sfn "$src" "$dst"
    echo "  linked ${dst#$HOME/} -> $src"
  fi
}

setup_python_deps() {
  if ! command -v python3 >/dev/null 2>&1; then
    die "python3 is required but not found on PATH"
  fi
  if command -v uv >/dev/null 2>&1 && [ -f "$ORCHESTRATOR_DIR/pyproject.toml" ]; then
    echo "Installing Python dependencies via uv..."
    (cd "$ORCHESTRATOR_DIR" && uv sync --extra dev 2>/dev/null) \
      || (cd "$ORCHESTRATOR_DIR" && uv sync)
  elif ! python3 -c "import yaml, pydantic" 2>/dev/null; then
    echo "Installing Python dependencies (pyyaml pydantic)..."
    pip install --user pyyaml pydantic
  fi
}

setup_cli() {
  echo "Installing orchestrator CLI → $ORCHESTRATOR_INSTALL_BIN ..."
  local cli_src="$ORCHESTRATOR_DIR/bin/orchestrator"
  [ -f "$cli_src" ] || die "$cli_src not found"
  chmod +x "$cli_src"
  mkdir -p "$ORCHESTRATOR_INSTALL_BIN"
  safe_ln "$cli_src" "$ORCHESTRATOR_INSTALL_BIN/orchestrator"
  if command -v orchestrator >/dev/null 2>&1; then
    echo "  on PATH: $(command -v orchestrator)"
  else
    echo "  note: $ORCHESTRATOR_INSTALL_BIN is not on PATH yet"
    echo "        add it in your shell config, or call: $ORCHESTRATOR_INSTALL_BIN/orchestrator"
  fi
}

resolve_workflow_config_source() {
  local sibling="${ORCHESTRATOR_DIR%/*}/workflows"
  if [ -d "$sibling/config/workflows" ]; then
    echo "$(cd "$sibling" && pwd -P)/config"
    return 0
  fi
  # Back-compat: old local checkout name
  sibling="${ORCHESTRATOR_DIR%/*}/workflow-config"
  if [ -d "$sibling/config/workflows" ]; then
    echo "$(cd "$sibling" && pwd -P)/config"
    return 0
  fi
  return 1
}

# Vendor workflows/steps into this repo.
# Primary layout: .orchestrator/<pack>/ (multi-pack friendly).
# Checkout config/ is a symlink to that pack (single tree, no duplicate).
vendor_config_from() {
  local src="$1"
  local pack_dest="$ORCHESTRATOR_DIR/.orchestrator/$PACK_NAME"
  local config_dest="$ORCHESTRATOR_DIR/config"

  [ -d "$src/workflows" ] || die "source missing workflows/: $src"

  echo "  → .orchestrator/$PACK_NAME/"
  mkdir -p "$pack_dest"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude 'runs/cache/' \
      --exclude 'runs/*/' \
      "$src"/ "$pack_dest"/
  else
    rm -rf "$pack_dest"
    mkdir -p "$pack_dest"
    cp -R "$src"/. "$pack_dest"/
  fi

  echo "  → config/ → .orchestrator/$PACK_NAME"
  if [ -L "$config_dest" ] || [ -e "$config_dest" ]; then
    rm -rf "$config_dest"
  fi
  ln -sfn ".orchestrator/$PACK_NAME" "$config_dest"
}

setup_repo_config() {
  echo "Vendoring workflows pack into this repo..."
  local src=""

  if [ "$REFRESH_CONFIG" -eq 0 ] \
    && [ -d "$ORCHESTRATOR_DIR/.orchestrator/$PACK_NAME/workflows" ] \
    && { [ -L "$ORCHESTRATOR_DIR/config" ] || [ -d "$ORCHESTRATOR_DIR/config/workflows" ]; }; then
    echo "  already vendored (pass --refresh-config to re-copy)"
    return 0
  fi

  if src="$(resolve_workflow_config_source)"; then
    echo "  source: $src (sibling checkout)"
  else
    local tmp
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/orchestrator-wf-config.XXXXXX")"
    echo "  cloning $WORKFLOW_CONFIG_GIT_URL ..."
    git clone --depth 1 "$WORKFLOW_CONFIG_GIT_URL" "$tmp/workflows"
    src="$tmp/workflows/config"
    trap 'rm -rf "$tmp"' RETURN
  fi

  vendor_config_from "$src"
  echo "  pack: .orchestrator/$PACK_NAME"
  echo "  checkout config/: $ORCHESTRATOR_DIR/config"
}

ensure_operator_skill() {
  echo "Checking in-repo workflow creator skill..."
  local src="$ORCHESTRATOR_DIR/skills/operator/SKILL.md"
  if [ -f "$src" ]; then
    echo "  present: skills/operator/"
  else
    die "missing skills/operator/SKILL.md — keep the workflow creator skill in this repo"
  fi
}

run_doctor() {
  if [ "$SKIP_DOCTOR" -eq 1 ]; then
    echo "Skipping doctor (--skip-doctor)"
    return 0
  fi
  echo "Running doctor..."
  local rc=0
  (
    cd "$ORCHESTRATOR_DIR"
    # Prefer the vendored pack when present; else checkout config/.
    unset ORCHESTRATOR_CONFIG || true
    if [ -d "$ORCHESTRATOR_DIR/.orchestrator/$PACK_NAME/workflows" ]; then
      export ORCHESTRATOR_CONFIG="$ORCHESTRATOR_DIR/.orchestrator/$PACK_NAME"
    fi
    export PYTHONPATH="${ORCHESTRATOR_DIR}${PYTHONPATH:+:$PYTHONPATH}"
    if command -v orchestrator >/dev/null 2>&1; then
      orchestrator doctor
    else
      "$ORCHESTRATOR_INSTALL_BIN/orchestrator" doctor
    fi
  ) || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  doctor exit $rc — install finished; fix any FAIL rows before running workflows"
  fi
  return 0
}

print_done() {
  echo
  echo "Install complete (repo-local)."
  echo "  CLI:    $(command -v orchestrator 2>/dev/null || echo "$ORCHESTRATOR_INSTALL_BIN/orchestrator")"
  echo "  Config: $ORCHESTRATOR_DIR/.orchestrator/$PACK_NAME  (config/ → pack)"
  echo "  Skill:  $ORCHESTRATOR_DIR/skills/operator"
  echo
  echo "Next:"
  echo "  orchestrator doctor"
  echo "  # workflow creator lives in-repo — open this repo in an agent and use /operator"
  echo "  orchestrator feature TICKET-1"
  echo "  # or: orchestrator $PACK_NAME/feature TICKET-1"
}

main() {
  echo "Installing orchestrator (repo-local)..."
  setup_python_deps
  setup_cli
  setup_repo_config
  ensure_operator_skill
  run_doctor
  print_done
}

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-doctor) SKIP_DOCTOR=1; shift ;;
    --refresh-config) REFRESH_CONFIG=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      die "unknown argument: $1 (try --help)"
      ;;
  esac
done

main
