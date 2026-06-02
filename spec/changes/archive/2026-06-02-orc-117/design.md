---
feature-id: orc-117
linear-ticket: ORC-117
---

# Design: Remove flags system from codebase

## Context

`state.yaml` carries a `flags` dict and `generate_plan.py` carries the
`rules_when` / `when:` / `verify_when` conditional machinery that reads it.
A HEAD grep (`rg '\bflags\b' --type py orchestrator_next bin -g '!tests/**'`)
proves the only non-test source readers are three files:

- `orchestrator_next/scripts/lib/seed_parse_overrides.py` — parses `key=value`
  overrides into a `flags` dict and emits it as a JSON output key.
- `orchestrator_next/scripts/lib/seed_write_state.py` — line 63 writes
  `"flags": d["flags"]` into state.yaml (a **hard** key read of the parse JSON).
- `orchestrator_next/generate_plan.py` — line 414 reads
  `state.raw.get("flags")`, threads it through `_merge_rules` and
  `_build_step_block`, and consumes it in `_evaluate_rules_when` (rules_when),
  the named-rule `when:` filter, and the `verify_when` override loop.

The remaining source hits are false positives: `doctor.py:418` and
`state_inspect.py:260` use the word "flags" for CLI/subprocess flags in
comments. No workflow YAML, step contract, or shell/bin script populates or
reads any of `rules_when`, `verify_when`, or a `when:` step-gate — confirmed by
`rg 'rules_when|verify_when' config/workflows config/steps` (the lone
`run-phase-review/prompt.md` hit is prose, and the ` if ` matches in schema
YAMLs are `check-rerun` comments). The conditional machinery has therefore
never fired in production. The `worktree` flag was already removed when
`create-worktree` became unconditional; the `linear` flag was never read.

Crucially for the named-rule `when:` filter removal: a structural scan of every
`rules:` block (`spec/project.yaml` and all `config/workflows/*.yaml`) confirms
**zero** dict-form named rules carry a `when:` key. Today a `when:`-gated rule
evaluates `flags.get(name, False)` → `False` → filtered out; deleting the filter
makes every named rule unconditionally active. Because no such rule exists,
this is pure dead-code deletion, not a behavior change — no previously-gated
rule flips to always-on.

This change deletes the dead `flags` write/parse and the inert conditional
machinery, and repairs the tests coupled to them.

## Goals / Non-Goals

### Goals

- Stop writing `flags` to state.yaml (`seed_write_state.py`).
- Stop parsing/emitting `flags` in the seed JSON (`seed_parse_overrides.py`).
- Delete `_evaluate_rules_when`, the named-rule `when:` filter, the
  `verify_when` override loop, and the `flags` parameter threading from
  `generate_plan.py`.
- Keep `pytest orchestrator_next/tests/` at parity with the recorded baseline
  (no test failing outside the named baseline set).

### Non-Goals

- Does NOT remove `extra_rules` — it is injected unconditionally and is not part
  of the flags system. Only the `rules_when` half of merge tier 1 is removed.
- Does NOT add a new "reject overrides" error path. The ticket says *remove*
  parsing (AC-2); the override-parse loop is deleted outright, not repurposed
  into a validator. No caller passes overrides (HEAD grep confirms), so there is
  nothing to guard against.
- Does NOT remove the `" if <flag>"` *bare-id stripping* in `_step_entry_for_id`
  — that line resolves a plain-string step entry to its bare id and is exercised
  by surviving tests; it does not read `flags`.
- Does NOT touch `resolved_flags` in `plan.yaml`-shape fixtures
  (`test_dispatch*.py`) — a separate legacy format read by no source code.
- Does NOT touch `doctor.py:418` or `state_inspect.py:260` (CLI/subprocess
  "flags", unrelated).
- Does NOT extract a standalone `rules_when` design doc — the design survives in
  git history and the archived `2026-04-20-generate-plan-yaml-at-init/` and
  `2026-05-22-orc-66/` artifacts; the referenced `rule-merge.md` has never
  existed (ORC-77 archived discovery confirms). No new doc is written.
- Does NOT attempt to fix the pre-existing baseline failures (see Constraints).

## Approaches Considered

### Approach 1: Atomic delete-and-fix per commit

Each commit removes one flag-bearing region together with the test code coupled
to it, so `pytest` stays green at every commit. The seed-pipeline removal is
fused into a single task because `seed_write_state.py` hard-reads the JSON
`flags` key produced by `seed_parse_overrides.py`.

- Pros: green at every commit; no cross-commit runtime breakage; small reviewable
  diffs; no test-driving of non-existent new behavior.
- Cons: requires fusing the coupled seed pair into one task rather than splitting
  along AC boundaries.
- Complexity: S

### Approach 2: RED-then-GREEN TDD pairs

Write a failing test per AC first, then make it pass.

