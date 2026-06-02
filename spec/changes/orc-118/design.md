---
feature-id: orc-118
linear-ticket: ORC-118
---

# Design: Move Agent Step Execution into bin/orchestrator

## Context

`run-workflow.sh` is the shell driver that loops `orchestrator next → execute → orchestrator done`. ORC-112 moved *inline script* execution into `bin/orchestrator` (Python): for a `run:` step the CLI runs the subprocess, records the result, and exits 0, leaving the shell to re-loop. But *agent* steps still execute inside `run-workflow.sh` — its `invoke_tool` machinery does prompt assembly, agent→tool routing, model-tier/pi-settings resolution, subprocess spawn, COMPLETION parse (`parse-completion.py`), usage adaptation, done-payload construction (`state_inspect.py build-payload`), and workflow-issues merge. ORC-118 completes the symmetry: agent steps execute in Python exactly as inline scripts do, reducing `run-workflow.sh` to a pure dispatch loop.

Most of the agent-path logic is *already Python*, reachable today only through shell shims:
- `orchestrator_next/usage_adapters.py` → `split_stdout` (package-root, importable).
- `orchestrator_next/agent_overlay.py` → `overlay_text` (importable; called via `python3 -m` subprocess).
- `orchestrator_next/scripts/lib/state_inspect.py` → `cmd_build_payload`, `cmd_workflow_meta`, `cmd_pi_settings` (under `scripts/lib/`, **not** package-importable).
- `orchestrator_next/scripts/lib/parse-completion.py` → `parse_completion` (**hyphenated filename — not importable as a module**).
- `orchestrator_next/scripts/lib/agent-routes.sh` → route resolution (shell wrapping an embedded Python heredoc).
- `orchestrator_next/scripts/lib/detect-workflow-issues.sh` → workflow-issues detection (~204 lines pure shell; consumed by both this driver and the LLM `skills/orchestrate` driver).

So the work is **wire existing Python + port the shell remainder into an importable module**, not a rewrite. The `bin/orchestrator` inline-script branch (`if action.get("run"):`) is the structural template for the new agent branch.

## Goals / Non-Goals

### Goals

- `bin/orchestrator` executes agent steps end-to-end: prompt assembly (instruction + workflow meta + ticket context + agent overlay), agent→tool routing, model-tier/pi-settings resolution, subprocess spawn with cwd from `worktree_path`, COMPLETION parse, usage adaptation, done-payload build + merge, `orchestrator done`, exit 0 to loop.
- `run-workflow.sh` becomes a pure `orchestrator next` loop: no `invoke_tool`, no `build_prompt`, no `resolve_agent_tool`/`resolve_tool`, no `parse-completion.py` call, no `build-payload` call, no `DONE_PAYLOAD` construction.
- Worktree cwd for agent subprocesses is resolved in Python from `worktree_path` in state.yaml.
- All four preserved behaviors keep working through the agent path: retry (re-dispatch at attempt+1), workflow-issues merge (retry-success / tool-crashed), pre-step hook, archive state-relocation.
- `pytest orchestrator_next/tests/` is clean and a full end-to-end workflow run passes.

### Non-Goals

- Deleting `run-workflow.sh`. It survives as a trivial loop (the loop, logging, hook call, completion/blocked/archive exit handling). Whether the skills layer later calls `bin/orchestrator` directly is deferred.
- Changing the agents.yaml / routes config **format**, the COMPLETION block contract (status vocabulary, field names), or the state.yaml shape.
- Migrating `orchestrator complete`, metrics/duckdb integration, or DAG task-node work (ORC-63/64/65).
- Porting `detect-workflow-issues.sh` to Python. It stays a shell script (shared with `skills/orchestrate`); the Python agent path invokes it as a subprocess, matching how the inline path already coexists with shell helpers.

## Approaches Considered

### Approach 1: Extract `orchestrator_next/agent_runner.py`, call it from `main()`

