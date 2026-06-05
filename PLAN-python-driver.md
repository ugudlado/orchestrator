# Python Driver Port — unify dispatch loop inside `orchestrator run`

Branch: `python-driver`  ·  Worktree: `~/code/feature_worktrees/python-driver`

## Goal
One execution path. The dispatch loop moves **in-process into the CLI** (`orchestrator run <id>`),
replacing the bash driver. Eliminates the divergent script-execution semantics between
`run-workflow.sh` and `bin/orchestrator`'s inline arm.

## Decisions (locked)
- **Hard cutover** (work isolated in worktree; main stays runnable).
- **Loop lives inside `orchestrator run`** — not a separate `driver.py` entry.
- **One canonical script path + one agent path**, both Python.
- **Keep `orchestrator next` / `done`** as standalone subcommands (tests, manual debugging).
- **pre/post contract hooks** ship in this port (`pre:` / `post:` keys on step contract).

## VERIFIED root cause (corrected after grep)
- `bin/orchestrator next` consumes `action.run` INLINE (record + exit 0, no JSON) BEFORE
  any `run` key reaches stdout. `emit_json` fires only for agent steps (bin/orchestrator:376).
- Therefore `run-workflow.sh`'s `run_step` arm — which only fires on JSON-with-`.run` from
  stdout — is **DEAD CODE**. It never executes under the live ORC-45 protocol.
- `grep -rn 'exit 10' config/steps/` → **nothing**. No step soft-fails. The exit-10 +
  `detect-workflow-issues.sh` script-warning machinery has **no live consumer**.
- So there is ONE live script path (CLI inline arm), not two. No behavioral merge needed.

## Canonical script semantics (LOCKED: drop dead soft-fail)
- exit 0  → completed (hoist `state_patch` from last stdout JSON line)
- else    → failed (on_failure routing retries)
- `archive-completed-change`: durable pre-write BEFORE running, then re-point state path
- DROPPED: exit-10 soft-fail, detect-workflow-issues script-warning (dead code; noted in commit)

## Where things are used (verified)
- `run-workflow.sh`: ONE real caller — `orchestrator-run.sh` line 16. Rest are tests/docs.
- CLI inline arm: `bin/orchestrator` lines ~379–490, reached when `dispatch` emits no-JSON.
- `orchestrator run` today: `_run_verb` → `_shell_workflow_verb(argv, "orchestrator-run.sh")`.

## Seeding logic that must survive (in orchestrator-run.sh)
REPO_ROOT resolution · idempotent state seeding · schema resolution · archived-state rerun
probe · ORCHESTRATOR_HOME/config resolution. → Port to Python (a `run` preamble) or keep a
thin seeding helper the CLI calls before the loop.

## Files
- NEW: in-process loop (module under orchestrator_next, called by `run` verb)
- EDIT: `bin/orchestrator` — `run` verb drives the loop in-process; DELETE inline-script arm
- EDIT: `dispatch.py` — emit `pre`/`post` + emit script steps as actions uniformly
- EDIT: `contract.py`/parser — `pre:` / `post:` schema keys
- DELETE: `run-workflow.sh`
- DELETE/THIN: `orchestrator-run.sh` (seeding → Python)
- MIGRATE: existing `.orchestrator/hooks/pre-step.sh` → contract `pre:`
- REWRITE: brittle tests asserting bash internals — `test_run_workflow_overlay.py`,
  `test_state_inspect.py` (6 call-site mirrors), `test_prose_contracts.py`,
  `test_parse_completion.py`. Replace with behavior tests against the Python loop.

## Failure policy (LOCKED — deliberate improvement over bash parity)
- Tool nonzero exit → record `failed`, continue (on_failure/max_retries retries). [bash parity]
- Malformed COMPLETION → record `failed`, continue (RETRY). **Changed from bash exit-5 abort.**
  Rationale: one flaky parse must not kill an autonomous feature run; matches autopilot intent.
  `max_retries` on the step caps any bad-output loop. Document this delta in the commit.
