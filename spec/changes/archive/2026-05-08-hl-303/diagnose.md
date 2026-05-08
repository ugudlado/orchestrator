# Diagnosis: HL-303 — Workflow Artifacts Should Live in the Worktree, Not repo_root

## Symptom

During `/autopilot ORC-37`, the dispatcher advanced past `execute-next-task` on the very
first iteration — all 29 tasks were skipped. The `repeat_until: all_tasks_completed`
predicate returned `True` immediately. The user had to manually symlink
`worktree/spec/changes/orc-37/tasks.md → repo_root/spec/changes/orc-37/tasks.md`
mid-run and manually fail `run-phase-review` to force the dispatcher back into
`execute-next-task`.

Root cause: workflow artifact writers and the `_check_all_tasks_completed` reader target
two different filesystem paths when `flags.worktree=true`.

---

## Reproduction

Save as `spec/changes/hl-303/repro.sh` (already committed). Run:

```
bash spec/changes/hl-303/repro.sh
```

**Expected output (post-fix):** "OK: predicate correctly detected unchecked tasks"

**Actual output (current, confirms bug):**

```
[writer] tasks.md written to: /tmp/.../repo/spec/changes/demo/tasks.md
[writer] content:
  - [ ] T-1: Fix the path mismatch bug
  - [ ] T-2: Write regression test

[reader] state.yaml has:
  worktree_path = /tmp/.../worktree  (NO tasks.md here)
  repo_root     = /tmp/.../repo

[resolver] _resolve_tasks_md chose: /tmp/.../worktree/spec/changes/demo/tasks.md
[resolver] that path exists: False

Bug confirmed: _check_all_tasks_completed returned True (fail-open)
               despite unchecked tasks existing at .../repo/spec/changes/demo/tasks.md
               The repeat_until: all_tasks_completed loop would IMMEDIATELY
               advance execute-next-task — skipping all tasks.
```

---

## Root Cause

### Writer side — artifacts go to `$REPO_ROOT/spec/changes/<id>/`

Every artifact-writing stage resolves `WORKFLOW_STATE_DIR` to `$REPO_ROOT/spec/changes`:

- **`skills/orchestrate/scripts/seed-state.sh:49`**
  ```bash
  WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
  ```
  This default is repo_root-relative and has no worktree awareness. `state.yaml` and
  `plan.yaml` are seeded here.

- **`config/steps/design-and-draft-artifacts.yaml:72`**
  Instruction: `Write to $WORKFLOW_STATE_DIR/$CHANGE_ID/<file>.`
  Artifacts `spec.md`, `design.md`, and `tasks.md` are written to
  `$REPO_ROOT/spec/changes/<id>/`.

- **`config/steps/diagnose.yaml` (via `design-and-draft-artifacts` convention)**
  Same `$WORKFLOW_STATE_DIR/$CHANGE_ID/` destination for `diagnose.md`.

- **`config/steps/ux-design.yaml:30-31`**
  Writes `ux-prototype.html` and `ux-artifacts.yaml` to `$WORKFLOW_STATE_DIR/$CHANGE_ID/`.

The `WORKFLOW_STATE_DIR` env var defaults to `$REPO_ROOT/spec/changes` in every entry
point (`SKILL.md`, `seed-state.sh`, step contracts). No path points into the worktree.

### Reader side — `_resolve_tasks_md` prefers `worktree_path`

`config/scripts/orchestrator_next/record.py:906`:

```python
root = state_raw.get("worktree_path") or state_raw.get("repo_root")
if isinstance(root, str) and root and isinstance(change_id, str) and change_id:
    candidates.append(Path(os.path.expanduser(root)) / "spec" / "changes" / change_id / "tasks.md")
```

When `state.yaml` has `worktree_path` set (e.g., `/Users/spidey/code/feature_worktrees/hl-303`),
the function builds a candidate path under the **worktree**, not `repo_root`. That directory
has no `spec/changes/<id>/tasks.md` because writers wrote to `repo_root` instead.