A new importable module owns agent-step execution. `bin/orchestrator main()` gains an `elif action.get("agent"):` branch that calls `agent_runner.run_agent_step(state, state_yaml_path, action)` — structurally parallel to the existing inline-script branch which delegates to `step_runner`/`record`/`step_env`. The shell functions are ported into helpers within (or alongside) this module: `resolve_route` (ports `agent-routes.sh`), `build_agent_prompt` (ports `build_prompt` + ticket fetch + overlay), `invoke_agent_tool` (ports `invoke_tool` subprocess + pi flags), and done-payload assembly (calls the existing `state_inspect`/`parse-completion`/`usage_adapters` functions, promoted to importable form).

- **Pros:** Unit-testable in pytest (satisfies `tdd_required` + AC-5 cleanly); mirrors the inline-script delegation pattern already in `main()`; keeps `main()` thin; one obvious home for all agent-path logic.
- **Cons:** New file (~250–300 lines, mostly relocated logic); requires promoting `parse-completion.py`/`state_inspect.py` functions into importable package modules.
- **Complexity:** M

### Approach 2: Inline the agent branch directly in `bin/orchestrator main()`

Add the full agent-execution body inline in `main()` next to the inline-script body, as the shell did in one function.

- **Pros:** No new file; superficially "fewer files."
- **Cons:** `main()` already ~210 lines; this doubles it. The logic is only reachable through bats subprocess tests (the inline-script body has the same limitation today) — directly at odds with `tdd_required` and AC-5's pytest gate. Mixing routing/subprocess/parse concerns into the entry point is the opposite of the existing delegation style.
- **Complexity:** M (not smaller — same logic, worse placement, lost testability)

### Approach 3: Keep agent execution in shell; only move worktree-cwd resolution

Minimal: resolve cwd in Python, leave `invoke_tool` etc. in shell.

- **Pros:** Smallest diff.
- **Cons:** Fails AC-1, AC-2, AC-4 — the ticket explicitly wants agent execution in `bin/orchestrator` and `run-workflow.sh` reduced to a pure loop. Non-starter.
- **Complexity:** S

### Selected Approach

**Approach 1.** The auto-selection heuristic maps Approach 1 and Approach 2 both to M and Approach 3 to S — but Approach 3 is disqualified on correctness (it does not satisfy AC-1/AC-2/AC-4, the core of the ticket), so it is removed before the lowest-complexity tie-break. Between the two M approaches, Approach 1 wins on module-reuse count (it reuses `step_runner`/`record`/`step_env` delegation structure and makes the ported logic unit-testable) and is the only one compatible with `tdd_required` + AC-5's pytest gate. Approach 2's "no new file" is not lower complexity once `main()` bloat and lost testability are counted honestly.

## High-Level Design

### Architecture Overview

```
run-workflow.sh (pure loop)                bin/orchestrator (Python)
  run pre-step hook                           main():
  ACTION=$(orchestrator next STATE)             dispatch() → (action, exit_code)
  on exit 1/2/3 → handle & exit                 if action.agent:  agent_runner.run_agent_step(...)  ← NEW
  on exit 0:                                     if action.run:    <inline-script branch> (ORC-112)
    loop (state already updated by CLI)          exit 0  (loop)
```

Post-migration, **both** kinds (`run:` and `agent:`) execute inside `bin/orchestrator` and exit 0 with no JSON on stdout. `run-workflow.sh` no longer branches on kind — it sees only "exit 0 → loop" / "exit 1|2|3 → terminal". This collapses the shell's `case "$KIND"` block entirely.

`agent_runner.run_agent_step` is the new orchestration function. It consumes the `action` dict that `dispatch()` already produces (which carries `step_id`, `phase`, `attempt`, `agent`, `instruction`, `step_context`, `started_at`) plus state.yaml, and drives the agent lifecycle to a recorded `orchestrator done`.

### Key Abstractions

