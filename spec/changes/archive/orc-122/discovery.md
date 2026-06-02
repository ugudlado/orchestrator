---
feature-id: orc-122
linear-ticket: ORC-122
---

# Discovery Brief: Orchestrator Graph with Cost/Token/Attempt Overlay

## Feature Summary

`orchestrator graph <schema> <slug>` currently renders a live phase DAG from a single state file, showing step status colors but no cost or performance data. This feature enhances the `graph` command to overlay per-step metrics (total tokens, USD cost, and retry count) onto each DAG node when a slug is provided. This makes the graph genuinely useful for post-run analysis: developers can immediately see which steps were expensive, slow, or unreliable.

## Personas & Actors

- **Developer**: runs `orchestrator graph <schema> <slug>` after a workflow completes to understand cost distribution and identify which steps needed retries.
- **Orchestrator CLI**: the `graph` subcommand that currently delegates to `render_graph(state)` or `render_workflow_graph(schema_name)`.
- **graph.py module**: renders Mermaid DAG output; the sole target of code changes.

## Use Cases

### Happy Path

UC-1: View cost overlay for completed run — Developer runs `orchestrator graph patch orc-120` and sees node labels like `implement-tasks\n9,278 tok · $5.41` with green fill.
UC-2: View overlay with retried step — Developer runs `orchestrator graph feature orc-118` and sees `run-phase-review\n6,200 tok · $0.75` with orange fill (attempt count > 1).
UC-3: View overlay as HTML — Developer runs `orchestrator graph patch orc-120 --html` and gets the annotated graph in browser with sidebar click-to-inspect still working.
UC-4: Static schema graph unchanged — Developer runs `orchestrator graph patch` (no slug) and sees the same plain topology graph as before, with no metrics.

### Error & Edge Cases

UC-E1: Script-only steps have no token data — Steps like `check-rerun` and `create-worktree` have `usage: {}` (no tokens/cost); their node labels show step ID only without a metrics line.
UC-E2: Multi-state run (feature + complete) — A feature workflow produces two state files in `.orchestrator/<slug>/`; aggregation must merge `step_history` from both files so complete-phase steps (e.g. `run-learn-cycle`) also show metrics.
UC-E3: Slug with no state files — `orchestrator graph feature nonexistent` exits with code 3 and an error message (existing behavior, unchanged).
UC-E4: All attempts abandoned, no final completed entry — Step shows accumulated tokens/cost from all attempts with the attempt count marker.

## Scope

### In Scope

- New `render_workflow_graph_with_overlay(schema_name, slug, state_dir)` function (or enhanced `render_workflow_graph`) in `orchestrator_next/graph.py`
- Multi-state aggregation reusing the same logic as `workflow_report_step.py`'s `_collect_all_states` (or a shared helper)
- Mermaid node label annotation: `step-id\nN tok · $X.XX` for agent steps with data
- Mermaid `style <node_id> fill:#f90` line for nodes with `attempts > 1`
- `--html` flag continues to work with overlaid step_data
- `orchestrator graph <schema>` (no slug) behavior unchanged

### Out of Scope

