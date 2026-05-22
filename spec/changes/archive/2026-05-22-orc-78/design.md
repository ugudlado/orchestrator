# Design: Unify phase-opening artifact — `discovery.md` for both explore and diagnose

## Problem

The `design-and-draft-artifacts` step declares `inputs: [diagnosis_result]`. Only the
`diagnose` step (bugfix schema) emits that name. The `explore` step (feature/spike
schemas) emits `discovery_result`. Since ORC-63 made the required-input pre-check at
`dispatch.py:_check_required_inputs` a hard block (exit 2), every feature and spike
workflow is dead-stopped immediately after `explore` — `design-and-draft-artifacts`
can never become dispatchable. Root cause and reproduction are confirmed in
`diagnose.md`.

## Selected Approach: Atomic single-pass rename to `discovery_result` / `discovery.md`

Rename the phase-opening artifact so a **single** name (`discovery_result`) and a
**single** file (`discovery.md`) serve every schema. The file's *internal structure*
still varies by schema — Discovery Brief Format for feature/spike, Diagnosis Format
for bugfix — but the contract handle is unified, so `design-and-draft-artifacts`
declares exactly one input name that every upstream producer satisfies.

### Approaches considered

| Approach | Description | Pros | Cons | Complexity |
|---|---|---|---|---|
| **(a) Atomic single-pass rename** (SELECTED) | Rename `diagnose.yaml`'s output to `discovery_result` and its file to `discovery.md`; point `design-and-draft-artifacts.yaml` at `discovery_result`; update all callsites in one change. | Removes the inconsistency entirely; no engine change; one name everywhere; matches the user-decided direction exactly. | Touches ~12 files in one pass. | **S** |
| (b) Backward-compat alias | Keep `diagnosis_result` as a deprecated synonym; teach `dispatch._resolve_inputs` to treat it as an alias of `discovery_result`. | No file rename. | Requires an **engine code change** to `_resolve_inputs`; leaves the naming inconsistency permanently in the codebase; more total complexity, not less. | M |
| (c) Staged rename | Rename the contract triple now; defer doc/test/template callsites to a follow-up. | Smaller first change. | Leaves stale references (`diagnose.md`) in docs and a broken bugfix template/fixtures between stages; no real benefit since the rename is mechanical. | M |

### Selection rationale

Auto-selection heuristic (XS=1…XL=5, lowest wins): (a)=S=2, (b)=M=3, (c)=M=3.
**Approach (a)** has the lowest complexity and is selected. It is also the only
option that fully removes the inconsistency without an engine change. The
diagnosis confirms that renaming the contract output name is sufficient —
`_check_required_inputs` is purely name-based, so `dispatch.py` is untouched.

## Finding: the rename is three-way, not two-way

The diagnosis framed this as a two-name problem (`diagnosis_result` vs
`discovery_result`). A fresh `grep` against HEAD shows **three** names for the same
phase-opening artifact:

1. `diagnose.md` — the filename `diagnose.yaml` actually emits.
2. `diagnosis.md` — the filename used by the format contract
   (`artifact-formats.md`), the bugfix template (`templates/bugfix/diagnosis.md`),
   and several skill/template references.
3. `discovery.md` — the target unified name (already used by `explore`).

All three collapse to `discovery.md`. The bugfix template file itself
(`templates/bugfix/diagnosis.md`) must be renamed to `templates/bugfix/discovery.md`
so that the `design-and-draft-artifacts` template-resolution path
(`$ORCHESTRATOR_HOME/config/templates/$SCHEMA/discovery.md`) and the diagnose-step
template both resolve to a file that exists for every schema.

## Component breakdown — grep-verified blast radius (HEAD)

The blast radius below is verified by `grep -rn` against HEAD for `diagnosis_result`,
`diagnose.md`, and `diagnosis.md`. It supersedes the diagnosis table, which missed
four files (`execute-next-task.yaml`, `artifact-formats.md`,
`skills/systematic-debugging/SKILL.md`, `config/templates/bugfix/`).

### Group 1 — Contract triple (must change atomically)

