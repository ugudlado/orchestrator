# Migration Guide: Adding a `run:` Field to a Step Contract

This guide explains how to migrate a step contract from inline-only execution to the
subprocess adapter path. After migration, `orchestrator next` returns `action: run_step`
and the caller executes your adapter script directly — giving the step full access to
its own token/cost metrics, which inline steps cannot provide.

**Reference adapter**: `config/scripts/adapters/claude_discoverer.py` is the template.
All new adapters should follow its shape.

## Pre-requisites

- The subprocess-per-step-observability feature must be merged to main.
- Python 3 must be available (`python3 --version` returns without error).
- `pyyaml`, `duckdb`, and `ruamel.yaml` must be installed (`make setup` installs them).
- Confirm `bin/orchestrator next` works on any existing fixture before modifying contracts.

## Step-by-Step

### 1. Write a Python adapter

Create `config/scripts/adapters/<step_id>_adapter.py` following the
`claude_discoverer.py` shape:

- Open with `#!/usr/bin/env python3` shebang and a `# requires:` comment listing deps.
- Read the six `ORCHESTRATOR_*` env vars (`ORCHESTRATOR_CHANGE_ID`, `ORCHESTRATOR_PHASE`,
  `ORCHESTRATOR_STEP_ID`, `ORCHESTRATOR_ATTEMPT`, `ORCHESTRATOR_WORKFLOW_DIR`,
  `ORCHESTRATOR_REPO_ROOT`). Exit 1 with a clear message if any are missing.
- Load the step contract YAML to get `instruction:` and `rules:` for the agent prompt.
- Invoke the agent runtime via subprocess (e.g., `claude -p --output-format json`).
  Exit 1 on non-zero exit or invalid JSON — do not write `state.yaml` on failure.
- Parse token/cost from the CLI output using the same extraction logic as
  `claude_discoverer.py::_extract_usage`.
- Atomically append a `step_history` entry to `$ORCHESTRATOR_WORKFLOW_DIR/state.yaml`
  using `ruamel.yaml` + `os.replace()` — never `yaml.safe_dump` (it reflattens
  block scalars and destroys comments).
- Exit 0 on success.

Exit codes: `0` success, `1` error (state.yaml NOT modified on failure).

### 2. Add `run:` to the step contract

In `config/steps/<step_id>.yaml`, add the `run:` field at the top level:

```yaml
id: <step_id>
version: <N+1>         # bump the version
run: config/scripts/adapters/<step_id>_adapter.py
intent: ...
rules: ...
instruction: |
  ...
```

The `run:` path is relative to the worktree root (where `orchestrator next` is invoked).

### 3. Bump the contract `version:`

Increment `version:` by 1 whenever you modify a step contract. This makes it easy to
correlate state.yaml step_history entries with the exact contract revision that ran.

### 4. Write a fixture state.yaml

Create a minimal fixture pointing at the migrated step, for use in the smoke test:

```yaml
change_id: test-migrate-<step_id>
schema: feature
version: 1
status: active
phase: specify
repo: orchestrator
step_history: []
next_step:
  phase: specify
  step_id: <step_id>
```

Place it under `config/scripts/tests/fixtures/state-<step_id>-run.yaml`.

### 5. Add a smoke test

Create `config/scripts/tests/test_<step_id>_adapter.py`. At minimum:

- A test that runs `bin/orchestrator next <fixture>` and asserts `action: run_step`
  in the JSON output (confirming the `run:` field is recognized).
- An integration test (skipped when `CLAUDE_API_KEY` / `ANTHROPIC_API_KEY` is absent)
  that runs the adapter end-to-end and asserts a `step_history` entry is appended to
  `state.yaml` with `status: completed` and non-null `usage.input_tokens`.

See `config/scripts/tests/test_explore_adapter.py` for the skip-gate pattern.

### 6. Verify `orchestrator next` returns `run_step`

```bash
ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE=config/steps \
  python3 bin/orchestrator next config/scripts/tests/fixtures/state-<step_id>-run.yaml
```

Expected output (action field):
```json
{
  "action": "run_step",
  "step_id": "<step_id>",
  ...
}
```

Exit code must be `0`. If you see `action: run_inline`, the `run:` field is not being
found — confirm the step contract path and the `ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE`
env var.

## Test Checklist

- [ ] `python3 -m unittest discover config/scripts/tests -v` — all tests pass, none fail
- [ ] `bin/orchestrator next <fixture>` returns `action: run_step` (not `run_inline`)
- [ ] Adapter exits 0 on success and writes a `step_history` entry to `state.yaml`
- [ ] Adapter exits 1 on Claude failure without modifying `state.yaml`
- [ ] Contract `version:` was bumped

## Rollback

Remove the `run:` line from the step contract (and revert the `version:` bump).
`orchestrator next` will immediately fall back to `action: run_inline`.
No data migration is needed — existing `step_events` rows are unaffected.