- Pros: matches the default feature template.
- Cons: wrong tool for deletion — there is no new behavior to test-drive; the
  "test" would assert absence, which the existing suite already does after
  edits. `tdd_required` is unset on this run.
- Complexity: M

### Approach 3: Single big-bang deletion commit

Remove all flag code and fix all tests in one commit.

- Pros: fewest commits.
- Cons: a single large diff is harder to review and bisect; loses the
  green-at-every-commit property; couples unrelated test files into one blast
  radius.
- Complexity: S

### Selected Approach

**Approach 1 (Atomic delete-and-fix per commit), complexity S.** This is
deletion of inert code, so RED/GREEN (Approach 2) test-drives behavior that does
not exist. Approach 3's single commit sacrifices reviewability and bisectability
for no gain. Approach 1 keeps each commit green and scoped. The one deviation
from "one task per AC" is the **seed pipeline**: AC-1 (stop writing `flags`) and
AC-2 (stop parsing `flags`) are fused into T-1 because `seed_write_state.py:63`
does `d["flags"]` — a hard key read. Removing the parse-side key first would
raise a runtime `KeyError` that **no test catches** (there is no seed-pipeline
integration test in the suite), so the unit suite would stay green while the real
seed path broke. T-1 therefore removes both edits together AND adds a
pipe-through verify that runs the two scripts end-to-end.

## High-Level Design

### Architecture Overview

Three source files form a one-directional pipeline for `flags`:

```
seed_parse_overrides.py  --(JSON: {"flags": {...}, ...})-->  seed_write_state.py
   (constructs flags dict)        d["flags"]                 (writes flags: to state.yaml)
                                                                      |
                                                              state.yaml: flags: {...}
                                                                      |
                                                      generate_plan.py: state.raw.get("flags")
                                                          -> _evaluate_rules_when (rules_when)
                                                          -> named-rule when: filter
                                                          -> verify_when override loop
```

Every arrow is severed. After the change, no source writes or reads `flags`;
a legacy state.yaml that still carries `flags:` is harmless (the key is an
unread entry in the YAML dict — UC-E1).

### Key Abstractions

No new abstractions. The change is subtractive: the `flags` parameter is removed
from `_merge_rules` and `_build_step_block`, and the helper `_evaluate_rules_when`
is deleted. The rule-merge collapses tier 1 to `extra_rules` only, the named-rule
tier to "all named rules active" (no `when:` gate), and the phase-verify
resolution to `verify_block = base_verify`.

## Low-Level Design

### Components

**`seed_parse_overrides.py`** — remove:
- the `flags` dict construction (the `for arg in raw_overrides:` loop building
  `flags[k] = ...`, lines 30–36) and the `"flags": flags` JSON output key
  (line 53). With no flags built, `raw_overrides = args[4:]` (line 28) becomes
  unused; remove it.
- the module docstring's "Stdout: JSON with keys ... flags ..." mention (line 6).

The override loop is deleted, not converted to a validator — the ticket requires
removing parsing (AC-2), and no caller passes overrides.

**`seed_write_state.py`** — remove the `"flags": d["flags"]` entry from the
`state` dict (line 63). No other reference.

**`generate_plan.py`** — remove, in this order:
- the `flags` extraction `flags: dict[str, Any] = state.raw.get("flags") or {}`
  (line 414).
- the `flags=flags` argument at the `_build_step_block` call site (line 459).
- the `flags` parameter from `_build_step_block` (line 252) and its forward to
  `_merge_rules(... flags, repo_name)` (line 278).
- the `flags` parameter from `_merge_rules` (line 188); inside it, drop
  `rules_when = step_entry.get("rules_when", {}) or {}` (line 197),
  `injected = _evaluate_rules_when(rules_when, flags)` (line 199), and
  `merged.extend(injected)` (line 238). Keep `extra` / `merged.extend(extra)`.
- the named-rule `when:` filter (lines 224–234): replace the
  `when_flag`/`elif flags.get(...)` branch with an unconditional
  `active_named.append(str(entry.get("rule", "")))` for every named rule.
- the `_evaluate_rules_when` function itself (lines 139–162).
- the `verify_when` override loop (lines 477–483): collapse the
  `if base_verify is not None:` body to `verify_block = base_verify`
  (keep the surrounding `if verify_block is None` /
  `base_verify = phase_def.get("verify")` guard).
- the `"verify_when"` entry from the `_resolve_phases` synthetic-phase key tuple
  (line 95), and the cosmetic `rules_when` mention in the `_step_entry_for_id`
  docstring (keep the bare-id stripping code at lines 132–135).

### Data Flow

After the change, `seed_parse_overrides.py` emits JSON with keys
`slug, schema_name, repo_root, active, filtered` (no `flags`).
`seed_write_state.py` no longer reads `d["flags"]`. `generate_plan.py` builds
rules from `extra_rules` + contract + phase + named (all named rules active).

