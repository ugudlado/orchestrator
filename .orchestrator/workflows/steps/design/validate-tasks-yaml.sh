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

# --- Parallel-safety check (advisory only — see reference/parallel-safety.md) ---
# Two tasks that have no depends_on path between them (in either direction) may
# be dispatched in the same parallel batch. If their `files` lists overlap,
# that is a same-file race the engine cannot see or arbitrate: it serializes
# shared singletons (git index, tasks.yaml itself) but has no notion of which
# byte ranges within a file two tasks intend to touch. This never fails the
# build — it is a warning, not a rule the validator enforces — because the fix
# is a design decision (add depends_on, merge the tasks, or split the file),
# not something to be autofixed here.
by_id = {t["id"]: t for t in tasks if isinstance(t, dict) and t.get("id")}

ancestors: dict[str, set] = {}

def _ancestors(task_id, _stack=None):
    if task_id in ancestors:
        return ancestors[task_id]
    _stack = _stack or set()
    if task_id in _stack:
        return set()  # cycle guard; cycles are already reported above
    _stack = _stack | {task_id}
    result: set = set()
    for dep in by_id.get(task_id, {}).get("depends_on") or []:
        if dep in by_id:
            result.add(dep)
            result |= _ancestors(dep, _stack)
    ancestors[task_id] = result
    return result

for tid in by_id:
    _ancestors(tid)

warnings = []
ids = list(by_id)
for i, a in enumerate(ids):
    for b in ids[i + 1:]:
        if a in ancestors.get(b, set()) or b in ancestors.get(a, set()):
            continue  # ordered — can never land in the same batch
        files_a = set(by_id[a].get("files") or [])
        files_b = set(by_id[b].get("files") or [])
        shared = files_a & files_b
        if shared:
            warnings.append(
                f"'{a}' and '{b}' have no depends_on edge between them but "
                f"both list {sorted(shared)} — if these are ever dispatched "
                f"in the same parallel batch, last write wins. Add a "
                f"depends_on edge, merge the tasks, or split the file."
            )

if warnings:
    for w in warnings:
        print(f"Warning (parallel-safety): {w}", file=sys.stderr)

print(f"OK: {path} is valid ({len(tasks)} tasks)")
if warnings:
    print(f"  {len(warnings)} parallel-safety warning(s) — see stderr", file=sys.stderr)
sys.exit(0)
PYEOF
