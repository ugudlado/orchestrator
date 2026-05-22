# Diagnosis: Unify phase-opening artifact — discovery.md for both explore and diagnose

## Symptoms

`orchestrator next` exits with code 2 and prints:

```
ERROR: step 'design-and-draft-artifacts' blocked — required input(s) ['diagnosis_result']
unresolvable: no prior completed step produced them under evidence.outputs and they are
absent from state.raw. The upstream producer has not completed.
```

This occurs on **every** feature and spike workflow immediately after the `explore` step
completes. The `design-and-draft-artifacts` step never becomes dispatchable.

## Reproduction Steps

Run from `/Users/spidey/code/orchestrator`:

```python
#!/usr/bin/env python3
"""Reproduce ORC-78: design-and-draft-artifacts blocked in feature workflow.

Run: python3 reproduce_orc78.py
Expected: Exit code 2 printed to stdout; error message to stderr.
"""
import sys, tempfile, os, yaml
sys.path.insert(0, 'config/scripts')

with tempfile.TemporaryDirectory() as tmp:
    steps_dir = os.path.join(tmp, 'steps')
    os.makedirs(steps_dir)
    state_dir = os.path.join(tmp, 'state')
    os.makedirs(state_dir)

    # explore declares output: discovery_result
    with open(os.path.join(steps_dir, 'explore.yaml'), 'w') as f:
        f.write('id: explore\nagent: discoverer\ninstruction: survey\nrules: []\n'
                'inputs: []\noutputs:\n  - discovery_result\n')

    # design-and-draft-artifacts declares input: diagnosis_result (the bug)
    with open(os.path.join(steps_dir, 'design-and-draft-artifacts.yaml'), 'w') as f:
        f.write('id: design-and-draft-artifacts\nagent: architect\ninstruction: design\n'
                'rules: []\ninputs:\n  - diagnosis_result\noutputs:\n  - design.md\n')

    os.environ['ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE'] = steps_dir

    nodes = [
        {'id': 'explore', 'status': 'completed', 'agent': 'discoverer', 'goal': '',
         'inputs': [], 'outputs': ['discovery_result'], 'rules': []},
        {'id': 'design-and-draft-artifacts', 'status': 'pending', 'agent': 'architect',
         'goal': '', 'inputs': ['diagnosis_result'], 'outputs': [], 'rules': []},
    ]
    history = [
        {'step_id': 'explore', 'phase': 'main', 'status': 'completed',
         'agent': 'discoverer', 'attempt': 1,
         'evidence': {'outputs': {'discovery_result': {'path': 'discovery.md'}}}}
    ]
    state_raw = {
        'schema': 'feature', 'change_id': 'test-orc78', 'phase': 'main',
        'flags': {}, 'step_history': history,
        'workflow_plan': {'main': {'nodes': nodes}}
    }
    sp = os.path.join(state_dir, 'state.yaml')
    with open(sp, 'w') as f:
        yaml.safe_dump(state_raw, f)

    from orchestrator_next.parser import load_state
    from orchestrator_next.dispatch import dispatch

    state = load_state(sp)
    action, code = dispatch(state, sp)
    print(f'Exit code: {code}')   # Expected: 2
    assert code == 2, f"Expected exit 2, got {code}"
    print("BUG CONFIRMED: feature workflow blocked at design-and-draft-artifacts")
```

**Actual output (verified):**

```
ERROR: step 'design-and-draft-artifacts' blocked — required input(s) ['diagnosis_result']
unresolvable: no prior completed step produced them under evidence.outputs and they are
absent from state.raw. The upstream producer has not completed.
Exit code: 2
BUG CONFIRMED: feature workflow blocked at design-and-draft-artifacts
```

## Expected vs Actual

- **Expected**: After `explore` completes (producing `discovery_result`), `orchestrator next` selects `design-and-draft-artifacts` and emits a dispatch action (exit 0).
- **Actual**: `orchestrator next` exits 2 because `design-and-draft-artifacts` declares `inputs: [diagnosis_result]` — a name that `explore` never produces. The engine's required-input pre-check at `dispatch.py:315` correctly blocks the step, but the contract mismatch makes it impossible to ever satisfy.

## Investigation

### Evidence Gathered

