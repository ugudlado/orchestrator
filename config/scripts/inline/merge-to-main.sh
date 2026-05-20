#!/usr/bin/env bash
# merge-to-main.sh — merge feature branch into default branch (autopilot gate).
#
# Env inputs:  STATE_YAML_PATH, REPO_ROOT
# Outputs:     {merge_record: {merged, skipped, reason?, branch?, default_branch?, merge_sha?}}

set -uo pipefail

STATE="${STATE_YAML_PATH:-}"
REPO_ROOT="${REPO_ROOT:-}"

if [[ -z "$STATE" || ! -f "$STATE" ]]; then
  printf '%s\n' '{"merge_record": {"merged": false, "skipped": true, "reason": "STATE_YAML_PATH missing"}}'
  exit 0
fi

if [[ -z "$REPO_ROOT" ]]; then
  REPO_ROOT="$(git -C "$(dirname "$STATE")" rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [[ -z "$REPO_ROOT" || ! -d "$REPO_ROOT/.git" ]]; then
  printf '%s\n' '{"merge_record": {"merged": false, "skipped": true, "reason": "not a git repo"}}'
  exit 0
fi

# shellcheck source=./_read_state_env.sh
source "$(dirname "$0")/_read_state_env.sh"
read_state_env "$STATE" CHANGE_ID BRANCH

if [[ -z "$BRANCH" ]]; then
  printf '%s\n' '{"merge_record": {"merged": false, "skipped": true, "reason": "no branch in state.yaml"}}'
  exit 0
fi

cd "$REPO_ROOT"

DEFAULT_BRANCH=""
if DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null); then
  DEFAULT_BRANCH="${DEFAULT_BRANCH#refs/remotes/origin/}"
fi
if [[ -z "$DEFAULT_BRANCH" ]]; then
  if git show-ref --verify --quiet refs/heads/main; then
    DEFAULT_BRANCH=main
  elif git show-ref --verify --quiet refs/heads/master; then
    DEFAULT_BRANCH=master
  else
    printf '%s\n' '{"merge_record": {"merged": false, "skipped": true, "reason": "cannot detect default branch"}}'
    exit 0
  fi
fi

if ! git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  printf '%s\n' "{\"merge_record\": {\"merged\": false, \"skipped\": true, \"reason\": \"branch not found: $BRANCH\"}}"
  exit 0
fi

if git branch --merged "$DEFAULT_BRANCH" | sed 's/^[* ] //' | grep -qxF "$BRANCH"; then
  MERGE_SHA="$(git rev-parse "$DEFAULT_BRANCH" 2>/dev/null || echo "")"
  printf '%s\n' "{\"merge_record\": {\"merged\": true, \"skipped\": true, \"reason\": \"already merged\", \"branch\": \"$BRANCH\", \"default_branch\": \"$DEFAULT_BRANCH\", \"merge_sha\": \"$MERGE_SHA\"}}"
  exit 0
fi

if ! git checkout "$DEFAULT_BRANCH" 2>/dev/null; then
  printf '%s\n' "{\"merge_record\": {\"merged\": false, \"skipped\": false, \"reason\": \"checkout $DEFAULT_BRANCH failed\"}}" >&2
  exit 1
fi

MERGE_MSG="merge(autopilot): ${CHANGE_ID:-$BRANCH}"
if ! git merge --no-ff "$BRANCH" -m "$MERGE_MSG" 2>/dev/null; then
  git merge --abort 2>/dev/null || true
  printf '%s\n' "{\"merge_record\": {\"merged\": false, \"skipped\": false, \"reason\": \"merge conflict\", \"branch\": \"$BRANCH\", \"default_branch\": \"$DEFAULT_BRANCH\"}}" >&2
  exit 1
fi

MERGE_SHA="$(git rev-parse HEAD 2>/dev/null || echo "")"
printf '%s\n' "{\"merge_record\": {\"merged\": true, \"skipped\": false, \"branch\": \"$BRANCH\", \"default_branch\": \"$DEFAULT_BRANCH\", \"merge_sha\": \"$MERGE_SHA\"}}"