The loop at `record.py:913-915` returns the first existing candidate. None exist under
the worktree. The function falls back to returning `candidates[-1]` (the non-existent
worktree path) at `record.py:917`.

### Fail-open site — `_check_all_tasks_completed` returns `True` on missing file

`config/scripts/orchestrator_next/record.py:920-932`:

```python
def _check_all_tasks_completed(state_raw: dict[str, Any]) -> bool:
    """Return True iff no unchecked `- [ ]` items remain in tasks.md.

    Missing or unreadable tasks.md returns True (fail-open: advance).
    """
    path = _resolve_tasks_md(state_raw)
    if path is None:
        return True          # line 927 — fail-open: no path at all
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return True          # line 931 — fail-open: file missing or unreadable
    return re.search(r"^\s*-\s*\[\s*\]", text, re.MULTILINE) is None
```

When `_resolve_tasks_md` returns the non-existent worktree path, `path.read_text()`
raises `FileNotFoundError`, the except clause catches it at line 931, and the predicate
returns `True` — meaning "all tasks done, advance." This is the origin of the
premature advancement.

### Structural seam: `workflow_dir` vs `WORKFLOW_STATE_DIR`

`config/scripts/orchestrator_next/parser.py:180` sets:
```python
workflow_dir = str(raw.get("worktree_path", ""))
```

This `workflow_dir` propagates as `ORCHESTRATOR_WORKFLOW_DIR` env var
(`config/steps/contracts/step-dispatch.md:160`). Agents that read
`$ORCHESTRATOR_WORKFLOW_DIR` see the worktree path, but step-contract instructions
reference `$WORKFLOW_STATE_DIR` (defaulting to `$REPO_ROOT/spec/changes`).
These two env vars point to **different directories** when `flags.worktree=true`.

### Asymmetric resolver pair (ORC-36 historical divergence)

Two separate resolver functions exist in `record.py` with different strategies:

- `_resolve_tasks_md` (line 889) → prefers `worktree_path` then `repo_root`
- `_resolve_feature_metrics_tasks_path` (line 807) → only uses `repo_root`

These were flagged as diverged in `spec/changes/archive/2026-05-03-orc-36/diagnose.md:191`.

---

## Impact

### Writers: step contracts that write artifacts to `$WORKFLOW_STATE_DIR/$CHANGE_ID/`

| File | Nature |
|------|--------|
| `skills/orchestrate/scripts/seed-state.sh:49` | Seeds `state.yaml`, `plan.yaml` to `$REPO_ROOT/spec/changes` |
| `config/steps/design-and-draft-artifacts.yaml:72` | Writes `spec.md`, `design.md`, `tasks.md` |
| `config/steps/diagnose.yaml` | Writes `diagnose.md` |
| `config/steps/ux-design.yaml:30-31` | Writes `ux-prototype.html`, `ux-artifacts.yaml` |
| `config/steps/workflow-init.yaml:50-51` | Writes/verifies `state.yaml`, `plan.yaml` |
| `config/steps/write-bootstrap-state.yaml:46` | Bootstrap schema writes `state.yaml` |
| `skills/orchestrate/SKILL.md:20` | Sets `WORKFLOW_STATE_DIR` default |
| `skills/learn/SKILL.md:16` | Sets `WORKFLOW_STATE_DIR` default |
| `skills/telemetry/SKILL.md:29` | Sets `WORKFLOW_STATE_DIR` default |

Total writer call-sites referencing `WORKFLOW_STATE_DIR` or equivalent: **63** (per
`grep -rn 'WORKFLOW_STATE_DIR|spec/changes/\$CHANGE_ID' config/ skills/ | wc -l`).

### Readers: code that probes `spec/changes/<id>/` via worktree preference

