---
feature-id: orc-118
linear-ticket: ORC-118
---

# Discovery Brief: Move Agent Step Execution into bin/orchestrator

## Feature Summary

Agent steps in the orchestrator workflow engine are currently dispatched and executed by `run-workflow.sh` — a bash script that handles prompt assembly, tool resolution, subprocess invocation, COMPLETION parsing, and done-payload recording for agent steps. `bin/orchestrator` already owns the equivalent lifecycle for inline scripts end-to-end (subprocess → record → exit 0 to loop). ORC-118 completes the migration begun in ORC-112 by moving agent step execution into `bin/orchestrator` (Python), leaving `run-workflow.sh` as a pure `orchestrator next` loop with no execution logic.

## Personas & Actors

- **Orchestrator driver (`run-workflow.sh`)**: the shell loop that calls `orchestrator next` and re-enters after each step; post-migration becomes a trivial dispatch loop
- **Orchestrator CLI (`bin/orchestrator`)**: Python entry point that currently handles inline scripts end-to-end and emits JSON for agent steps; post-migration handles both paths
- **Agent tools (claude, pi, cursor, omp)**: external CLI tools spawned by the CLI to execute agent steps; interface remains unchanged from agent perspective
- **Feature developer**: human or autopilot that triggers `orchestrator run <id>` and expects the same observable behavior before and after the migration

## Use Cases

### Happy Path

UC-1: Agent step executes via bin/orchestrator — orchestrator next emits an agent action; bin/orchestrator resolves the tool, assembles the prompt (instruction + ticket context + agent overlay + workflow meta), spawns the subprocess, captures stdout, parses the COMPLETION block, calls orchestrator done with the step history entry, and exits 0 so run-workflow.sh loops.

UC-2: run-workflow.sh as pure loop — after migration, run-workflow.sh calls `orchestrator next`, receives exit 0, and loops without any invoke_tool, parse-completion, or build-payload logic; it has no knowledge of the step kind (agent vs inline).

UC-3: Worktree cwd resolved in Python — bin/orchestrator reads `worktree_path` from state.yaml and uses it as the subprocess cwd for agent steps, matching what run-workflow.sh currently sets via AGENT_WORK_DIR.

UC-4: Agent overlay and ticket context injected in Python — bin/orchestrator appends repo-scoped agent overlay (`.orchestrator/agents/<agent>.md`) and ticket context (from backlog CLI) to the prompt before spawning the tool subprocess.

### Error & Edge Cases

UC-E1: Tool binary not found — bin/orchestrator resolves the agent-to-tool mapping from agents.yaml; if the binary is missing, it records a failed step entry and exits 0 so the driver can handle the failure loop.

UC-E2: COMPLETION block absent or malformed — bin/orchestrator's COMPLETION parser fails to find or parse the block in agent stdout; step is recorded as failed with the raw stdout as evidence; driver loops.

UC-E3: Agent subprocess exits non-zero — bin/orchestrator captures the non-zero exit, records a failed step entry (matching current behavior of run-workflow.sh on tool failure), and exits 0.

UC-E4: No worktree path in state — agent step runs with cwd=None (repo root), matching inline script fallback behavior.

UC-E5: Pre-step hook fails — if `.orchestrator/hooks/pre-step.sh` exits non-zero, step is not dispatched; error surfaced to driver.

## Scope

### In Scope

- Move agent step prompt assembly (instruction + step_context + ticket_context + workflow_meta + agent_overlay) into bin/orchestrator / Python
- Move tool resolution (agents.yaml routing, binary lookup, model tier selection) into Python
- Move subprocess invocation (args_template substitution, pi settings flags) into Python
- Move COMPLETION block parsing (currently parse-completion.py) into Python inline
- Move done-payload construction (currently state_inspect build-payload) into Python inline
- Move usage adapter normalization (tool-specific output parsing) into Python
- Move worktree cwd resolution from run-workflow.sh into bin/orchestrator
- Reduce run-workflow.sh to a pure `orchestrator next` loop
- Preserve all existing behavior: retry logic, workflow-issues detection, pre-step hooks, archive handling
- Full pytest suite passes (orchestrator_next/tests/)
- End-to-end workflow run passes

### Out of Scope

