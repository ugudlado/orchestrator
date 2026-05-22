# Tasks — One developer spawn per task + max_parallel + pure-orchestration driver + step classification

## Group A — execute-next-task: one spawn per task

- [x] T-1: Write tests for the per-task execute-next-task contract (RED — tests must fail)
  Why: AC-2 — execute-next-task must dispatch one developer agent per task, not one spawn for all tasks.
  Files: config/scripts/orchestrator_next/tests/test_execute_next_task_per_task.py
  Change: New pytest module. Load `config/steps/execute-next-task.yaml` and assert: the `instruction:` scopes a spawn to ONE assigned task (no "complete all tasks" / "One developer spawn completes all tasks" language); `repeat_until: all_tasks_completed` is still present; `agent: developer`, `inputs: [tasks.md]`, `outputs: [task_execution_result]` are unchanged; the instruction names the implement→verify→commit→mark-[x] sequence for the one task. Fails today — the contract still says one spawn does all tasks.
  Test scenarios:
    - the contract instruction contains no "all tasks" / "all unchecked" loop language
    - `repeat_until: all_tasks_completed` is retained
    - the instruction describes implement + verify + commit + mark-[x] for a single assigned task
    - `flags_read:` declares `max_parallel`

- [x] T-2: Rewrite execute-next-task.yaml as a per-task contract (GREEN — make T-1 pass)
  Why: AC-2 — one spawn implements one task; repeat_until re-dispatches until tasks.md is drained.
  Files: config/steps/execute-next-task.yaml
  Change: Rewrite the `instruction:` from the all-tasks loop to: (1) the driver supplies the assigned `task_id`; (2) implement that one task; (3) run project verification per quality_bar; (4) `git commit` per `contracts/auto-commit.md`; (5) mark that task `- [ ]` → `- [x]` in tasks.md; (6) return COMPLETION. Remove the "One developer spawn completes all tasks" line and the all-tasks loop steps. Keep `repeat_until: all_tasks_completed`, `agent: developer`, `inputs`, `outputs`. Add a `flags_read:` block declaring `max_parallel`. Bump `version:`. Keep the regression gate, retry, and rollback agent-side, scoped to the one task.
  Test scenarios:
    - all T-1 tests pass
    - the contract loads and parses as valid YAML
  depends: T-1

- [x] T-3: Update the developer agent contract to one-task scope (GREEN — supports T-2)
  Why: AC-2 — the developer agent must implement one assigned task (implement, verify, commit, mark, return), not drain a queue.
  Files: agents/developer.md, skills/developer/SKILL.md
  Change: Where `agents/developer.md` / `skills/developer/SKILL.md` describe completing "all unchecked items" or draining the tasks.md queue, change the language to "implement the one assigned task — implement, verify, commit, mark it `- [x]`, return COMPLETION." Keep the agent owning its own commit and `[x]` marking (intentional per design.md § reframed organizing principle).
  Test scenarios:
    - grep confirms agents/developer.md and skills/developer/SKILL.md describe one-task scope, not all-tasks
    - the agent still owns commit + [x] marking for its task
  depends: T-2

- [x] T-4: Review checkpoint — Group A (phase gate)
  Why: phase gate — confirm the per-task contract and developer agent are consistent before wiring the flag and driver.
  Test scenarios:
    - type-check clean
    - full test suite green
  depends: T-3

## Group B — max_parallel flag

- [x] T-5: Write tests for the max_parallel flag (RED — tests must fail)
  Why: AC-3 — max_parallel must be a behavioral flag (default 1, integer) with a CLI binding, flowing into state.yaml.flags.
  Files: config/scripts/orchestrator_next/tests/test_max_parallel_flag.py
  Change: New pytest module. Assert `flags.yaml` registers `max_parallel` under `behavioral:` with `default: 1` and a description; assert a `cli:` entry `--max-parallel` sets `max_parallel`; assert that flag resolution carries the value as an INTEGER (not coerced to bool) into `state.yaml.flags` through the `cli_flags > state_flags > schema_defaults` merge. Fails today — `max_parallel` is not in flags.yaml.
  Test scenarios:
    - flags.yaml behavioral block contains `max_parallel` with default 1
    - a `--max-parallel N` cli binding sets the flag to integer N
    - resolved flags carry max_parallel as an int; default resolves to 1 when unset
    - the first non-boolean behavioral flag does not break boolean-flag resolution

- [x] T-6: Add max_parallel to flags.yaml and the flag-merge path (GREEN — make T-5 pass)
  Why: AC-3 — register the flag and ensure the integer value survives the merge.
  Files: config/flags.yaml
  Change: Add `max_parallel: { default: 1, description: "Max concurrent developer spawns per task in execute-next-task" }` under `behavioral:`. Add `--max-parallel: { sets: { max_parallel: <N> } }` under `cli:`. If the flag-merge code path (parser/seed-state) coerces behavioral flags to bool, fix it to preserve the declared value type — this is the first integer behavioral flag.
  Test scenarios:
    - all T-5 tests pass
    - flags.yaml parses as valid YAML
  depends: T-5