| File | Current | Change |
|---|---|---|
| `config/steps/diagnose.yaml` | `outputs: [diagnosis_result]`; instruction/verify reference `diagnose.md`; COMPLETION line `outputs.diagnosis_result: {path: "diagnose.md"}` (lines 51, 61, 65, 72) | Rename output to `discovery_result`; rename file to `discovery.md` in all instruction/verify/COMPLETION text. |
| `config/steps/design-and-draft-artifacts.yaml` | `inputs: [diagnosis_result]` (line 12) | Rename input to `discovery_result`. |
| `config/templates/bugfix/diagnosis.md` | Template file named `diagnosis.md` | `git mv` to `config/templates/bugfix/discovery.md` (content unchanged — heading `# Diagnosis: {title}` is structural, stays). |

### Group 2 — Documentation & prose callsites

| File | Change |
|---|---|
| `config/steps/contracts/artifact-formats.md` | Lines 269, 363, 396, 405: filename references `diagnosis.md` → `discovery.md`. Section heading "Diagnosis Format Contract" and the in-fence template heading `# Diagnosis: {title}` (line 276) stay — they describe the bugfix-variant *structure*, not the filename. |
| `config/steps/CONVENTIONS.md` | Lines 264, 275: `diagnose.md` → `discovery.md` in the artifact-layout table and prose. |
| `config/steps/execute-next-task.yaml` | Line 30: rule references "reproduction script from diagnosis.md" → `discovery.md`. |
| `config/templates/bugfix/fix-plan.md` | Line 6: "Root cause reference: {from diagnosis.md ...}" → `discovery.md`. |
| `config/templates/bugfix/tasks.md` | Line 9: `T-3 Document root cause in diagnosis.md` → `discovery.md`. (Only this filename token — the stale task list itself is out of scope, see Non-Goals.) |
| `skills/systematic-debugging/SKILL.md` | Lines 16, 72, 81: `diagnosis.md` → `discovery.md`. |
| `skills/linear/SKILL.md` | Line 69: `diagnose.md` → `discovery.md` in the description-field summary. |
| `agents/discoverer.md` | Lines 121–131: diagnose-step section heading, instruction, and COMPLETION block — `diagnose.md` → `discovery.md`, `diagnosis_result` → `discovery_result`. |

### Group 3 — Test fixtures

| File | Change |
|---|---|
| `config/scripts/orchestrator_next/tests/test_record_agent_field.py` | Lines 136, 172, 225, 264: payload fixtures `"outputs": {"diagnosis_result": "diagnose.md"}` → `{"discovery_result": "discovery.md"}`. |
| `config/tests/test-archive-merges-worktree-artifacts.sh` | Lines 25, 44: creates and checks `diagnose.md` → `discovery.md`. |

### Not changed (verified)

- `config/scripts/orchestrator_next/dispatch.py` — `_check_required_inputs` is purely
  name-based; renaming the contract output satisfies the check. No engine change.
- `config/scripts/orchestrator_next/tests/test_orc36_path_consolidation.py` —
  docstrings reference the *historical* ORC-36 diagnosis artifact, not the step
  contract. Not a functional reference.
- Legacy `spec/changes/orc-30|44|58/` plan.yaml / state.yaml — completed historical
  run records, not consumed by current dispatch.

## Error handling

This is a contract-name rename, not new runtime logic. The "error path" of interest
is the dispatch pre-check itself: after the rename, a feature/spike workflow at
`design-and-draft-artifacts` must resolve `discovery_result` from `explore`'s
evidence (exit 0), and a bugfix workflow must resolve `discovery_result` from
`diagnose`'s evidence (exit 0). The regression test asserts both — exit 2 before the
fix, exit 0 after.

## Goals

- A single phase-opening artifact name — `discovery_result` / `discovery.md` — across
  feature, spike, and bugfix schemas.
- `design-and-draft-artifacts` becomes dispatchable in feature/spike workflows
  (no more exit 2 at the required-input pre-check).
