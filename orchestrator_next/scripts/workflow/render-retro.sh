#!/usr/bin/env bash
# render-retro.sh — render archived retro.md issues as a markdown table on stderr.
#
# Usage: render-retro.sh <change_id>
# Env:   WORKTREE_ROOT (default $PWD), REPO_ROOT (default $WORKTREE_ROOT)
#
# Resolves retro.md from archive (or active fallback), parses ## ISSUE-N blocks,
# and prints "## Issues this run (N)" + pipe table. Silent (exit 0, no output)
# when retro.md is missing, has no issues, or python3 is unavailable.

set -uo pipefail

CHANGE_ID="${1:-}"
WORKTREE_ROOT="${WORKTREE_ROOT:-$PWD}"
REPO_ROOT="${REPO_ROOT:-$WORKTREE_ROOT}"

if [[ -z "$CHANGE_ID" ]]; then
  echo "usage: render-retro.sh <change_id>" >&2
  exit 2
fi

_first_legacy_retro() {
  local archive_base="$1"
  local change_id="$2"
  local match
  shopt -s nullglob
  for match in "$archive_base"/*-"$change_id"/retro.md; do
    if [[ -f "$match" ]]; then
      printf '%s\n' "$match"
      return 0
    fi
  done
  return 1
}

_resolve_retro_path() {
  local change_id="$1"
  local candidate

  candidate="$WORKTREE_ROOT/spec/changes/archive/$change_id/retro.md"
  if [[ -f "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  candidate=$(_first_legacy_retro "$WORKTREE_ROOT/spec/changes/archive" "$change_id" || true)
  if [[ -n "${candidate:-}" ]] && [[ -f "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  candidate="$REPO_ROOT/spec/changes/archive/$change_id/retro.md"
  if [[ -f "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  candidate=$(_first_legacy_retro "$REPO_ROOT/spec/changes/archive" "$change_id" || true)
  if [[ -n "${candidate:-}" ]] && [[ -f "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  candidate="$WORKTREE_ROOT/spec/changes/$change_id/retro.md"
  if [[ -f "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  return 1
}

RETRO_PATH=$(_resolve_retro_path "$CHANGE_ID" || true)
if [[ -z "${RETRO_PATH:-}" ]] || [[ ! -f "$RETRO_PATH" ]]; then
  exit 0
fi

issue_count=0
if ! issue_count=$(grep -cE '^## ISSUE-' "$RETRO_PATH" 2>/dev/null); then
  issue_count=0
fi
if [[ "$issue_count" -eq 0 ]]; then
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "render-retro: python3 missing" >&2
  exit 0
fi

python3 - "$RETRO_PATH" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()

field_re = re.compile(r"^(?:- )?\*\*(\w+)\*\*:\s*(.*)$", re.MULTILINE)
issue_heading_re = re.compile(r"^## ISSUE-\d+", re.MULTILINE)

blocks = issue_heading_re.split(text)
issues = []
for block in blocks[1:]:
    fields = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        m = field_re.match(lines[i])
        if m:
            key, val = m.group(1), m.group(2).strip()
            # collect indented continuation lines (multi-line values)
            j = i + 1
            while j < len(lines) and lines[j].startswith("  "):
                val = (val + " " + lines[j].strip()).strip()
                j += 1
            # strip YAML block scalar marker
            if val.startswith(">"):
                val = val[1:].strip()
            fields[key] = val
            i = j
        else:
            i += 1
    issues.append(fields)

if not issues:
    sys.exit(0)


def cell(value: str, *, truncate: bool = False) -> str:
    if not value:
        return "—"
    if truncate and len(value) > 120:
        return value[:120] + "…"
    return value


def emit(*parts: str) -> None:
    print("".join(parts), file=sys.stderr)


emit("\n")
emit(f"## Issues this run ({len(issues)})\n")
emit("| Severity | Category | Detail | Fix direction |\n")
emit("|---|---|---|---|\n")
for fields in issues:
    severity = cell(fields.get("severity", ""))
    category = cell(fields.get("category", ""))
    detail = cell(fields.get("detail") or fields.get("title", ""), truncate=True)
    fix_direction = cell(fields.get("fix_direction", ""), truncate=True)
    emit(f"| {severity} | {category} | {detail} | {fix_direction} |\n")
PY
