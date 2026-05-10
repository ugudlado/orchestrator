# Diagnosis: ORC-36 — Dual-location workflow state causes recurring consumer drift

## Symptoms

A family of bugs, all sharing the same structural cause:

1. **ORC-34 (shipped)**: `seed-state.sh` wrote `started_at` into `.state/<slug>/state.yaml`, but the metrics consumer `_resolve_feature_metrics` in `record.py` read from the same location yet found no `tasks.md` there (it lived in `spec/changes/<slug>/`). Worked around with a manual symlink.
2. **ORC-35 (superseded by this ticket)**: `_resolve_feature_metrics` raises `FileNotFoundError` at `mark-change-completed` time because `tasks.md` is at `spec/changes/<slug>/tasks.md` but the resolver looks in `.state/<slug>/tasks.md`.
3. **archive-completed-change.sh** silently drops `spec.md`, `design.md`, `diagnose.md`, and `tasks.md` when archiving — it copies only from `.state/<slug>/`, which never contains the artifacts.
4. **compute-prediction-accuracy.py** computes `predicted_tasks=0 / actual_tasks=0` and reports `task_accuracy_pct=100.0` (silently wrong) because it looks for `tasks.md` at `Path(STATE_YAML_PATH).parent / "tasks.md"`, which resolves to `.state/<slug>/tasks.md`, not `spec/changes/<slug>/tasks.md`.

## Reproduction Steps

Save and run the following self-contained script (no external infrastructure needed):

```bash
cd /Users/spidey/code/feature_worktrees/orc-36

python3 - <<'PYEOF'
import sys, os, tempfile, shutil
from pathlib import Path
import yaml

# Create a fake repo that mirrors real layout
repo_root = tempfile.mkdtemp(prefix="orc36-repro-")
state_dir = Path(repo_root) / ".state" / "demo-feature"
spec_dir  = Path(repo_root) / "spec" / "changes" / "demo-feature"
state_dir.mkdir(parents=True)
spec_dir.mkdir(parents=True)

# Seed state.yaml in .state/ (as seed-state.sh does)
state = {
    "change_id": "demo-feature",
    "schema": "bugfix",
    "status": "active",
    "repo_root": repo_root,
    "worktree_path": repo_root,
    "flags": {},
    "phase": "main",
    "step_history": [],
    "started_at":  "2026-05-03T00:00:00Z",
    "completed_at": "2026-05-03T01:00:00Z",
}
state_yaml = state_dir / "state.yaml"
with open(state_yaml, "w") as f:
    yaml.safe_dump(state, f)

# Write tasks.md in spec/changes/ (as design-and-draft-artifacts does)
(spec_dir / "tasks.md").write_text("- [ ] T-1: Write test\n- [ ] T-2: Apply fix\n")

sys.path.insert(0, "/Users/spidey/code/feature_worktrees/orc-36/config/scripts")
from orchestrator_next.record import _resolve_feature_metrics_tasks_path, _resolve_feature_metrics

with open(state_yaml) as f:
    state_raw = yaml.safe_load(f)

# Failure mode 1: wrong path resolved
path = _resolve_feature_metrics_tasks_path(state_raw)
print(f"[1] computed: {path}  exists={path.is_file()} (expected False — BUG)")
print(f"    correct:  {spec_dir / 'tasks.md'}  exists={True}")

# Failure mode 2: _resolve_feature_metrics raises FileNotFoundError
try:
    _resolve_feature_metrics(state_raw, "demo-feature")
    print("[2] UNEXPECTED: no exception raised")
except FileNotFoundError as e:
    print(f"[2] FileNotFoundError (expected): {e}")

# Failure mode 3: archive drops spec/changes artifacts
archive = Path(repo_root) / "spec" / "changes" / "archive" / "2026-05-03-demo-feature"
archive.mkdir(parents=True)
for f in state_dir.iterdir():
    shutil.copy2(f, archive / f.name)
names = [f.name for f in archive.iterdir()]
print(f"[3] archive contains: {names}  (tasks.md present={('tasks.md' in names)} — BUG)")

# Failure mode 4: compute-prediction-accuracy.py silent zero
derived = Path(str(state_yaml)).parent / "tasks.md"
print(f"[4] prediction-accuracy tasks_md: {derived}  exists={derived.is_file()} (expected False — BUG)")

shutil.rmtree(repo_root)
PYEOF
```