- **`run_agent_step(state, state_yaml_path, action) -> int`** — top-level orchestrator for one agent step; returns process exit code (0 to loop). The agent-path twin of the inline-script body.
- **`resolve_route(agent, repo_root) -> Route`** — ports `agent-routes.sh` + `resolve_config` + `resolve_tool`: resolves agent→tool binary, args_template, and model tier with `.orchestrator` override precedence and `ORCHESTRATOR_*` env overrides. (The shell already implements the core as an embedded Python heredoc — that heredoc becomes the function body.)
- **`build_agent_prompt(action, state, repo_root, ticket_id) -> str`** — ports `build_prompt` + `fetch_ticket_context` + the COMPLETION contract footer + `agent_overlay.overlay_text`.
- **`invoke_agent_tool(route, prompt, cwd, pi_settings) -> CompletedProcess`** — ports `invoke_tool`'s args_template substitution, pi-flag prepend, and `subprocess.run`.
- **`build_agent_payload(...)`** — promoted from `state_inspect.cmd_build_payload`'s agent/failed-kind bodies into a callable function (the `state_inspect` CLI subcommand calls the same function); reuses the promoted `parse_completion` and `usage_adapters.split_stdout`; merges workflow-issues via subprocess call to `detect-workflow-issues.sh`. The result is recorded via `record.record(...)` directly (no `orchestrator done` subprocess) — parity with the inline-script branch. `workflow_meta_text(state)` is similarly extracted from `cmd_workflow_meta` so `build_agent_prompt` calls it in-process.

## Low-Level Design

### Components

| Component | Responsibility | Ports / reuses |
|-----------|----------------|----------------|
| `agent_runner.run_agent_step` | Drive one agent step end-to-end; record + return exit 0 | new orchestration |
| `agent_runner.resolve_route` | agent→tool+template+model_tier, override precedence | `agent-routes.sh`, `resolve_config`, `resolve_tool` |
| `agent_runner.build_agent_prompt` | Assemble prompt + ticket + overlay + COMPLETION footer | `build_prompt`, `fetch_ticket_context`, `agent_overlay.overlay_text` |
| `agent_runner.invoke_agent_tool` | args_template subst, pi flags, subprocess spawn w/ cwd | `invoke_tool` heredoc |
| `agent_runner.resolve_agent_cwd` | `worktree_path` from state → cwd (fallback repo_root) | `WORKTREE_PATH`/`AGENT_WORK_DIR` logic |
| `parse_completion` (promoted) | parse COMPLETION block | `parse-completion.py` (renamed importable) |
| `state_inspect` build-payload (promoted) | done-payload from completion + usage | `cmd_build_payload` (importable call) |
| `usage_adapters.split_stdout` | assistant_text + normalized usage | already importable |
| `bin/orchestrator main()` | add `elif action.get("agent"):` branch | mirrors inline-script branch |
| `run-workflow.sh` | pure loop + hook + terminal-exit handling | strip agent + run_step branches |
| `detect-workflow-issues.sh` | workflow-issues array (subprocess, unchanged) | shared with skills/orchestrate |

**Importability fix (prerequisite):** `parse-completion.py` cannot be imported (hyphen). It is renamed/relocated to an importable package module (`orchestrator_next/parse_completion.py`) with a thin shim left at the old path if any other caller depends on the script form (grep first). `state_inspect.cmd_build_payload` is called by importing the function and passing an args object, or by extracting the payload-building core into a plain function the CLI subcommand also calls — no behavior change to the `state_inspect` CLI.

### Data Flow