- `config/steps/explore.yaml:38` — `outputs: [discovery_result]`; step produces `discovery_result`
- `config/steps/diagnose.yaml:72` — `outputs: [diagnosis_result]`; step produces `diagnosis_result`
- `config/steps/design-and-draft-artifacts.yaml:12` — `inputs: [diagnosis_result]`; only one name declared, the bugfix name
- `config/workflows/feature.yaml` — step order: `explore → design-and-draft-artifacts`
- `config/workflows/bugfix.yaml` — step order: `diagnose → design-and-draft-artifacts`
- `config/scripts/orchestrator_next/dispatch.py:197–217` — `_check_required_inputs` is the hard block; introduced as a real enforcement in ORC-63 (was a no-op stub before)

### Data Flow Trace

**Feature/spike schema path (broken):**

```
explore step completes
  → evidence.outputs = {discovery_result: {path: "discovery.md"}}

orchestrator next selects design-and-draft-artifacts

dispatch.py:315 calls _check_required_inputs(state, contract, "design-and-draft-artifacts")
  → _resolve_inputs walks state.step_history in reverse
  → looks for "diagnosis_result" in each entry's evidence.outputs   ← KEY: wrong name
  → explore's outputs only contain "discovery_result" → not found
  → also checks state.raw top-level → not found
  → missing = ["diagnosis_result"]
  → required_missing = ["diagnosis_result"]  (not in optional_inputs)
  → prints ERROR, returns exit 2                                     ← BLOCKS HERE
```

**Root divergence point**: `dispatch.py:93` iterates `contract.inputs`; `contract.inputs` for `design-and-draft-artifacts` is `["diagnosis_result"]`, but the only artifact in step history is keyed `"discovery_result"`. The lookup at line 99 (`if name in outputs`) never matches.

**Bugfix schema path (works, but only by accident):**

```
diagnose step completes
  → evidence.outputs = {diagnosis_result: {path: "diagnose.md"}}

design-and-draft-artifacts input check looks for "diagnosis_result" → found → unblocked
```

The bugfix path works only because `diagnose` happens to emit the same name as `design-and-draft-artifacts` expects. The feature/spike path has never worked since ORC-63 made the pre-check blocking.

## Root Cause

Two producers emit the phase-opening brief under different output names:
- `explore` → `discovery_result` (`config/steps/explore.yaml:38`)
- `diagnose` → `diagnosis_result` (`config/steps/diagnose.yaml:72`)

The single consumer declares only one of these names:
- `design-and-draft-artifacts` → `inputs: [diagnosis_result]` (`config/steps/design-and-draft-artifacts.yaml:12`)

The required-input pre-check enforced at `dispatch.py:315` (introduced as a hard block in ORC-63) makes this mismatch fatal. Before ORC-63 the check was a no-op stub, so the bug was latent.

Reference: `config/steps/design-and-draft-artifacts.yaml:12` (wrong input name) and `config/scripts/orchestrator_next/dispatch.py:197–217` (enforcement point).

## Impact

### Severity

critical

### Affected Areas

- **All feature and spike workflows** — every `orchestrator next` call at `design-and-draft-artifacts` exits 2. No feature or spike workflow can advance past the explore step since ORC-63 shipped.
- **Bugfix workflows** — unaffected; `diagnose` produces `diagnosis_result` which matches.

### Files Requiring Change (confirmed blast radius)

| File | Nature of reference | Must change? |
|---|---|---|
| `config/steps/diagnose.yaml` | Declares output `diagnosis_result`, filename `diagnose.md` | Yes — rename output and file |
| `config/steps/design-and-draft-artifacts.yaml:12` | `inputs: [diagnosis_result]` | Yes — rename to `discovery_result` |
| `config/scripts/orchestrator_next/tests/test_record_agent_field.py` lines 136, 172, 225, 264 | 4 payload fixtures use `diagnosis_result`/`diagnose.md` | Yes — update fixtures |
| `config/tests/test-archive-merges-worktree-artifacts.sh` lines 25, 44 | Creates and checks `diagnose.md` | Yes — rename to `discovery.md` |
| `config/steps/CONVENTIONS.md` lines 264, 275 | Lists `diagnose.md` in artifact table | Yes — rename |
| `agents/discoverer.md` lines 121–131 | Discoverer COMPLETION block for diagnose step | Yes — rename output/file |
| `skills/linear/SKILL.md:69` | References `diagnose.md` in description field | Yes — rename |
| `config/steps/contracts/artifact-formats.md` | Diagnosis Format Contract names the file `diagnosis.md` (inconsistent, not `diagnose.md`) | Review — section title uses `diagnosis.md`; no functional reference |