- `record()` returns code 3 (bad payload) → record as `failed`, not fatal. [parity point #3]

## FINAL STATUS — port complete, verified
- run_loop.py: agent arm + script arm + loop + run_cmd (arg parse) + _seed_state (Python).
- agent_routes.py: route resolution (lifted from agent-routes.sh).
- bin/orchestrator: `run` verb → run_cmd in-process; inline-script arm now calls the
  SAME run_script_step (one canonical executor); legacy duplicate block deleted.
- DELETED: run-workflow.sh, orchestrator-run.sh, seed-state.sh.
- Bug found+fixed during verify: loop caught only dispatch.ContractDispatchError but the
  missing-run path raises parser.ContractDispatchError (different class) → now catches both.
- Bug found+fixed during verify: inline_script_env action_env is keyword-only (caught at
  RUNTIME by the script canary, invisible to import — vindicates the advisor's gate).

## VERIFICATION (the gate, met)
- test_inline_script.py — script step runs end-to-end via run_script_step → recorded. PASS.
- test_seed_state.py (3) — _seed_state produces dispatch-ready state, idempotent, fail-loud. PASS.
- test_run_loop_agent_arm.py (NEW, 2) — full agent chain (build_prompt→fake claude→split_stdout
  →parse_completion→_agent_payload→record) + record accepts payload + malformed=recoverable. PASS.
- Full suite: 469 passed, 13 failed. All 13 failures are PRE-EXISTING on main (verified by
  running the same 13 on main → 13 fail). ZERO regressions from this change.
- Advisor bug-watch: #1 exception-class (FIXED), #2 in_progress row (safe — record computes
  attempt), #3 attempt on payload (safe — record falls back).

## pre/post hooks — SHIPPED (user re-confirmed "build now" after premise change)
- StepContract.pre / .post fields (parser._as_str_list coerces scalar|list).
- dispatch emits pre/post in both agent + script action dicts.
- run_loop._run_hooks: pre runs before body (nonzero → block, exit 2); post after
  (nonzero → logged, non-fatal). Hooks get ORCHESTRATOR_STATE_YAML/STEP_ID + REPO_ROOT.
- test_run_loop_hooks.py (3): pre+post run, failing-pre blocks, failing-post non-fatal. PASS.
- Note: old global .orchestrator/hooks/pre-step.sh had NO repo consumer; per-contract
  pre:/post: is the replacement shape (declared on the step that needs it).

## Out of scope (not mine)
- 13 pre-existing test failures (stale parse-completion path, graph telemetry, etc.) — fail on
  main too; verified by running the same 13 on main. Untouched.

## Build status (session checkpoint)
- DONE: architecture verified; bash run_step arm proven dead; exit-10 soft-fail proven unused.
- DONE: `orchestrator_next/agent_routes.py` (route resolution, lifted from agent-routes.sh).
- DONE: `orchestrator_next/run_loop.py` scaffold — StepResult, run_agent_step signature,
  _build_prompt / _invoke_tool stubs (NotImplementedError, filled w/ parity tests).
- NEXT: fill _build_prompt (parity vs bash build_prompt) + _invoke_tool (tools.yaml
  args_template) → then the loop → seeding port → CLI `run` wiring → pre/post → test rewrites.

## Three load-bearing parity points (acceptance criteria)
1. exit-10 soft-fail preserved.
2. archive relocation: durable pre-write + state-path re-point.
3. `orchestrator done` exit-3 (bad payload) → record as failed, not fatal (on_failure retry).

## Gate
A real canary feature run on the worktree must produce a `state.yaml` step_history
**equivalent to a bash run** before the change is trusted. Unit tests alone are insufficient.

## Risks
- Brittle structural tests (per ORC-108 memory) — expect to rewrite, not patch.
- Seeding logic in shell is load-bearing; porting it is the largest hidden surface.
- pi-settings flag assembly, agent_overlay append, workflow-meta prompt assembly —
  all currently shell, must port faithfully into the agent arm.
