#!/usr/bin/env bash
# eval.sh — deterministic gate for design-review: structural checks that must
# pass before the LLM rubric even runs. Exit code is the evidence.
#
# Usage: eval.sh <design.md> <tasks.yaml>
# Exit 0: all checks pass.
# Exit 1: a required section/field is missing (diagnostics on stderr).

set -uo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: eval.sh <design.md> <tasks.yaml>" >&2
  exit 1
fi

DESIGN_MD="$1"
TASKS_YAML="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
errors=0

if [[ ! -f "$DESIGN_MD" ]]; then
  echo "Error: file not found: $DESIGN_MD" >&2
  exit 1
fi

# Design Format Contract — required sections (skills/design/prompt.md § Design Format Contract)
REQUIRED_SECTIONS=(
  "## Context"
  "## Goals / Non-Goals"
  "## Approaches Considered"
  "### Selected Approach"
  "## Constraints"
  "## Trade-offs"
  "## Acceptance Criteria"
  "## Open Questions"
)

for section in "${REQUIRED_SECTIONS[@]}"; do
  if ! grep -qF "$section" "$DESIGN_MD"; then
    echo "Error: $DESIGN_MD missing required section: $section" >&2
    errors=$((errors + 1))
  fi
done

if ! grep -qE '^feature-id:' "$DESIGN_MD" || ! grep -qE '^linear-ticket:' "$DESIGN_MD"; then
  echo "Error: $DESIGN_MD missing frontmatter feature-id/linear-ticket" >&2
  errors=$((errors + 1))
fi

# [traces: UC-N] check is conditional on discovery.md existing next to design.md —
# bugfix schema runs diagnose instead of explore, so discovery.md is legitimately
# absent there. Unconditional grep would fail every bugfix run.
DESIGN_DIR="$(dirname "$DESIGN_MD")"
if [[ -f "$DESIGN_DIR/discovery.md" ]]; then
  if ! grep -qE '\[traces: UC-[0-9]+' "$DESIGN_MD"; then
    echo "Error: $DESIGN_MD has discovery.md but no AC traces [traces: UC-N]" >&2
    errors=$((errors + 1))
  fi
fi

if ! bash "$SCRIPT_DIR/../../design/pack/validate-tasks-yaml.sh" "$TASKS_YAML"; then
  errors=$((errors + 1))
fi

if [[ $errors -gt 0 ]]; then
  echo "eval.sh: $errors check(s) failed" >&2
  exit 1
fi

echo "OK: $DESIGN_MD and $TASKS_YAML pass structural checks"
exit 0