- Bugfix workflows continue to work (now by contract, not by accident).
- No remaining references to `diagnosis_result`, `diagnose.md`, or `diagnosis.md`
  as the phase-opening artifact name anywhere in `config/`, `skills/`, `agents/`.

## Non-Goals

- **No engine change.** `dispatch.py` / `_check_required_inputs` is not modified;
  the diagnosis confirms a contract-output rename is sufficient.
- **No backward-compat alias.** `diagnosis_result` is removed, not aliased
  (approach (b) rejected).
- **No mutation of this running workflow's artifacts.** The orc-78 run's own
  `diagnose.md` on disk stays. The rename applies to `config/` contracts and
  templates — it affects *future* workflows only. No task touches
  `/Users/spidey/code/feature_worktrees/orc-78/spec/changes/orc-78/`.
- **No restructure of `templates/bugfix/tasks.md`.** Its pre-rendered T-1..T-10
  list is stale relative to the current bugfix workflow, but that is unrelated
  tech debt — only the `diagnosis.md` filename token on line 9 changes.
- **No rename of the "Diagnosis Format Contract" section** in `artifact-formats.md`.
  The section describes the bugfix-variant *internal structure* of `discovery.md`;
  that structural distinction remains valid.
- **No change to ORC-36 historical docstrings** in `test_orc36_path_consolidation.py`.

## Risks

- **Self-modification hazard.** This bugfix renames `diagnose.md` — the artifact this
  very `diagnose` step produced. Mitigation: the rename targets `config/` contracts
  and templates only; the running orc-78 state and its `diagnose.md` are untouched.
  Tasks must not write under `spec/changes/orc-78/`.
- **Partial application.** If the contract triple (Group 1) is split across tasks, an
  intermediate state leaves one schema broken. Mitigation: Group 1 is a single task.
- **Stale-callsite miss.** Any missed reference leaves a dangling `diagnose.md` /
  `diagnosis.md` mention. Mitigation: the final fix task ends with a repo-wide grep
  asserting zero remaining occurrences.

## Acceptance Criteria

- **AC-1**: A committed regression test in
  `config/scripts/orchestrator_next/tests/` exercises the feature-schema dispatch
  path: it fails with exit code 2 on HEAD and passes (exit 0) after the rename.
  Verify: `python3 -m pytest config/scripts/orchestrator_next/tests/<test_file> -q`.
- **AC-2**: `config/steps/diagnose.yaml` declares `outputs: [discovery_result]` and
  every instruction/verify/COMPLETION reference names `discovery.md`.
  Verify: `grep -n "discovery_result\|discovery.md" config/steps/diagnose.yaml`
  returns matches; `grep -c "diagnosis_result\|diagnose.md" config/steps/diagnose.yaml`
  returns 0.
- **AC-3**: `config/steps/design-and-draft-artifacts.yaml` declares
  `inputs: [discovery_result]`.
  Verify: `grep -n "inputs:" -A1 config/steps/design-and-draft-artifacts.yaml`
  shows `discovery_result`.
- **AC-4**: The bugfix template is `config/templates/bugfix/discovery.md` (renamed
  via `git mv`, content unchanged); `config/templates/bugfix/diagnosis.md` no longer
  exists. Verify: `test -f config/templates/bugfix/discovery.md && ! test -f config/templates/bugfix/diagnosis.md`.
- **AC-5**: No reference to `diagnosis_result`, `diagnose.md`, or `diagnosis.md` as
  the phase-opening artifact remains in `config/` (excluding the in-fence template
  heading and the "Diagnosis Format Contract" section title), `skills/`, or
  `agents/`. Verify:
  `grep -rn "diagnosis_result\|diagnose\.md" config/ skills/ agents/` returns no
  functional matches; remaining `diagnosis.md` hits are only the section heading.
- **AC-6**: A feature/spike workflow clears the `design-and-draft-artifacts`
  required-input pre-check — the regression test from AC-1 confirms exit 0 after
  the rename.
- **AC-7**: The full test suite passes with zero new failures after the change.
  Verify: project test runner (pytest for `orchestrator_next`, the shell tests
  under `config/tests/`).
