#!/usr/bin/env bash
# Manage named tmux sessions ("workspaces") for mobile access over Tailscale.
# Usage:
#   remote-session start <name> [path]   # path defaults to ~/code/<name>
#   remote-session attach <name>
#   remote-session list
#   remote-session kill <name>

set -euo pipefail

CMD="${1:-}"
NAME="${2:-}"
PATH_ARG="${3:-}"

die() { echo "error: $*" >&2; exit 1; }

require_name() {
  [[ -n "$NAME" ]] || die "missing <name>. usage: remote-session $CMD <name>"
}

case "$CMD" in
  start)
    require_name
    if tmux has-session -t "$NAME" 2>/dev/null; then
      echo "session '$NAME' already exists. attach with: remote-session attach $NAME"
      exit 0
    fi
    target="${PATH_ARG:-$HOME/code/$NAME}"
    [[ -d "$target" ]] || die "path '$target' not found. pass an explicit path: remote-session start $NAME <path>"
    tmux new-session -d -s "$NAME" -c "$target"
    echo "started '$NAME' in $target"
    echo "attach: remote-session attach $NAME"
    ;;
  attach)
    require_name
    tmux has-session -t "$NAME" 2>/dev/null || die "no session '$NAME'. start with: remote-session start $NAME"
    exec tmux attach -t "$NAME"
    ;;
  list|ls)
    tmux ls 2>/dev/null || echo "no sessions"
    ;;
  kill)
    require_name
    tmux kill-session -t "$NAME"
    echo "killed '$NAME'"
    ;;
  ""|help|-h|--help)
    cat <<EOF
remote-session — named tmux workspaces for mobile access

  start <name> [path]   create session (path defaults to ~/code/<name>)
  attach <name>         attach to session
  list                  list sessions
  kill <name>           kill session

Phone usage (over Tailscale):
  ssh <mac-tailnet-name>
  remote-session attach <name>
EOF
    ;;
  *)
    die "unknown command '$CMD'. run: remote-session help"
    ;;
esac
