#!/usr/bin/env bash
# validate-tasks-yaml.sh — validate a tasks.yaml file against the Tasks YAML
# Format Contract (skills/architect/prompt.md § Tasks YAML Format Contract).
#
# Usage: validate-tasks-yaml.sh <path-to-tasks.yaml>
# Exit 0: file is well-formed.
# Exit 1: validation error (diagnostic on stderr).

set -uo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: validate-tasks-yaml.sh <path-to-tasks.yaml>" >&2
  exit 1
fi

TASKS_YAML="$1"

if [[ ! -f "$TASKS_YAML" ]]; then
  echo "Error: file not found: $TASKS_YAML" >&2
  exit 1
fi

python3 - "$TASKS_YAML" <<'PYEOF'
import sys
import yaml

path = sys.argv[1]

try:
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
except yaml.YAMLError as e:
    print(f"Error: invalid YAML in {path}: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(doc, dict):
    print(f"Error: tasks.yaml must be a YAML mapping, got {type(doc).__name__}", file=sys.stderr)
    sys.exit(1)

# Check version
if "version" not in doc:
    print("Error: missing required top-level field 'version'", file=sys.stderr)
    sys.exit(1)

# Check tasks list
tasks = doc.get("tasks")
if not isinstance(tasks, list):
    print("Error: 'tasks' must be a list", file=sys.stderr)
    sys.exit(1)

if len(tasks) == 0:
    print("Error: 'tasks' list is empty", file=sys.stderr)
    sys.exit(1)

REQUIRED_FIELDS = ("id", "title", "files", "verify")

seen_ids = set()
errors = []

for i, task in enumerate(tasks):
    if not isinstance(task, dict):
        errors.append(f"Task at index {i} is not a mapping")
        continue

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in task:
            task_id = task.get("id", f"<index {i}>")
            errors.append(f"Task '{task_id}' missing required field '{field}'")

    # Check duplicate ids
    task_id = task.get("id")
    if task_id is not None:
        if task_id in seen_ids:
            errors.append(f"Duplicate task id '{task_id}'")
        else:
            seen_ids.add(task_id)

if errors:
    for e in errors:
        print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)

# Check unknown depends_on references (after collecting all ids)
for task in tasks:
    if not isinstance(task, dict):
        continue
    deps = task.get("depends_on")
    if deps is None:
        continue
    if not isinstance(deps, list):
        errors.append(f"Task '{task.get('id')}' depends_on must be a list")
        continue
    for dep in deps:
        if dep not in seen_ids:
            errors.append(
                f"Task '{task.get('id')}' depends_on unknown id '{dep}'"
            )

# Check reviews shape when present
for task in tasks:
    if not isinstance(task, dict):
        continue
    reviews = task.get("reviews")
    if reviews is None:
        continue
    task_id = task.get("id", "<unknown>")
    if not isinstance(reviews, list):
        errors.append(f"Task '{task_id}' reviews must be a list")
        continue
    for j, entry in enumerate(reviews):
        if not isinstance(entry, dict):
            errors.append(f"Task '{task_id}' reviews[{j}] must be a mapping")
            continue
        for field in ("at", "comment"):
            if field not in entry or not entry[field]:
                errors.append(
                    f"Task '{task_id}' reviews[{j}] missing required field '{field}'"
                )

if errors:
    for e in errors:
        print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {path} is valid ({len(tasks)} tasks)")
sys.exit(0)
PYEOF