- Eliminating run-workflow.sh entirely (may survive as a trivial wrapper; that decision is deferred)
- Changing the agents.yaml/routes.yaml config format
- Changing the COMPLETION block contract (status values, field names)
- Changing the state.yaml shape
- Migrating the `orchestrator complete` subcommand
- Migrating metrics/duckdb integration
- DAG task-node changes (ORC-63/64/65 epic)

## UI Direction

N/A — no UI components. This is a backend refactor of the CLI dispatch path.

## Key Decisions

- **Migrate agent step execution to Python, not a new shell layer**: bin/orchestrator already owns inline script execution end-to-end; parity with Python is the stated goal (ORC-118 description, Implementation Notes).
- **Selected design direction: extract `orchestrator_next/agent_runner.py`** (not inline in `main()`). `main()` gains an `elif action.get("agent"):` branch that delegates to `run_agent_step(...)`, mirroring how the inline-script branch delegates to `step_runner`/`record`/`step_env`. Chosen over inlining because `tdd_required` + AC-5's pytest gate require the agent logic to be unit-testable in-process, and over a cwd-only minimal change because that fails AC-1/AC-2/AC-4. Complexity: M.
- **run-workflow.sh survival**: reduced to a pure `orchestrator next` loop (loop + pre-step hook + terminal-exit handling + archive-missing check). It survives as the public loop entry; OQ-5 (skills calling bin/orchestrator directly) is deferred — no skills-layer change required by this feature.
- **OQ-1 resolved — COMPLETION parser promoted to importable module**: `parse-completion.py` (hyphenated, non-importable) is moved to `orchestrator_next/parse_completion.py`; in-process call, no subprocess. A script shim is kept at the old path only if a live caller depends on it (grep first).
- **OQ-2 resolved — usage adapters already importable**: `usage_adapters.split_stdout` is package-root Python; reused directly, no port needed.
- **OQ-3 resolved — pi settings stay pi-specific**: `resolve_pi_settings` ports `state_inspect cmd_pi_settings`; pi flag prepend remains gated on `tool_name == "pi"`.
- **OQ-4 resolved — pre-step hook stays in run-workflow.sh**: it is loop-level orchestration that runs before `orchestrator next` regardless of step kind, not per-step execution.
- **`detect-workflow-issues.sh` stays shell, called as subprocess**: it is shared with the LLM `skills/orchestrate` driver; a Python port would duplicate and drift. The inline-script path already coexists with shell helpers.
- **Deliberate semantics change (surfaced, not silent)**: the current shell exits 4 (unknown agent) and 5 (malformed COMPLETION), hard-killing the loop. To make run-workflow.sh a pure loop (it only understands exit 0/1/2/3), these become recorded `failed` steps that return 0 → re-dispatch via `_compute_attempt` / block at `max_spawn_failures`. Tool-crash record-and-loop (UC-E3) is already the existing behavior. This is NOT literal behavior-preservation.
- **Test migration**: `tests/test_run_workflow.bats` is green today (10/10) and covers the shell agent path being deleted. Its agent-path scenarios are retired and replaced by pytest unit tests against `agent_runner`; a grep-based bats test asserts the pure-loop shape.
- **Build-or-extend**: extend `bin/orchestrator` (existing Python CLI); do not build a new entry point. The inline script path is the template for the agent path.

## Open Questions

- OQ-1: Should COMPLETION parsing reuse parse-completion.py as a subprocess call, import it as a Python module, or be reimplemented inline? The current script is ~130 lines; a clean Python port is straightforward but adds surface area.
- OQ-2: Usage adapter normalization (splitting tool stdout into assistant_text + usage tokens) is currently bash functions for each tool (claude, pi, cursor, omp). Should these be ported to Python functions in a new `agent_runner.py`, or kept as subprocess calls to the existing shell adapters?
- OQ-3: Pi settings resolution (`~/.pi/agent/settings.json`) currently feeds `--provider`/`--model` flags for the pi tool. Does this path need to survive for all tools or is it pi-specific? Clarify before building the Python equivalent.
- OQ-4: The `invoke_tool` function in run-workflow.sh handles pre-step hooks (`.orchestrator/hooks/pre-step.sh`). Should bin/orchestrator call the hook before spawning the agent subprocess, or does hook support stay in the shell loop?
- OQ-5: After migration, does run-workflow.sh still exist as a public interface (skills call it via `orchestrator run`), or does the skills layer need updating to call bin/orchestrator directly?