- [x] T-7: Review checkpoint — Group B (phase gate)
  Why: phase gate — confirm the flag resolves correctly before the driver consumes it.
  Test scenarios:
    - type-check clean
    - full test suite green
  depends: T-6

## Group C — Driver dispatch-loop: per-task spawn + bounded parallelism

- [x] T-8: Write tests for the per-task / bounded-parallel dispatch loop (RED — tests must fail)
  Why: AC-3 — the driver must spawn min(max_parallel, ready_set) developer agents per loop iteration over the ready-task set.
  Files: config/scripts/orchestrator_next/tests/test_driver_per_task_dispatch.py
  Change: New pytest module. Drive a fixture state.yaml + tasks.md and assert: when the current step is `execute-next-task`, the ready-task set is derived from `orchestrator ready` plus a tasks.md scan for `- [ ]` tasks whose `depends:` are all `[x]`; at `max_parallel: 1` exactly one task is dispatched per iteration; at `max_parallel: 3` up to 3 independent ready tasks are dispatched concurrently; a task with an unsatisfied `depends:` is not dispatched. Use the existing test driver fixture pattern. Fails today — the driver spawns one all-tasks agent.
  Test scenarios:
    - the ready set = `- [ ]` tasks whose depends: are all `[x]`
    - max_parallel:1 → one developer spawn per loop iteration
    - max_parallel:3 with 3 independent ready tasks → 3 concurrent spawns; spawn count = min(max_parallel, len(ready))
    - a task whose depends: includes a still-`[ ]` task is excluded from the ready set
    - repeat_until re-dispatches execute-next-task until tasks.md has zero `- [ ]`

- [x] T-9: Update the orchestrate skill dispatch loop for per-task bounded-parallel spawn (GREEN — make T-8 pass)
  Why: AC-3 — the driver consumes orchestrator ready, reads max_parallel, and spawns bounded-concurrent per-task developer agents.
  Files: skills/orchestrate/SKILL.md
  Change: In the dispatch loop (SKILL.md §3, lines ~120-195): when `orchestrator next` returns the `execute-next-task` action, the driver (1) derives the ready-task set via `orchestrator ready` + a tasks.md `- [ ]`/`depends:` scan, (2) reads `max_parallel` from `state.yaml.flags` (default 1), (3) spawns up to `max_parallel` developer agents concurrently (run_in_background: true), one per independent ready task, each prompt carrying the assigned `task_id`, (4) collects each COMPLETION and calls `orchestrator done` once per spawn, (5) loops — repeat_until re-dispatches. Update the spawn-prompt guidance: "pass full tasks.md queue; agent completes all unchecked items" → "pass the assigned task_id; agent completes that one task."
  Test scenarios:
    - all T-8 tests pass
    - the SKILL.md loop no longer instructs the agent to drain the whole queue
  depends: T-8

- [x] T-10: Review checkpoint — Group C (phase gate)
  Why: phase gate — confirm per-task dispatch and parallelism work end to end before the audit.
  Test scenarios:
    - type-check clean
    - full test suite green
    - an end-to-end fixture: execute-next-task dispatches one agent per task; repeat_until drains tasks.md
  depends: T-9

## Group D — Driver / skills pure-orchestration audit

- [x] T-11: Write the driver pure-orchestration regression-guard test (RED — fails until T-9 lands)
  Why: AC-4 — the dispatch loop must carry no deterministic ticket or state side effects.
  Files: config/scripts/orchestrator_next/tests/test_driver_pure_orchestration.py
  Change: New pytest module. Grep `skills/orchestrate/SKILL.md`'s dispatch-loop section and `skills/{developer,reviewer,linear}/SKILL.md` and assert: no `backlog task edit --check-ac|--notes|--final-summary`, no `git commit`, and no direct `state.yaml` Write/Edit appears inside the driver's dispatch loop. The skill files are largely clean today; the assertion is tied to the SKILL.md loop after the T-9 rewrite to lock the pure-orchestration property.
  Test scenarios:
    - the orchestrate SKILL.md dispatch loop contains no backlog task edit / git commit / state.yaml mutation
    - developer/reviewer/linear skills delegate ticket transitions to /backlog-manager, not inline
    - the loop is a next → spawn → done wrapper with no deterministic glue

- [x] T-12: Relocate or remove any deterministic glue found in the driver/skills (GREEN — make T-11 pass)
  Why: AC-4 — any deterministic side effect the driver/skills perform around the dispatch loop must be removed or relocated per the litmus classification.
  Files: skills/orchestrate/SKILL.md
  Change: If the T-11 audit finds deterministic ticket/state glue inside the dispatch loop, remove it or relocate it (a deterministic step → a `run:` step contract; a ticket transition → `/backlog-manager` outside the loop). If the audit finds none — the loop is already a thin next/done wrapper — this task is a no-op confirmation: annotate `(no RED — audit found the loop already clean)` and the T-11 test stands as the permanent guard.
  Test scenarios:
    - all T-11 tests pass
    - the dispatch loop performs pure orchestration only
  depends: T-11