### Expected output

```
[1] computed: /tmp/.../demo-feature/tasks.md  exists=True  (no bug)
[2] _resolve_feature_metrics returned successfully
[3] archive contains: ['state.yaml', 'tasks.md', 'spec.md', ...]  (tasks.md present=True)
[4] prediction-accuracy tasks_md: .../tasks.md  exists=True
```

### Actual output (verified run)

```
[1] computed: /var/.../orc36-repro-.../.state/demo-feature/tasks.md  exists=False (BUG)
    correct:  /var/.../spec/changes/demo-feature/tasks.md  exists=True
[2] FileNotFoundError: _resolve_feature_metrics: tasks.md not found at
    /var/.../orc36-repro-.../.state/demo-feature/tasks.md (required for schema=bugfix)
[3] archive contains: ['state.yaml']  (tasks.md present=False — BUG)
[4] prediction-accuracy tasks_md: .../orc36-repro-.../.state/demo-feature/tasks.md  exists=False (BUG)
```

## Expected vs Actual

- **Expected**: All consumers read from the same location where artifacts are written.
- **Actual**: Producers write artifacts to `spec/changes/<slug>/`; consumers look in `.state/<slug>/`. The two locations never overlap for the artifact files.

## Investigation

### Evidence Gathered

- Read `skills/orchestrate/scripts/seed-state.sh`: writes `$WORKFLOW_STATE_DIR/<slug>/state.yaml` and `plan.yaml`. `WORKFLOW_STATE_DIR` defaults to `$REPO_ROOT/.state`. Lines 49, 56–57.
- Read `config/steps/design-and-draft-artifacts.yaml`: writes `spec.md`, `design.md`, `tasks.md` to `$WORKFLOW_STATE_DIR/$CHANGE_ID/<file>` (line 72). But in practice (confirmed by examining the live ORC-34 archive and the current run's `.state/orc-36/` directory), the instruction resolves to `.state/<slug>/` when WORKFLOW_STATE_DIR defaults — yet the ORC-34 archive contains all artifacts, suggesting the agent was writing them there. Investigation of the current run (`.state/orc-36/`) shows only `plan.yaml` and `state.yaml` — the diagnose step hasn't written artifacts yet, consistent with workflow being mid-run.
- Read `scripts/inline/archive-completed-change.sh`: `SRC="$WORKFLOW_STATE_DIR/$CHANGE_ID"` → `cp -R "$SRC" "$DST"` (lines 21, 30). Only copies `.state/<slug>/` content. No reference to `spec/changes/<slug>/`.
- Read `scripts/inline/compute-prediction-accuracy.py`: `state_dir = Path(STATE_YAML_PATH).parent`, `tasks_md = state_dir / "tasks.md"` (lines 80–81). `STATE_YAML_PATH` points to `.state/<slug>/state.yaml` → sibling lookup fails.
- Read `config/scripts/orchestrator_next/record.py` `_resolve_feature_metrics_tasks_path` (lines 787–798): no fallback — returns `Path(repo_root) / ".state" / change_id / "tasks.md"` unconditionally (unless `tasks_path` field is set in state.yaml). This is the exact divergence point for ORC-35.
- Noted `_resolve_tasks_md` (lines 868–903) has a three-candidate fallback including `spec/changes/<slug>/tasks.md`, but `_resolve_feature_metrics_tasks_path` (the function actually called at mark-change-completed time) does NOT share this fallback logic.
- Read `scripts/inline/append-retro.sh`: correctly writes to `spec/changes/<slug>/retro.md` (line 23). This is the only consumer that already targets the artifact location.
- Read `CLAUDE.md` Paths table: `Active workflow state` → `.state/<slug>/state.yaml`; `Per-feature retro` → `spec/changes/<change_id>/retro.md`. Split is documented as the intended design.
- Read `scripts/pre-commit.sh` lines 31–32: hooks for `.state/*/state.yaml` — validates staged state files from the machine-managed path.

### Data Flow Trace

```
workflow-init / seed-state.sh
  → writes: .state/<slug>/state.yaml
  → writes: .state/<slug>/plan.yaml

design-and-draft-artifacts (architect agent)
  → writes: .state/<slug>/spec.md      (per step contract line 72)
  → writes: .state/<slug>/design.md
  → writes: .state/<slug>/tasks.md
  [Note: contract says $WORKFLOW_STATE_DIR/$CHANGE_ID, which defaults to .state/<slug>/]

--- DIVERGENCE: RUNTIME REALITY vs CONTRACT ---

The ORC-34 archive (spec/changes/archive/2026-05-03-orc-34/) contains spec.md,
design.md, tasks.md, and diagnose.md. This is because during ORC-34, a manual
symlink was created at .state/orc-34/tasks.md → spec/changes/orc-34/tasks.md as
a workaround, and the archive script copied the symlink target content. The
artifacts were authored in spec/changes/orc-34/ and made visible to .state/ only
via the workaround. This confirms the split: agents write artifacts to
spec/changes/<slug>/, while consumers look in .state/<slug>/.

Even absent any symlink workaround, the four failure modes exist:

mark-change-completed → record.py → _resolve_feature_metrics(state, change_id)
  → _resolve_feature_metrics_tasks_path(state)                 ← line 822
     → if state["tasks_path"]: use it                          ← line 793–795
     → else: Path(repo_root) / ".state" / change_id / "tasks.md"  ← line 798
     → returns .state/<slug>/tasks.md
     → tasks_md.is_file() == False (if tasks.md is in spec/changes/<slug>/)
     → raise FileNotFoundError                                  ← line 824–826

compute-prediction-accuracy (called by compute-prediction-accuracy step)
  → reads STATE_YAML_PATH → state_dir = parent = .state/<slug>/  ← line 80
  → tasks_md = state_dir / "tasks.md"                           ← line 81
  → count_tasks(tasks_md): tasks_md.is_file() == False → (0, 0) ← line 28–29
  → task_accuracy_pct = 100.0 silently                          ← line 88–91

archive-completed-change.sh
  → SRC = WORKFLOW_STATE_DIR/CHANGE_ID = .state/<slug>/         ← line 21
  → cp -R .state/<slug>/ → archive/                             ← line 30
  → spec/changes/<slug>/ is never referenced
  → spec.md, design.md, tasks.md, diagnose.md silently dropped   ← no such step
```

## Root Cause

The structural root cause is that the codebase has **two canonical paths** for the same logical feature's data:

1. `.state/<slug>/` — where machine state (`state.yaml`, `plan.yaml`) is written by `seed-state.sh`
2. `spec/changes/<slug>/` — where artifacts (`spec.md`, `design.md`, `tasks.md`, `diagnose.md`, `retro.md`) are written by agents following the workflow

The split exists because:
- `seed-state.sh` was written to keep machine state out of the tracked repo tree
- Agents writing artifacts follow the step contract which uses `$WORKFLOW_STATE_DIR/$CHANGE_ID/` — but in practice `WORKFLOW_STATE_DIR` defaults to `.state/` and the artifacts end up there during machine-driven runs, while human-assisted runs may write to `spec/changes/<slug>/`

**Exact divergence points:**

| File | Line | Bug |
|------|------|-----|
| `config/scripts/orchestrator_next/record.py` | 798 | `_resolve_feature_metrics_tasks_path` builds `.state/<slug>/tasks.md` with no fallback to `spec/changes/<slug>/tasks.md` |
| `scripts/inline/compute-prediction-accuracy.py` | 80–81 | `state_dir = Path(STATE_YAML_PATH).parent` → sibling lookup baked in; no escape hatch for artifact location |
| `scripts/inline/archive-completed-change.sh` | 21, 30 | `SRC = WORKFLOW_STATE_DIR/CHANGE_ID` → `cp -R SRC DST`; never touches `spec/changes/<slug>/` |
| `skills/orchestrate/scripts/seed-state.sh` | 49, 56 | writes to `WORKFLOW_STATE_DIR` (defaults `.state/`); no artifact path awareness |
| `CLAUDE.md` | 40, 46 | Paths table documents the split as the intended model — reinforces it for every agent spawned |

Note: `_resolve_tasks_md` at line 868–903 in `record.py` has a multi-candidate fallback that includes both `.state/<slug>/tasks.md` AND `spec/changes/<slug>/tasks.md` — but this function is used only by `_check_all_tasks_completed`, not by `_resolve_feature_metrics`. The two resolver functions have diverged.

Reference:
- `config/scripts/orchestrator_next/record.py:787–798` — `_resolve_feature_metrics_tasks_path`
- `scripts/inline/compute-prediction-accuracy.py:80–81` — hardcoded sibling path
- `scripts/inline/archive-completed-change.sh:21,30` — source dir never includes spec/changes

## Impact

### Severity

High — affects all feature/bugfix completions (mark-change-completed, compute-prediction-accuracy, archive) every run.

### Affected Areas

| Consumer | Failure mode | Severity |
|----------|-------------|----------|
| `record.py:_resolve_feature_metrics_tasks_path` | `FileNotFoundError` at `mark-change-completed` time for any feature where `tasks.md` lives in `spec/changes/` | Critical — blocks completion |
| `scripts/inline/compute-prediction-accuracy.py` | Silent `predicted=0 / actual=0` → `task_accuracy_pct=100.0` on every run | High — corrupts metrics silently |
| `scripts/inline/archive-completed-change.sh` | `spec.md`, `design.md`, `tasks.md`, `diagnose.md` silently dropped from archive | High — data loss |
| `scripts/pre-commit.sh:31–32` | Hook validates `.state/*/state.yaml` — if state moves, hook stops firing | Medium — loses guard |
| `CLAUDE.md:40` | Paths table documents `.state/<slug>/` as canonical — misleads all agent spawns | Medium — perpetuates drift |
| `skills/orchestrate/SKILL.md:20,82–83` | `WORKFLOW_STATE_DIR` defaults to `.state/` — all orchestrate dispatches use wrong root | High — structural |
| `skills/learn/SKILL.md:16,31–32` | Scans `WORKFLOW_STATE_DIR/*/state.yaml` — misses any active run using `spec/changes/` | Medium — learn step may miss in-progress features |
| `skills/telemetry/SKILL.md:29,57` | Same scan pattern as learn — same miss risk | Medium |
| Step contracts (9 files) | All reference `$WORKFLOW_STATE_DIR/$CHANGE_ID/` for reads/writes | Medium — consistent internally, but wrong root |
| `agents/workflow-init.md:72,101` | Instructions reference `$WORKFLOW_STATE_DIR/<slug>/` | Medium — agent follows wrong root |

**Full impact catalog — non-test source files with hardcoded path expectations:**

Scripts (exact lines where behavior diverges):
- `skills/orchestrate/scripts/seed-state.sh:49,56` — writes to `.state/`
- `scripts/inline/archive-completed-change.sh:21,30` — reads from `.state/`, never reads `spec/changes/`
- `scripts/inline/compute-prediction-accuracy.py:80–81` — derives artifact path from `STATE_YAML_PATH` parent
- `scripts/pre-commit.sh:31–32` — glob matches `.state/*/state.yaml`

Python (config/scripts):
- `config/scripts/orchestrator_next/record.py:798` — `_resolve_feature_metrics_tasks_path` hardcodes `.state/` default
- `config/scripts/orchestrator_next/record.py:873,890` — `_resolve_tasks_md` has both-location fallback but is NOT used by `_resolve_feature_metrics`

Step contracts:
- `config/steps/design-and-draft-artifacts.yaml:72,96–97` — writes + verifies artifacts in `$WORKFLOW_STATE_DIR/$CHANGE_ID/`
- `config/steps/archive-completed-change.yaml:14,24,32,39` — copies from `$WORKFLOW_STATE_DIR/$CHANGE_ID`
- `config/steps/compute-prediction-accuracy.yaml:24` — reads from `$WORKFLOW_STATE_DIR/$CHANGE_ID/`
- `config/steps/compute-swe-metrics.yaml:25` — passes `$WORKFLOW_STATE_DIR/$CHANGE_ID` to script
- `config/steps/workflow-init.yaml:50–51` — verifies state.yaml + plan.yaml in `$WORKFLOW_STATE_DIR/<slug>/`
- `config/steps/run-learn-cycle.yaml:18` — reads from `$WORKFLOW_STATE_DIR/$CHANGE_ID/`
- `config/steps/select-workflow.yaml:31` — scans `$WORKFLOW_STATE_DIR/*/state.yaml`
- `config/steps/preview-route.yaml:22` — passes `$WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml` to estimate-cost.sh
- `config/steps/ux-design.yaml:30–31,43` — writes and verifies in `$WORKFLOW_STATE_DIR/$CHANGE_ID/`
- `config/steps/write-bootstrap-state.yaml:46,87,92,97` — writes state.yaml to `$WORKFLOW_STATE_DIR/<slug>/`

Skills:
- `skills/orchestrate/SKILL.md:20,82–83,136` — sets `WORKFLOW_STATE_DIR=$REPO_ROOT/.state`, references state.yaml/plan.yaml there
- `skills/learn/SKILL.md:16,31–32` — sets and scans `$WORKFLOW_STATE_DIR/*/state.yaml`
- `skills/telemetry/SKILL.md:29,57` — same pattern
- `skills/linear/SKILL.md:87` — writes `linear_ticket_id` to `$WORKFLOW_STATE_DIR/<feature>/state.yaml`

Documentation:
- `CLAUDE.md:40` — Paths table: `Active workflow state` → `.state/<slug>/state.yaml`
- `CLAUDE.md:46` — Paths table: `Per-feature retro` → `spec/changes/<change_id>/retro.md`
- `agents/workflow-init.md:72,92,101` — instructions reference `$WORKFLOW_STATE_DIR/<slug>/`

**Grep cross-check:**

The catalog above enumerates files by logical role. Line-level grep counts are higher because many files have multiple references:

```bash
grep -rn ".state/" /Users/spidey/code/feature_worktrees/orc-36 \
  --include="*.sh" --include="*.py" \
  | grep -v ".git/|/archive/|/tests/|test_" | wc -l
# → 5 lines across 3 files (ingest-pricing.py:1 comment, record.py:2 docstrings, pre-commit.sh:2)

grep -rn "WORKFLOW_STATE_DIR" \
  /Users/spidey/code/feature_worktrees/orc-36 \
  --include="*.sh" --include="*.py" --include="*.yaml" --include="*.md" \
  | grep -v ".git/|/archive/" | wc -l
# → 54 lines; catalog above enumerates 21 unique files/sections
# Discrepancy: each file has multiple lines referencing the variable.
# No additional files were found beyond what is cataloged.
```

### Since When

The split was established when `WORKFLOW_STATE_DIR` defaulted to `.state/` (introduced in the HL-287 workflow-engine refactor, committed around 2026-04-09 per `spec/project.yaml` learnings). The artifact producer (`design-and-draft-artifacts`) continued writing to the same `$WORKFLOW_STATE_DIR/$CHANGE_ID/` path, meaning all artifacts ended up in `.state/` in machine-driven runs — but the structural seam existed from that point. The retro system (`append-retro.sh`) was explicitly wired to `spec/changes/<slug>/retro.md` afterward, creating an explicit divergence. ORC-34 was the first run to trigger the metrics consumer failures (May 2026).

## Linear Ticket

none

## Unresolved Questions

1. **Eliminate `.state/` entirely or repoint it?** The ticket's proposed approach eliminates `.state/<slug>/` and moves everything to `spec/changes/<slug>/`. An alternative is to keep `.state/` for machine-only outputs and repoint `WORKFLOW_STATE_DIR` to `spec/changes/`. The distinction affects whether the `WORKFLOW_STATE_DIR` env var is retired, repointed, or aliased. Architect decision required.

2. **Migration order for in-flight workflows.** Changing the producer (`seed-state.sh`, `design-and-draft-artifacts`) before fixing consumers leaves any workflow mid-run in a broken state; changing consumers first leaves existing `.state/` layouts unread. A migration script (move `.state/<slug>/` → `spec/changes/<slug>/`) would close the window but needs to run before the first consumer-side step fires in any active run. The current `orc-36` workflow is mid-flight with state at `.state/orc-36/` — this run must be handled explicitly.

3. **`scripts/pre-commit.sh:31–32` — move hook or retire it?** The hook validates staged `.state/*/state.yaml`. If active state moves to `spec/changes/<slug>/`, the hook glob stops matching. Per the ticket's `.gitignore` plan (`spec/changes/*/state.yaml` → untracked), the new state files would never be staged, making the hook vacuously inert. Should it be updated to a schema validation step run before commit, or retired entirely?

4. **ORC-32 coordination.** `config/scripts/read-sub-state-metrics.sh` was noted as "related but ORC-32 owns it." Needs confirmation that ORC-32's changes are independent of the path consolidation, or whether they must be sequenced.

5. **`.gitignore` scope.** The ticket proposes excluding `spec/changes/*/state.yaml` and `spec/changes/*/plan.yaml`. This must not accidentally exclude `spec/changes/archive/*/state.yaml` (which IS committed). The glob pattern needs to be narrow enough to miss the archive subtree.
