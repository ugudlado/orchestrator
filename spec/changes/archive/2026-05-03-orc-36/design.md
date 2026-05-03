# Design: ORC-36 — Consolidate active workflow state under `spec/changes/<slug>/`

## Selected Approach

**Re-point `WORKFLOW_STATE_DIR` default** from `$REPO_ROOT/.state` to `$REPO_ROOT/spec/changes`. All producers and consumers continue using `$WORKFLOW_STATE_DIR/$CHANGE_ID/` as the canonical path — only the default flips. The `.state/` directory is no longer created or referenced.

**Rationale.** Diagnose.md established that the bug class is structural: producers and consumers diverged on which root directory holds workflow state. The simplest closure is to make every callsite resolve to the same root. The env-var seam stays intact (useful for tests, override-able), but its production default points to the directory where artifacts already live (`spec/changes/<slug>/`).

This is the smallest fix that closes the seam permanently. It does not introduce fallbacks (rejected — re-creates drift), does not retire `WORKFLOW_STATE_DIR` (rejected — invasive), does not migrate artifacts to `.state/` (rejected — pollutes machine-state dir with human-reviewable files).

## Component Breakdown

### 1. Writer: `seed-state.sh`

Single line change at `skills/orchestrate/scripts/seed-state.sh:49`:

```diff
- WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/.state}"
+ WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
```

Header comment at line 11 updated to describe the new default. No other logic change — `STATE_DIR="$WORKFLOW_STATE_DIR/$SLUG"` (line 56) automatically resolves to the new location.

### 2. Skills: matching default flip

Same one-line flip in:

- `skills/orchestrate/SKILL.md:20`
- `skills/learn/SKILL.md:16`
- `skills/telemetry/SKILL.md:29`

These three are the only places `WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-...}` is set. `skills/linear/SKILL.md:87` references `$WORKFLOW_STATE_DIR/<feature>/state.yaml` but does not set the default — no change needed there.

### 3. Archive script: `cp -R` → `mv`

`scripts/inline/archive-completed-change.sh` rewrite (lines 21, 30 region):

```diff
- SRC="$WORKFLOW_STATE_DIR/$CHANGE_ID"
- DST="$REPO_ROOT/$ARCHIVE_PATH"
- ...
- mkdir -p "$(dirname "$DST")"
- cp -R "$SRC" "$DST"
+ SRC="$REPO_ROOT/spec/changes/$CHANGE_ID"
+ DST="$REPO_ROOT/$ARCHIVE_PATH"
+ ...
+ mkdir -p "$(dirname "$DST")"
+ mv "$SRC" "$DST"
```

The source path is hardcoded to `$REPO_ROOT/spec/changes/$CHANGE_ID` (not `$WORKFLOW_STATE_DIR/$CHANGE_ID`) because `spec/changes/<slug>/` is the single canonical location. This avoids any risk of a `WORKFLOW_STATE_DIR` override inadvertently pointing the archive at a different root. `mv` is atomic (same filesystem) and removes the source dir as a side effect, replacing the prior copy + cleanup pattern. The downstream `git add "$ARCHIVE_PATH"` and `git commit` still work because `state.yaml`/`plan.yaml` inside the archive subtree are NOT covered by the `.gitignore` glob (see component 9).

A defensive cleanup block removes any legacy `.state/<slug>/` directory that may still exist from a pre-consolidation run (e.g. the orc-36 one-time migration).

### 4. Metrics resolver: `record.py:798`

`config/scripts/orchestrator_next/record.py` `_resolve_feature_metrics_tasks_path`:

```diff
-     return Path(repo_root) / ".state" / change_id / "tasks.md"
+     workflow_state_dir = os.environ.get("WORKFLOW_STATE_DIR") or str(Path(repo_root) / "spec" / "changes")
+     return Path(workflow_state_dir) / change_id / "tasks.md"
```

(or, even simpler: inline the new default if `WORKFLOW_STATE_DIR` env-var read is unwarranted at this layer — choose the form consistent with surrounding code; design intent is "look in `spec/changes/<slug>/tasks.md` by default, respect `WORKFLOW_STATE_DIR` override".)

The `_resolve_tasks_md` (lines 868–903) duplicate fallback logic remains untouched — it's used by `_check_all_tasks_completed` and already has both-location fallback as defense in depth. Out of scope to refactor here.

### 5. Prediction accuracy: `compute-prediction-accuracy.py:80–81`

```diff
- state_dir = Path(STATE_YAML_PATH).parent
- tasks_md = state_dir / "tasks.md"
+ state_dir = Path(STATE_YAML_PATH).parent
+ tasks_md = state_dir / "tasks.md"   # state_dir is now spec/changes/<slug>/, sibling lookup works
```

Code is unchanged — but verify by running. Once `STATE_YAML_PATH` resolves to `spec/changes/<slug>/state.yaml`, the parent IS the artifact dir, and `tasks.md` is a sibling. The bug evaporates by the producer-side fix. Add a comment at line 80 to document the assumption.

### 6. SWE metrics: `compute-swe-metrics.sh`

The script accepts `<state_dir>` as `$1`. Caller (`config/steps/compute-swe-metrics.yaml:25`) passes `$WORKFLOW_STATE_DIR/$CHANGE_ID`. Once `WORKFLOW_STATE_DIR` flips, the script automatically reads `state.yaml` from `spec/changes/<slug>/`. No internal change to the script. Verify only.

### 7. Step contracts (9 files)

