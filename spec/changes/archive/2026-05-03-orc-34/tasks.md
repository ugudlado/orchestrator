# Tasks — Add started_at to seed-state.sh canonical state.yaml

- [x] T-1: Add regression-test assertions in `test_seed_state.py` that the seeded `state.yaml` contains both `created_at` and `started_at` and they are equal
  Verify: `pytest config/scripts/orchestrator_next/tests/test_seed_state.py -k seed_state -q` FAILS on `main` with an assertion error referencing `started_at`; the new assertion lines are visible in `git diff config/scripts/orchestrator_next/tests/test_seed_state.py`.

- [x] T-2: Fix `seed-state.sh` to write `started_at` alongside `created_at` with the same ISO-8601 UTC timestamp value
  Verify: `pytest config/scripts/orchestrator_next/tests/test_seed_state.py -k seed_state -q` PASSES; `grep -n "started_at" skills/orchestrate/scripts/seed-state.sh` shows the new key inside the `state = {...}` dict; a fresh seed (`bash skills/orchestrate/scripts/seed-state.sh <slug> bugfix` against a tmp repo) produces a `state.yaml` for which `python3 -c "import yaml,sys; s=yaml.safe_load(open(sys.argv[1])); assert s['created_at']==s['started_at'] and s['started_at']"` exits 0.
  depends: T-1

- [x] T-3: Run the full repository test suite — zero new failures
  Verify: `pytest config/scripts/orchestrator_next/tests -q` exits 0; no new failures vs. `main`.
  depends: T-2

- [x] T-4: E2E verify `orchestrator done` no longer raises `feature_metrics_resolution_failed` from missing `started_at` on a freshly-seeded state
  Verify: Run the reproduction recipe from `diagnose.md` § Reproduction Steps on the fix branch, then pipe a minimal step-completion payload through `orchestrator done <state.yaml>`; resulting `step_history` contains no `feature_metrics_resolution_failed` warning whose `reason` traces to missing `started_at`. Save command transcript to evidence.
  depends: T-2