- Duration overlay in node labels (duration is in the HTML sidebar click-to-inspect, not the node label — ticket doesn't include it)
- Changes to the workflow-report step or metrics.py
- DuckDB integration (graph.py is explicitly "no DuckDB")
- New CLI flags beyond the existing `<schema> <slug> [--html]` interface

## UI Direction

N/A — no UI components. Output is Mermaid flowchart text to stdout, or an HTML page served locally via existing `--html` mode.

## Key Decisions

- **Aggregation location**: The `_collect_all_states` logic lives in `workflow_report_step.py` today. For graph.py (which must remain "no DuckDB, no step writes"), we can either duplicate the YAML-glob pattern or extract it to `orchestrator_next/parser.py` or a small shared helper. Given graph.py already imports from parser, a small `collect_all_state_histories(slug, state_dir)` function in graph.py is cleanest — avoids cross-module import from a config/steps script.
- **Build vs reuse**: Reuse the aggregation pattern from `workflow_report_step.py` (copy the YAML-glob approach, not import it — that file is a step script, not a library module). The data shape is identical: iterate all `*_state.yaml` files in `.orchestrator/<slug>/`, merge `step_history`, collapse by step_id summing tokens/cost and taking max attempt.
- **Style injection**: Mermaid's `style <id> fill:#f90` directive (one line per retried step) is the simplest approach — no new classDef needed.
- **Node label format**: `"step-id\nN tok · $X.XX"` for steps with data; plain `"step-id"` for script-only steps. Consistent with the ticket's example: `explore["explore\n9,278 tok · $1.05"]`.
- **Slug state directory**: `_resolve_slug_state` already resolves to the most recent state file from `.orchestrator/<slug>/`. For overlay aggregation, we need the parent directory (`.orchestrator/<slug>/`) to glob all state files — passed from `_graph_verb` which already constructs this path.

- **[VERIFIED T-0] Join key is the schema-step ID, not exploded per-task nodes.** Confirmed against live runs `.orchestrator/orc-120/*_state.yaml`, `orc-118`, `orc-99`, `orc-74`: `step_history` records aggregate schema-step IDs (`implement-tasks`, `run-phase-review`, `explore`) — implement-tasks is a single entry, NOT exploded into `T-1`/`T-2`. So keying overlay metrics by `render_workflow_graph` node ID lines up directly with `step_history[].step_id`. No id-mapping layer needed.

- **[VERIFIED T-0] Overlay targets the STATIC schema graph (`render_workflow_graph`), not the per-phase live DAG (`render_graph`).** The ticket example spans multiple phases (`explore` is in explore phase; `run-phase-review`/`implement-tasks` are in implement phase). `render_graph(state)` renders only the *current phase's* `workflow_plan` nodes via `phase_nodes(state, phase)` — it physically cannot show all three at once. Overlay must annotate `render_workflow_graph(schema_name)` output.

- **[VERIFIED T-0] Behavior change: `graph <schema> <slug>` is being repointed.** `render_graph` has exactly one caller (`bin/orchestrator:211`, the `<schema> <slug>` path). Repointing that path to the overlay drops the per-phase live-DAG view from the CLI. `render_graph` itself is retained as a library function (used by tests) but loses its CLI entry point. Documented in design.md Decisions.

- **[VERIFIED T-0] Aggregation contract = sum + max-attempt.** A retried step has multiple `step_history` entries (att=1, att=2 — confirmed in orc-99, orc-74). Overlay must sum tokens (`input_tokens`+`output_tokens`) and `cost_usd` across all entries for a step_id, and take `max(attempt)` as the retry marker. Identical to `_render_report`'s collapse logic. Node label uses summed tokens+cost; `attempts > 1` → orange `style` line.

- **[VERIFIED T-0] Multi-file aggregation required.** orc-120 has a `patch` state file (holds implement-tasks, run-learn-cycle) AND a `complete` state file (holds workflow-report, merge-to-main). Overlay must glob all `*_state.yaml` in `.orchestrator/<slug>/` and merge — single-file resolution misses complete-phase steps. Mirrors `workflow_report_step._collect_all_states`.

- **[VERIFIED T-0] Status fill out of scope.** ACs #2/#3 require only token/cost labels + orange fill for `attempts > 1`. `render_workflow_graph` has no status colors today; adding them is scope creep and creates a precedence question. Decision: plain nodes, orange only for retries. (Overrides discovery UC-1's "green fill" mention.)

- **[VERIFIED T-0] Phase-gate verify must be scoped.** Full suite `pytest orchestrator_next/tests/` is NOT green at HEAD (4 pre-existing failures: telemetry schema, 2 operator_workflows, capture-baseline). Per ORC-119 learning, the phase-gate task's verify is scoped to `pytest orchestrator_next/tests/test_graph_workflow.py -v`, not the full suite.

## Open Questions

- OQ-1: Should the overlay also annotate nodes with wall-clock duration (e.g. `implement-tasks\n9,278 tok · $5.41 · 4.2m`)? The ticket description does not include duration in the node label, but duration data exists in `usage.duration_ms`. Leaving out for now — easy follow-on.
- OQ-2: The `render_graph(state)` path for `graph <schema> <slug>` currently reads from a single state file (the most recent one). Should the overlay mode also try to aggregate across all state files for the slug, or just the single resolved state? Aggregation across all files (feature + complete runs) is more complete. The workflow_report precedent supports multi-file aggregation. Recommended: multi-file aggregation for overlay mode.