Mechanical text update. Every reference to `$WORKFLOW_STATE_DIR/$CHANGE_ID/` already uses the variable, so no logic change. Some contracts have prose like "writes to `.state/...`" — replace with "writes to `spec/changes/<slug>/...`". Files (from diagnose.md catalog):

- `config/steps/design-and-draft-artifacts.yaml`
- `config/steps/archive-completed-change.yaml`
- `config/steps/compute-prediction-accuracy.yaml`
- `config/steps/compute-swe-metrics.yaml`
- `config/steps/workflow-init.yaml`
- `config/steps/run-learn-cycle.yaml`
- `config/steps/select-workflow.yaml`
- `config/steps/preview-route.yaml`
- `config/steps/ux-design.yaml`
- `config/steps/write-bootstrap-state.yaml`
- `config/steps/CONVENTIONS.md`

(11 files; no logic change — variable references are correct, prose updates only where it mentions `.state/` literally.)

### 8. Agent + doc files

- `agents/workflow-init.md` — three line ranges (72, 92, 101) reference `$WORKFLOW_STATE_DIR/<slug>/`. No change to the variable references; sweep prose for any `.state/` mentions.
- `CLAUDE.md` Paths table line 40: `Active workflow state | $REPO_ROOT/.state/<slug>/state.yaml` → `$REPO_ROOT/spec/changes/<slug>/state.yaml`.

### 9. `.gitignore`

Append:

```
# Active workflow state — committed only after archive (under spec/changes/archive/)
spec/changes/*/state.yaml
spec/changes/*/plan.yaml
```

Glob is single-segment (`*` matches one path component). It does NOT match `spec/changes/archive/<date>-<slug>/state.yaml` (two extra segments). Verify with `git check-ignore -v` on both paths in T-1.

### 10. Pre-commit hook retirement

`scripts/pre-commit.sh:31–32` (and the surrounding `if [ -n "$state_files" ]` block) check staged `.state/*/state.yaml` files. After this fix:

- `.state/*` no longer exists.
- `spec/changes/*/state.yaml` is gitignored (never staged in the active form).
- Archived `spec/changes/archive/**/state.yaml` IS staged but is historical — schema validation at archive time has no value (the file was already validated when active).

Delete the `state.yaml` schema check block. The yaml-syntax check above it (lines 1–28) stays — covers all yaml.

### 11. One-time orc-36 self-archival (T-7)

This run's `state.yaml`/`plan.yaml` already live in `.state/orc-36/`. After the fix lands, the archive script — which now does `mv "$WORKFLOW_STATE_DIR/$CHANGE_ID" "$DST"` — would `mv spec/changes/orc-36 archive/...`. But `state.yaml`/`plan.yaml` are still in `.state/orc-36/`, so they'd be missing.

**Mitigation**: Before running the `archive-completed-change` step for orc-36, the `mark-change-completed` step (or a dedicated migration helper invoked once during T-7) must move `.state/orc-36/{state.yaml,plan.yaml}` into `spec/changes/orc-36/`. After that one-time mv, the standard archive logic works.

T-7 owns this: it's a single line in a shell snippet (`mv .state/orc-36/* spec/changes/orc-36/ && rmdir .state/orc-36`) executed before the archive step on this run only. It is NOT committed code — it's the operator/agent's one-off finalization.

## Data Flow (Post-Fix)

```
seed-state.sh (writer)
  → spec/changes/<slug>/state.yaml
  → spec/changes/<slug>/plan.yaml

design-and-draft-artifacts (architect agent)
  → spec/changes/<slug>/spec.md
  → spec/changes/<slug>/design.md
  → spec/changes/<slug>/tasks.md

mark-change-completed → record.py
  → _resolve_feature_metrics_tasks_path(state)
    → spec/changes/<slug>/tasks.md  (exists ✓)

compute-prediction-accuracy.py
  → STATE_YAML_PATH = spec/changes/<slug>/state.yaml
  → state_dir.parent / "tasks.md"
    → spec/changes/<slug>/tasks.md  (exists ✓)

archive-completed-change.sh
  → mv spec/changes/<slug>  spec/changes/archive/<date>-<slug>
  → archive contains: state.yaml, plan.yaml, spec.md, design.md, tasks.md, diagnose.md, ...
```

## Error Handling

- **`mv` fails (e.g. cross-filesystem)**: archive script must abort and report the error rather than silently fall back to `cp`. Same filesystem is guaranteed because both source and destination are under `$REPO_ROOT`.
- **Source dir missing during archive**: existing `[ ! -d "$SRC" ]` check stays — emits skip JSON.
- **`spec/changes/<slug>/` already exists from a previous failed run before `seed-state.sh` runs**: `seed-state.sh` should `mkdir -p` (idempotent) and not clobber existing files; current behavior is sufficient — no change needed.
- **`WORKFLOW_STATE_DIR` set to a non-`spec/changes` path in CI/test**: still works; the env var is the override seam. Tests can point at a tempdir.

## Simplicity Check

- One-line writer change.
- One-line resolver change.
- One-character archive change (`cp -R` → `mv`).
- Pure text sweep across step contracts/skills/agents/docs.
- Two `.gitignore` lines.
- One block deletion in pre-commit.
- One-time orc-36 finalization (operator-side, not committed).

No new abstractions, no fallback paths, no state migrations. The fix collapses two paths into one — by definition simpler than the status quo.

## Migration / Rollout

Per **NF-1**, operator drains all in-flight workflows before installing. The only in-flight workflow (orc-36 itself) is handled by T-7's special-case finalization. No general migration script.

Post-install verification (AC-9): run a fresh `/autopilot` on a follow-up backlog ticket and confirm clean completion.