| File | Lines | Description |
|------|-------|-------------|
| `config/scripts/orchestrator_next/record.py` | 889–917 | `_resolve_tasks_md` — primary reader, buggy candidate priority |
| `config/scripts/orchestrator_next/record.py` | 920–932 | `_check_all_tasks_completed` — fail-open at 927, 931 |
| `config/scripts/orchestrator_next/record.py` | 807–819 | `_resolve_feature_metrics_tasks_path` — repo_root only (not worktree-biased; diverged from `_resolve_tasks_md`) |
| `config/scripts/orchestrator_next/record.py` | 1391 | `worktree_path or repo_root` — retro write path |
| `config/scripts/orchestrator_next/parser.py` | 180 | Sets `workflow_dir = worktree_path` → `ORCHESTRATOR_WORKFLOW_DIR` env |

### Step contracts that hardcode or reference artifact location

| File | Hardcoded location |
|------|-------------------|
| `config/steps/CONVENTIONS.md` | `$WORKFLOW_STATE_DIR/$CHANGE_ID/` |
| `config/steps/design-and-draft-artifacts.yaml` | `$WORKFLOW_STATE_DIR/$CHANGE_ID/` |
| `config/steps/archive-completed-change.yaml` | Copies from `$WORKFLOW_STATE_DIR/$CHANGE_ID/` to `spec/changes/archive/` |
| `config/steps/compute-swe-metrics.yaml` | `$WORKFLOW_STATE_DIR/$CHANGE_ID` |
| `config/steps/mark-change-completed.yaml` | `spec/changes/archive/YYYY-MM-DD-<id>/` |
| `config/steps/run-phase-review.yaml` | `spec/changes/archive/*/state.yaml` |

### Tests covering this area

| Test file | What it covers | Status |
|-----------|---------------|--------|
| `config/scripts/orchestrator_next/tests/test_resolve_tasks_md.py:50-58` | `test_resolve_uses_worktree_when_present` — asserts worktree_path wins over repo_root. **This test codifies the buggy preference.** Will need updating post-fix. |
| `config/scripts/orchestrator_next/tests/test_resolve_tasks_md.py:24-32` | `test_resolve_finds_repo_root_tasks_md` — only tests repo_root when no worktree_path |
| `config/scripts/orchestrator_next/tests/test_resolve_tasks_md.py:61-85` | `test_check_all_tasks_completed_*` — uses repo_root only; does not exercise the mismatch scenario |

**No existing test** covers the specific scenario where `worktree_path` and `repo_root` point
to different directories AND `tasks.md` exists under `repo_root` only.

---

## Proposed Approach

Consolidate artifact writers and the `_resolve_tasks_md` reader on a single canonical
artifact location (either always `repo_root`-relative, or always worktree-local when
`flags.worktree=true`), removing the `worktree_path` preference that causes the reader to
look in an empty worktree directory.

---

## Unresolved Questions

1. **Which side moves — writers or reader?** Writers could be updated to emit into the
   worktree when `flags.worktree=true`, matching the reader's current preference. Or the
   reader could drop worktree_path priority and always use repo_root, matching current
   writers. Each direction has different implications for the archive flow.

2. **State.yaml itself lives at repo_root during the run.** If artifacts move into the
   worktree, `archive-completed-change.yaml` (which copies from `$WORKFLOW_STATE_DIR/$CHANGE_ID`)
   needs its source path updated too. What happens when the worktree is removed mid-run
   (e.g., crash before `archive-completed-change`)? Are worktree-local artifacts recoverable?

3. **Backward compatibility for existing in-progress `spec/changes/<id>/` dirs** from
   runs started before this fix. Should `_resolve_tasks_md` keep a legacy fallback for
   the repo_root location in a transitional period?

4. **`ORCHESTRATOR_WORKFLOW_DIR` vs `WORKFLOW_STATE_DIR` naming.** `parser.py:180` sets
   `workflow_dir = worktree_path`, so `ORCHESTRATOR_WORKFLOW_DIR` currently means "worktree"
   while `WORKFLOW_STATE_DIR` means "repo_root/spec/changes". Once the canonical location
   is chosen, these two env vars should converge on the same root or be clearly renamed to
   avoid future drift.

5. **`_resolve_tasks_md` and `_resolve_feature_metrics_tasks_path` are already diverged**
   (flagged in ORC-36). Should the fix unify them into a single resolver, or fix them
   independently to keep ORC-36's minimal-fix scope intact?