- [x] T-13: Review checkpoint — Group D (phase gate)
  Why: phase gate — confirm the pure-orchestration property holds and is guarded.
  Test scenarios:
    - all T-11 tests pass
    - full test suite green
  depends: T-12

## Group E — Classification artifacts: project.yaml rule, CONVENTIONS.md, audit

- [x] T-14: Write the rule-merge propagation test for the `step-classification` rule (RED — must fail)
  Why: AC-5 — the project.yaml litmus rule must verifiably reach an agent step's merged rules via rule-merge.
  Files: config/scripts/orchestrator_next/tests/test_step_classification_rule.py
  Change: New pytest module. Reuse the established fixture pattern in `test_generate_plan.py` — `_make_project_yaml(tmp_path, rules)` (line 38) and `generate_plan(str(state_path))` (lines 142, 219). Build a fixture project.yaml with a named rule `id: step-classification`, no `when:`, run `generate_plan` over the feature schema, and assert the rule text appears in `workflow_plan[<phase>].nodes[].rules` for at least one `agent:` node; a negative control without the rule asserts absence. Fails today — no such test exists. Prior art: `test_generate_plan.py` lines 38, 142, 219.
  Test scenarios:
    - generate_plan over the feature schema yields agent-step nodes carrying the `step-classification` rule text
    - a project.yaml WITHOUT the rule produces a plan with no such rule text (negative control)
    - the rule, having no `when:`, is active regardless of flag values

- [x] T-15: Add the `step-classification` named rule to project.yaml (GREEN — make T-14 pass)
  Why: AC-5 — propagate the LLM-vs-script classification principle into every plan via rule-merge.
  Files: spec/project.yaml
  Change: Append a named rule to the `rules:` block (after the `agent-agnostic` entry, ~project.yaml:248): `id: step-classification`, no `when:`, `rule:` stating the litmus test — "Classify each step by judgment vs determinism: if a script given this exact input could produce the right output every time, use run:; if it must weigh, interpret, or generate, use agent:. The burden of proof is on agent:. A step bundling judgment with a deterministic side effect must be split into separate nodes." Tool-agnostic wording.
  Test scenarios:
    - all T-14 tests pass
    - project.yaml parses as valid YAML
    - generate_plan over all four shipping schemas still succeeds
  depends: T-14

- [x] T-16: Write the design.md audit + CONVENTIONS.md placement guard tests (no RED — content artifacts)
  Why: AC-1, AC-6 — the classification audit must be complete; the CONVENTIONS.md section must be correctly placed.
  Files: config/scripts/orchestrator_next/tests/test_step_classification_docs.py
  Change: New pytest module. (a) List `config/steps/*.yaml` step-contract ids, parse design.md's Step Classification Audit, assert every id appears exactly once across its sub-sections. (b) Read `config/steps/CONVENTIONS.md`, assert a `## Step Classification` heading exists between `## Single Responsibility Principle` and `## Structure` and its body contains the litmus-test sentence. No RED phase — these guard content artifacts.
  Test scenarios:
    - the set of ids in design.md's audit equals the set of step-contract ids on disk
    - the audit records a lane (run:/agent:/pre-init) for every contract
    - `## Step Classification` is present in CONVENTIONS.md, positioned after Single Responsibility Principle and before Structure
    - the CONVENTIONS.md section body contains the litmus test and the unit-of-work split rule

- [x] T-17: Finalize the design.md audit and add the CONVENTIONS.md § Step Classification section (GREEN — make T-16 pass)
  Why: AC-1, AC-6 — reconcile the audit against the live contract set and document the litmus test as the authoring procedure.
  Files: spec/changes/orc-66/design.md, config/steps/CONVENTIONS.md
  Change: Re-grep `config/steps/*.yaml` and reconcile design.md's `## Step Classification Audit` so every id appears exactly once in the correct sub-section, adjusting the per-section counts if the live set differs from the 19/9/1 snapshot. Insert a `## Step Classification` section into CONVENTIONS.md between `## Single Responsibility Principle` (~line 54) and `## Structure` (~line 56): the litmus test, the burden-of-proof-on-agent rule, the unit-of-work split rule (deciding a commit message = agent output; an agent committing its own task is the agent owning its unit, not glue), and a one-line cross-reference to Single Responsibility. No dangling forward reference.
  Test scenarios:
    - all T-16 tests pass
    - the audit's sub-section counts sum to the total step-contract count on disk
  depends: T-15, T-16

- [x] T-18: Review checkpoint — Group E (phase gate)
  Why: phase gate — final integration: classification artifacts consistent, full suite green.
  Test scenarios:
    - type-check clean
    - full test suite green
    - design.md, project.yaml, CONVENTIONS.md render correctly; no broken cross-references
  depends: T-17