### State Management

`state.yaml` loses its `flags` key. Legacy state files that still contain
`flags:` are read by `_parser.load_state` into `state.raw` and simply never
accessed (UC-E1) — no crash.

### Error Handling

The single failure mode is the seed-pipeline coupling: if the parse-side `flags`
key were removed while `seed_write_state.py` still did `d["flags"]`, that line
would raise `KeyError` at runtime. T-1 removes both in one commit and verifies
the pipe-through, preventing this. No new error paths are introduced.

## Constraints

- **Baseline is dirty.** `pytest orchestrator_next/tests/` on HEAD already
  reports **8 failures**, none flag-related:
  - `test_agent_runner.py::test_parse_completion_importable`
  - `test_agent_runner.py::test_parse_completion_valid_block`
  - `test_agent_runner.py::test_parse_completion_invalid_inputs[no completion block here]`
  - `test_agent_runner.py::test_parse_completion_invalid_inputs[COMPLETION:\n  status: blocked\n  outputs: {}\n]`
  - `test_agent_runner.py::test_parse_completion_invalid_inputs[COMPLETION:\n  status: completed\n  outputs: {bad\n]`
  - `test_step_runner.py::test_capture_test_baseline_script_uses_step_dir_env`
  - `test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[feature-ticket-qa]`
  - `test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[bugfix-ticket-qa]`

  The five `test_agent_runner.py` cases come from an **untracked** test file
  (ORC-118 work) — if implementation runs in a clean worktree branched off
  HEAD, that file is absent and those five disappear. AC-4 is therefore phrased
  against a **named set**, not a count: *no test fails outside this set.*
- All `verify` commands are repo-root-relative (no absolute paths, no
  `cd /abs/path`).

## Trade-offs

- **Fusing AC-1 and AC-2 into one task** trades strict one-task-per-AC tidiness
  for runtime correctness across the seed pipeline. Acceptable: the two edits
  are one atomic unit (the hard `d["flags"]` read), and the unit suite cannot
  catch the split-order `KeyError` — so T-1 adds an explicit pipe-through verify.
- **Sweeping inert `flags: {}` fixtures** (T-4) is broader than the strictly
  AC-bound edits. Acceptable as honesty cleanup so no fixture implies a state
  field that no longer exists; each is a no-op for behavior and keeps the suite
  truthful.

## Acceptance Criteria

- AC-1: Given a seed run, when `seed_write_state.py` writes state.yaml, then the
  output contains no `flags` key. [traces: UC-1]
- AC-2: Given the seed pipeline, when `seed_parse_overrides.py` runs and its
  JSON stdout is consumed by `seed_write_state.py`, then the JSON contains no
  `flags` key and `seed_write_state.py` does not raise. [traces: UC-1]
- AC-3: Given a state.yaml (with or without a legacy `flags` key), when
  `generate_plan.py` runs, then it promotes the plan to nodes shape without
  calling `_evaluate_rules_when` (which no longer exists), without a `when:`
  named-rule filter, and without a `verify_when` override — and does not crash
  on a legacy `flags` key. [traces: UC-2, UC-E1]
- AC-4: Given the test suite, when `pytest orchestrator_next/tests/ -q` runs
  after the change, then no test fails outside the baseline set named in
  Constraints (in particular `test_generate_plan.py`,
  `test_generate_plan_directory_layout.py`, and `test_workflow_schemas_load.py`
  all pass). [traces: UC-2, UC-E2]

## Decisions

- Fuse AC-1 + AC-2 into one task → `seed_write_state.py:63` hard-reads
  `d["flags"]` from the parse JSON → split-order removal raises an
  untested-runtime `KeyError`; fusing + a pipe-through verify keeps the seed path
  correct.
- Rewrite `test_rule_merge_precedence`, do not just update its helper call →
  the test's schema populates `rules_when` and asserts
  `rules.index("Injected from rules_when.")` → after `_evaluate_rules_when` is
  gone that string never appears and `.index` raises `ValueError`; the
  `rules_when` schema key (lines 182–184) and the two `idx_injected_when`
  lines (234, 244) must be removed. `extra_rules` and its ordering assertion
  stay.
- Express AC-4 as a named baseline set, not a count → 5 of the 8 baseline
  failures live in an untracked file that may be absent in a clean worktree; a
  count comparison would silently shift.
- Delete the override-parse loop outright (no reject-on-override validator) →
  AC-2 requires removing parsing, and no caller passes overrides → keeps the
  diff minimal and avoids adding a behavior the ticket does not ask for.

## Open Questions

- None. The `verify_when` removal (formerly OQ-1) is resolved: it is
  `flags`-dependent and can never fire once `flags` is gone, so it is removed
  under the ticket's "when: conditional" scope and collapses to
  `verify_block = base_verify`.