1. `run-workflow.sh` runs pre-step hook, calls `orchestrator next STATE`.
2. `dispatch()` returns an agent action (exit 0); `main()` routes to `run_agent_step`.
3. `run_agent_step`: `resolve_route(agent)` → tool/template/tier; `resolve_agent_cwd(state)` → cwd; `build_agent_prompt(...)` → prompt (writes prompt file); `invoke_agent_tool(...)` → stdout/stderr + exit.
4. On tool exit ≠ 0: build a `failed` payload (model=none, zero tokens), merge workflow-issues (`--tool-exit`), `orchestrator done`, **return 0** (driver re-loops; `_compute_attempt` re-dispatches at attempt+1, or `max_spawn_failures` cap blocks). *This is the existing tool-failure semantics, preserved.*
5. On tool exit 0: `split_stdout` → assistant_text + usage; `parse_completion(assistant_text)`. On parse failure → build a `failed` payload + log raw tail, `orchestrator done`, **return 0** (driver re-loops). *This is the deliberate semantics change — see Decisions.*
6. On valid COMPLETION: build agent done-payload (completion + usage + started_at), merge workflow-issues (`--attempt` retry-success), call `record.record(state_yaml_path, payload)` directly (parity with the inline-script branch — not an `orchestrator done` subprocess; the payload is already terminal), return 0.
7. `run-workflow.sh` sees exit 0, re-loops. Archive relocation (state.yaml moved by `archive-completed-change`) is detected via the existing "state.yaml missing after loop" check, which already lives in the shell loop and is agent-kind-agnostic.

### State Management

- The only state.yaml mutator on the agent path remains `orchestrator done` (via `record.py`) — unchanged. `run_agent_step` does not write state directly.
- The in_progress pre-stamp (`_append_in_progress_state_entry_if_absent`) currently fires in `main()` before emitting JSON; post-migration it fires in `main()` (or `run_agent_step`) before the subprocess spawn, preserving pause/resume semantics. Behavior unchanged — only the call site relative to execution.
- `attempt` arrives in the action from `dispatch()` (`_compute_attempt`); `run_agent_step` does not recompute it.

### Error Handling

- **Tool binary missing (UC-E1):** `resolve_route` returns a sentinel / raises; `run_agent_step` records a `failed` (zero-token) payload and returns 0 → driver loops → `max_spawn_failures` eventually blocks (exit 2). *(Behavior change from current exit-4-kills-loop; see Decisions.)*
- **Malformed/absent COMPLETION (UC-E2):** record `failed` with raw stdout tail as evidence, return 0 → loop. *(Behavior change from current exit-5-kills-loop; see Decisions.)*
- **Tool subprocess non-zero (UC-E3):** record `failed`, merge `--tool-exit` workflow-issue, return 0 → loop. *(Unchanged — current shell already does this at line 648.)*
- **No worktree_path (UC-E4):** cwd falls back to repo_root, matching inline-script `REPO_ROOT`-or-None behavior.
- **Pre-step hook failure (UC-E5):** hook invocation stays in `run-workflow.sh` (best-effort `|| true`, unchanged). Out of `run_agent_step`'s scope — resolves OQ-4 toward "hook stays in shell loop."

## Constraints

- `tdd_required`: every implementation task is preceded by a failing-test task.
- `agent-agnostic` rule: no LLM-tool names hard-coded in schemas/steps; tool wiring stays in agents.yaml/routes (the `pi`/`claude`/`cursor` special-casing already exists in `invoke_tool` and is ported verbatim, not newly introduced).
- `detect-workflow-issues.sh` is shared with `skills/orchestrate` — must remain a callable shell script; the Python path invokes it as a subprocess.
- verify commands repo-root-relative — no absolute paths, no `cd /abs &&`.

## Trade-offs

- **Subprocess call to `detect-workflow-issues.sh` instead of porting it.** Sacrifices in-process purity for ~204 lines un-rewritten and a single source of truth shared with the LLM driver. Acceptable: the inline-script path already coexists with shell helpers, and a duplicate Python port would drift from the shell version.
- **Behavior change on two error channels (exit 4/5 → record-and-loop).** Sacrifices literal behavior-preservation for the AC-2 "pure loop" goal: the shell loop only understands exit 0/1/2/3, so unknown-agent and malformed-COMPLETION must become recorded failures that re-dispatch, exactly like tool-crash already does. Acceptable and arguably better — uniform error handling that participates in retry/cap instead of hard-killing the run.

