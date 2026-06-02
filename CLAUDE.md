# CLAUDE.md

Read `spec/project.yaml` for all project context — vision, architecture, tech stack, quality bars, rules, gotchas, and learnings.

---

## CLI & Workflow Documentation

This repo implements the **orchestrator** workflow engine — a config-driven, LLM-agnostic framework for deterministic multi-step workflows. The orchestrator CLI drives features through formal phases with state tracking in `state.yaml`.

### Repository Structure

```
orchestrator/
├── bin/orchestrator              # CLI entry point (Python)
├── config/
│   ├── scripts/                  # Workflow driver scripts
│   ├── steps/                    # Step directories (contract.yaml + prompt.md or script.sh)
│   └── workflows/*.yaml          # Workflow schemas
├── skills/*/SKILL.md             # Skills (includes agent definitions)
├── skills/*.md                   # Skill dispatchers (entry points)
├── spec/
│   ├── project.yaml              # Repo-specific config
│   └── changes/<slug>/**         # Active feature artifacts
│       └── archive/              # Completed features
├── tests/                        # Workflow validation tests
└── config/pricing.yaml           # Model pricing rates (USD/MTok)
```

### Quick Start

#### Running a Feature

```bash
# Full workflow from ticket ID
orchestrator run HL-287 --schema feature

# Alternative: specify a different repo path
orchestrator run HL-287 --repo /path/to/repo

# Complete phase only (after implementation)
orchestrator complete HL-287
```

#### State Management

```bash
# Advance to next step
cat state.yaml | orchestrator next -

# Append step event as JSON
echo '{"step_id":"specify","phase":"specify","status":"completed"}' | orchestrator done -

# Visualize workflow DAG
orchestrator graph state.yaml
```

### Workflow Phases

Each feature progresses through formal phases:

1. **Specify** → Create spec.md, tasks.md, id.md
2. **Diagnose** → Plan approach, select tools/tech
3. **Implement** → Execute tasks, mark AC complete
4. **Complete** → Verify, sign-off, merge, archive

### State File Format

`state.yaml` tracks workflow state:

```yaml
schema:  # Workflow schema (feature|bugfix|chore|spike)
  version: 1
  type: feature

step_history:  # Terminal steps recorded in metrics
  - step_id: specify
    phase: specify
    status: completed
    agent: discoverer
    attempt: 1
```

### Metrics & Cost Tracking

- Step duration, tokens, and cost recorded in `step_history[].usage` in state.yaml
- Cost computed at record time using `config/pricing.yaml` (static YAML, no DB required)
- Pricing rates: USD per million tokens by model_id with `effective_from` dating

### Key Learnings

```markdown
- workflow-plan-upfront: Write full workflow_plan at init time
- file-level-symlinks: Use per-file symlinks for agents, per-dir for skills
- autopilot-must-complete: Never skip complete phase (learn + metrics)
- metrics-db-derived: Cross-repo metrics derived at bootstrap time
- specify-phase-scope-churn-cost: Front-load scope constraints in description
- worktree-state-dir-path: State dir is spec/changes/<id>/, not worktree root
- worktree-branch-sync: Check branch divergence before merge phase
- bash-fragility-prefer-python-for-new-code: Python for YAML/state logic
```

### Quality Gates

```yaml
quality_bar:
  min_phase_review_score: 9        # Minimum quality score per phase
  max_retry_rounds: 8              # Max retries per step
  max_spawn_failures: 3            # Max agent spawn failures
```

### Exit Codes (ORC-45 Protocol)

- `0 + JSON` → Agent path; driver spawns Agent tool
- `0 + no JSON` → Inline script completed; driver loops
- `1` → Workflow complete (ready to merge/archive)
- `2` → Step blocked (requires user action)
- `3` → Error (ContractDispatchError, parse error, etc.)

### Rules Every Agent Must Follow

1. **evidence-based**: Never claim completion without verification
2. **minimal-diffs**: Keep changes scoped to current task
3. **spec-first**: Artifacts must exist before implementation
4. **state-sync**: Update state.yaml after every step
5. **retry-limit**: Max 3 retry attempts per step
6. **signoff-required**: User approval at phase boundaries
7. **agent-agnostic**: No LLM tool references in schemas/steps

### CLI Reference

| Command | Description |
|---------|-------------|
| `orchestrator run <id>` | Run full workflow from ticket |
| `orchestrator complete <id>` | Complete phase + merge |
| `orchestrator next <state.yaml>` | Dispatch next step |
| `orchestrator done <state.yaml>` | Append step event (JSON on stdin) |
| `orchestrator graph <state.yaml>` | Render Mermaid DAG |
| `orchestrator expand-plan` | Append task-nodes to plan |
| `orchestrator doctor` | Run diagnostics |

### Skills Entry Points

Skills are the interface to workflow actions:

- `/orchestrate` → Shell out to `orchestrator run <id> --schema <name>` (single execution model via `run-workflow.sh`)
- `/specify` → Create specification artifacts
- `/diagnose` → Plan approach, select tech stack
- `/implement` → Execute implementation phase
- `/complete` → Verify, sign-off, merge, archive
- `/autopilot` → Self-improving iteration (includes complete phase)
- `/developer` → Claim & implement next dev ticket
- `/reviewer` → Claim & review next code review ticket
- `/backlog-manager` → Task lifecycle operations
- `/linear` → Linear issue management (if configured)
- `/context-hub` → Curated library documentation
- `/ctx-stats`, `/ctx-doctor`, `/ctx-purge`, `/ctx-upgrade` → Context-mode tools

### Testing

```bash
pytest orchestrator_next/tests/ -q
```

### Getting Help

```bash
orchestrator --help
```

---