### Files That Do NOT Require Change

| File | Why excluded |
|---|---|
| `config/scripts/orchestrator_next/tests/test_orc36_path_consolidation.py` lines 5, 119, 157, 273 | Docstring comments only; reference the ORC-36 diagnosis artifact (a historical document), not the step contract |
| `spec/changes/orc-44/plan.yaml`, `orc-30/plan.yaml`, `orc-58/plan.yaml` | Legacy pre-ORC-63 plan.yaml format; not consumed by current dispatch engine |
| `spec/changes/orc-58/state.yaml` | Completed run record; historical only |
| `spec/changes/orc-30/state.yaml` | Completed run record; historical only |
| `config/steps/contracts/done-payload.md` | Already shows `discovery_result`/`discovery.md` as the example; no `diagnosis_result` reference |
| `config/steps/ux-design.yaml` | Already uses `discovery_result`; not affected |

### Since When

Introduced when ORC-63 made the required-input pre-check a hard block. Before ORC-63 `_check_required_inputs` was a no-op stub and the mismatch was silently ignored. Exact commit: the ORC-63 merge that landed `dispatch.py` with `_check_required_inputs` returning exit 2.

## Self-Modification Impact Note

ORC-78's own fix renames `diagnose.md` → `discovery.md`, which is the artifact this very `diagnose` step produces. This is not a problem for the running ORC-78 workflow — the rename is applied to the `config/steps/` contracts and related files by ORC-78's implementation tasks, not to the current run's state. The architect/developer should sequence tasks so that the rename of `diagnose.yaml` output declaration and `diagnose.md` filename happens atomically with updating `design-and-draft-artifacts.yaml:12` — partial application would leave one schema broken while fixing the other.

## Proposed Approach

Unify the phase-opening artifact name to `discovery_result` / `discovery.md` across both schemas: rename `diagnose.yaml`'s declared output from `diagnosis_result` to `discovery_result` and its filename from `diagnose.md` to `discovery.md`, then update `design-and-draft-artifacts.yaml` to declare `inputs: [discovery_result]`, and update all callsites in the blast-radius table above.

## Unresolved Questions

None — fix direction is user-decided. The blast radius is fully confirmed.

## Key Decisions

- **Design direction**: Atomic single-pass rename to `discovery_result` / `discovery.md`
  (approach (a)). Selected by the auto-selection heuristic — complexity S (2),
  lower than the backward-compat alias (M) and the staged rename (M). It is also
  the only option that fully removes the naming inconsistency without an engine
  change.
- **Rejected — backward-compat alias** (approach (b)): would require teaching
  `dispatch._resolve_inputs` to treat `diagnosis_result` as a synonym (an engine
  change) and would leave the inconsistency permanently in the codebase. More
  total complexity, not less.
- **Rejected — staged rename** (approach (c)): leaves stale `diagnose.md`
  references and a broken bugfix template between stages with no real benefit;
  the rename is mechanical.
- **Blast radius correction**: a fresh `grep` against HEAD found four files the
  diagnosis table missed — `config/steps/execute-next-task.yaml`,
  `config/steps/contracts/artifact-formats.md`,
  `skills/systematic-debugging/SKILL.md`, and `config/templates/bugfix/`. The
  rename is three-way (`diagnose.md` + `diagnosis.md` + target `discovery.md`),
  not two-way. design.md carries the grep-verified file list.
- **No engine change**: `_check_required_inputs` is purely name-based; renaming
  the contract output is sufficient. `dispatch.py` is not touched.
- **Self-modification**: the rename applies to `config/` contracts and templates
  only; the running orc-78 workflow keeps its own `diagnose.md` on disk. No task
  mutates `spec/changes/orc-78/` artifacts.

## Linear Ticket

ORC-78