## Acceptance Criteria

- AC-1: Given an agent step dispatched by `orchestrator next` (exit 0 with `agent` set), when `bin/orchestrator` runs it, then `run_agent_step` resolves the tool, assembles the prompt (instruction + workflow meta + ticket context + agent overlay), spawns the subprocess, parses COMPLETION, calls `orchestrator done` with the step-history entry, and exits 0 — verified by a pytest unit test asserting the recorded payload and exit code with a stub tool. [traces: UC-1]
- AC-2: Given the migrated driver, when `run-workflow.sh` is inspected, then it contains no `invoke_tool`, no `build_prompt`, no `resolve_agent_tool`/`resolve_tool`, no `parse-completion.py` invocation, no `build-payload` invocation, and no `DONE_PAYLOAD` construction — verified by a grep assertion over `run-workflow.sh`. [traces: UC-2]
- AC-3: Given a state.yaml with `worktree_path` set, when an agent step runs, then the subprocess cwd equals `worktree_path`; given no `worktree_path`, cwd falls back to repo_root — verified by a pytest test on `resolve_agent_cwd`. [traces: UC-3, UC-E4]
- AC-4: Given an agent step, when the prompt is built in Python, then it contains the agent overlay (`.orchestrator/agents/<agent>.md`), the ticket context (when ticket_id present), and the workflow meta block — verified by a pytest test on `build_agent_prompt`. [traces: UC-4]
- AC-5: Given the full migration, when `pytest orchestrator_next/tests/` runs and a full end-to-end workflow run executes, then both pass clean; the bats agent-path coverage in `test_run_workflow.bats` is retired and replaced by equivalent pytest unit tests against `agent_runner` — verified by a green pytest run and a documented bats retirement. [traces: UC-1, UC-E1, UC-E2, UC-E3]
- AC-6: Given the migrated driver, when an agent step runs, then the pre-step hook (`.orchestrator/hooks/pre-step.sh`) is still invoked by `run-workflow.sh` before `orchestrator next` (loop-level, best-effort `|| true`) and `run_agent_step` does not invoke it — verified by inspection that `run_pre_step_hook` is retained in the pure-loop `run-workflow.sh` and absent from `agent_runner`. [traces: UC-E5]

## Decisions

- Extract `agent_runner.py` rather than inline in `main()` → mirrors the inline-script delegation pattern and makes logic unit-testable under `tdd_required` → `main()` stays thin; agent logic gets pytest coverage.
- Promote `parse-completion.py` → importable `orchestrator_next/parse_completion.py` (leave a shim only if a live caller needs the script form — grep before deleting) → removes the hyphen-import blocker → in-process parse, no subprocess.
- Keep `detect-workflow-issues.sh` as a subprocess call, not a Python port → preserves the single source shared with `skills/orchestrate` → ~204 lines un-rewritten, no drift risk.
- Convert exit-4 (unknown agent) and exit-5 (malformed COMPLETION) into recorded `failed` + return 0 → required to make `run-workflow.sh` a pure loop (AC-2); the loop only handles 0/1/2/3 → these failures now participate in retry/`max_spawn_failures` instead of hard-killing the run. Surfaced explicitly: this is NOT literal behavior-preservation.
- Pre-step hook stays in `run-workflow.sh` (resolves OQ-4) → it is loop-level orchestration, not per-step execution, and runs before `orchestrator next` regardless of step kind.
- pi-settings/model-tier resolution + logging move into Python (`resolve_route` + `run_agent_step` log line) (resolves OQ-3: pi-specific, kept pi-specific).

## Open Questions

- OQ-5 (does the skills layer still call `run-workflow.sh` post-migration) is deferred per Non-Goals — `run-workflow.sh` survives as the public loop entry, so no skills-layer change is required by this feature.
