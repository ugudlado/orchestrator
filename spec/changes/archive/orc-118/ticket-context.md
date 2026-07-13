Task ORC-118 - User-level model config: auto-load ~/.orchestrator/models.yaml + orchestrator models verb
==================================================
Status: To Do
Priority: medium
Labels: engine, config-split

Description:
--------------------------------------------------
Phase 1 of docs/plan-config-repo-split.md. Let anyone switch models by editing one file in their home dir - no env vars, no config checkout, no engine knowledge.

Changes:
- model_routes.py: consult ~/.orchestrator/models.yaml between ORCHESTRATOR_MODELS_CONFIG and the config-root models.yaml (per-tier merge, same dict.update pattern)
- New verb `orchestrator models`: print effective routing + source file per tier
- doctor: report which file each tier resolved from

Precedence (highest wins): ORCHESTRATOR_MODEL_ROUTE_OVERRIDES env > ORCHESTRATOR_MODELS_CONFIG file > ~/.orchestrator/models.yaml > <config_root>/models.yaml

Acceptance Criteria:
--------------------------------------------------
- [ ] #1 Editing ~/.orchestrator/models.yaml changes tier routing for the next run with no env vars set
- [ ] #2 Per-run overrides (env JSON and ORCHESTRATOR_MODELS_CONFIG) still win over the user file; config-root models.yaml remains the fallback
- [ ] #3 orchestrator models prints effective subprocess + model_id per tier and which file each value came from
- [ ] #4 doctor passes and shows the resolution source; existing model_routes tests green
