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
│   ├── steps/                    # Step dirs (contract.yaml + pack/prompt.md or script.sh)
│   └── workflows/*.yaml          # Workflow schemas
├── skills/*/SKILL.md             # Skills (entry points + agent definitions)
├── spec/
│   ├── project.yaml              # Repo-specific config
│   └── changes/archive/          # Completed features (active state lives in <repo>/.orchestrator/<slug>/)
├── tests/                        # Workflow validation tests
└── config/pricing.yaml           # Model pricing rates (USD/MTok)
```

Active workflow state lives in `<repo_root>/.orchestrator/<slug>/*_<schema>_state.yaml` (one file per schema run — e.g. separate files for `design` and `implement` on the same ticket), not under `spec/changes/`. Archived runs move to `spec/changes/archive/`.

### Quick Start

#### Config resolution (explicit — no cwd fallback)

The engine is an installable package (`pip install .` / `uv tool install git+<repo-url>`)
with `config/` bundled inside the wheel; the CLI works via the `orchestrator` console
script, `bin/orchestrator` (dev shim), or `python -m orchestrator_next`. The config root
must be set explicitly — `ORCHESTRATOR_CONFIG` (the config dir itself), or legacy
`ORCHESTRATOR_HOME` (repo root, config/ is a subdir). Unset → hard error:

```bash
export ORCHESTRATOR_CONFIG=$(orchestrator config-path)  # bundled/checkout config
```

#### Running a Feature

```bash
# Full workflow from ticket ID (design + implement + review, one chained run)
orchestrator run HL-287 --schema feature

# Alternative: specify a different repo path
orchestrator run HL-287 --repo /path/to/repo

# Or run phases as separate, independently-resumable invocations on the same
# ticket (same change_id carries worktree/branch/artifacts across runs):
orchestrator run HL-287 --schema design      # explore -> design.md/tasks.yaml -> design-review
orchestrator run HL-287 --schema implement   # implement -> review gate -> QA -> learn

# Complete phase only (after implementation) — "complete" is a workflow schema name,
# dispatched via the same <workflow> <ticket-id> form as "feature"/"bugfix"
orchestrator complete HL-287
```

#### State Management

```bash
# Advance to next step (state.yaml is a real path, not stdin)
orchestrator next state.yaml

# Append step event as JSON (payload on stdin, path as arg)
echo '{"step_id":"specify","phase":"specify","status":"completed"}' | orchestrator done state.yaml

# Visualize a workflow schema's DAG (takes a schema name, not a state file)
orchestrator graph feature
```

#### Prompt Optimization

```bash
export ORCHESTRATOR_PROMPT_OPTIMIZE=1
orchestrator optimize <slug> --repo <repo>
```

The freshness gate skips packs without new training scenarios since their latest
optimization run. Packs with `ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS`
below `train + 3` (or an underbudget `--max-external-calls`) are refused as
`underbudget`; a seed-identical GEPA candidate (`prompt-optimize` exit 3 /
`gepa-noop`) is recorded as `noop` and skips compare/retry. Set
`ORCHESTRATOR_PROMPT_EVAL=1` to enable prompt evaluation.
Scheduling is operator-owned; a cron runner can invoke the one-liner
`ORCHESTRATOR_PROMPT_OPTIMIZE=1 orchestrator optimize <slug> --repo <repo>`.

### Headless Runs (servers / CI)

Set `ORCHESTRATOR_HEADLESS=1` to mark a run unattended (cloud sessions with
`CLAUDE_CODE_REMOTE=true` count automatically). Two behaviors switch on:

- **State auto-commit**: every recorded step commits `.orchestrator/<slug>/` on the current
  branch (`git add -f` — the dir is gitignored), so an ephemeral run resumes from the branch.
  On a blocked exit (2) or script abort (3) the state is also pushed (`git push origin HEAD`).
- **Exit-2 notification**: set `ORCHESTRATOR_NOTIFY_CMD` to any shell command; on a block it
  receives a JSON event on stdin — `{event, change_id, schema, reason, state_yaml_path}`.
  Point it at curl, a Slack CLI, whatever. Notification works with or without headless mode.

Resume after a block: pull the branch and re-run `orchestrator run <id> --schema <s>` —
state resolution is idempotent and picks up the existing state file.

### Workflow Phases

The core schemas are `design`, `implement`, `feature`, and `bugfix`; each embeds its own
automated review gate. Other schemas (`patch`, `autopilot`) are step-list variants of these.

1. **Design** (`explore` → `design` → `design-review`) → design.md + tasks.yaml
2. **Implement** (`implement` → `ticket-review` → `review` → `ticket-qa` → `learn`) → code + quality gate + learned train scenarios
3. **Feature/Bugfix** chain both phases in one run (`bugfix` starts with `diagnose` instead of `explore`)

`complete` is a separate teardown schema (archive, merge, worktree removal) run after review passes.

### State File Format

`state.yaml` tracks workflow state:

```yaml
schema: # Workflow schema (feature|bugfix|chore|spike)
  version: 1
  type: feature

step_history: # Terminal steps recorded in metrics
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
- state-dir-location: Active state lives in <repo\*root>/.orchestrator/<slug>/\*\*<schema>\_state.yaml, not spec/changes/<id>/ or worktree root
- worktree-branch-sync: Check branch divergence before merge phase
- bash-fragility-prefer-python-for-new-code: Python for YAML/state logic
- orchestrator-next-simplified (Jun 2026): No typed I/O, no repeat_until loop, no flat-file contracts — all 21 step contracts are directory-form (contract.yaml + prompt.md or script.sh). See docs/simplification-june-2026.md.
- steps-as-skills (Jul 2026): Workflow/contract declares `run:` | `prompt:+model:`.
  `prompt:` is a relative path to a `.md` file resolved via prompt search dirs (`ORCHESTRATOR_PROMPT_PATH` overrides; default `<repo>/skills` then engine `skills/`) —
  `<name>/SKILL.md` (skill conventions) or any other `.md` (verbatim).
  Skills live under `skills/`; learn appends scenarios by colocation.
```

### Quality Gates

```yaml
quality_bar:
  min_phase_review_score: 9 # Minimum quality score per phase
  max_retry_rounds: 8 # Max retries per step
  max_spawn_failures: 3 # Max agent spawn failures
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

| Command                                       | Description                                                                                                              |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `orchestrator run <id>`                       | Run full workflow from ticket                                                                                            |
| `orchestrator complete <id>`                  | Run the "complete" workflow schema (verify, sign-off, merge, archive) — same dispatch path as `run`, not a distinct verb |
| `orchestrator next <state.yaml>`              | Dispatch next step                                                                                                       |
| `orchestrator done <state.yaml>`              | Append step event (JSON on stdin)                                                                                        |
| `orchestrator graph <schema>`                 | Render Mermaid DAG of a workflow schema                                                                                  |
| `orchestrator validate-workflow <schema>`     | Validate a workflow schema file                                                                                          |
| `orchestrator reset-step <step> <state.yaml>` | Reset a step for re-run                                                                                                  |
| `orchestrator doctor`                         | Run diagnostics                                                                                                          |

### Skills Entry Points

Skills are the interface to workflow actions:

- `/orchestrate` → Shell out to `orchestrator run <id> --schema <name>` (in-process dispatch loop in `orchestrator_next/run_loop.py`). In a cloud/Slack session (`CLAUDE_CODE_REMOTE=true`) this routes to [`DRIVE.md`](DRIVE.md) instead — a different execution model where the session self-drives `next`/`done` and is the model for every step (see `docs/cloud-environment.md` for setup).
- `/specify` → Create specification artifacts
- `/design` → Design phase only — design.md + tasks.yaml, stops after design-review passes
- `/implement` → Execute implementation phase, including the automated review gate + learn cycle
- `/complete-feature` → Verify, sign-off, merge, archive
- `/autopilot` → Self-improving iteration (includes complete phase)
- `/backlog-manager` → Task lifecycle operations
- `/linear` → Linear issue management (if configured)
- `/context-hub` → Curated library documentation
- `/doctor` → Run orchestrator health check
- `/approve-qa`, `/rework` → QA sign-off / send back for rework
- `/commit-group` → Group unstaged changes into atomic commits
- `/ideate` → Brainstorm and prioritize the backlog
- `/systematic-debugging` → Methodical bug investigation
- `/workflow-creator` → Scaffold new orchestrator workflow schemas

See `skills/` for the full list of 24 skill directories.

### Testing

```bash
pytest tests/ -q
```

### Getting Help

```bash
orchestrator --help
```

---

## Cursor Cloud specific instructions

Environment setup (deps + wiring) is handled by the startup update script (`pip install -e .` + `pytest`/`ruff` + the two symlinks below). Notes here are the non-obvious caveats for running things.

- **Interpreter:** only `python3` exists (no `python`). `DRIVE.md`/`.claude/cloud-setup.sh` say `python`; use `python3` (or the installed `orchestrator` console script).
- **Install:** `pip install -e .` works and is the intended install (console script `orchestrator`, config shipped in-package). `orchestrator config-path` prints the config dir.
- **CLI needs an explicit config root.** `config_root()` has no cwd/bundled fallback: every dispatch verb (`next`, `graph`, `validate-workflow`, `doctor`) raises `ConfigRootError` unless `ORCHESTRATOR_CONFIG` or `ORCHESTRATOR_HOME` is set. Run with `export ORCHESTRATOR_CONFIG=$(orchestrator config-path)` (or `ORCHESTRATOR_HOME=<repo>`). The test suite sets this itself.
- **Lint:** `ruff check orchestrator_next/` (config in `pyproject.toml`).
- **Canonical tests:** `pytest orchestrator_next/tests/ -q` from the repo root — expect `292 passed, 5 xfailed`. Two gotchas:
  - Do **not** have `ORCHESTRATOR_SKIP_USAGE_CHECK` set when running the suite — it disables usage validation and makes `test_record_validation.py::TestCheckB` fail. (It's only for the DRIVE.md self-drive loop.)
  - Tests default `ORCHESTRATOR_HOME` to `~/.config/orchestrator` and several invoke the CLI at `~/.local/bin/orchestrator`. The update script creates both symlinks (config → repo `config/`, CLI → repo `bin/orchestrator`); without them ~12 tests fail with "schema not found" / "orchestrator not found".
- **Drive loop auto-commits + pushes in headless mode.** With `CLAUDE_CODE_REMOTE=true` (or `ORCHESTRATOR_HEADLESS=1`), every `orchestrator next`/`done` auto-commits `.orchestrator/<slug>/` and pushes on block (`record.py:autocommit_state`, for cloud resume per DRIVE.md). This creates `wip: orchestrator state for <slug>` commits on your current branch. When smoke-testing the loop, do it on a throwaway branch/scratch repo or leave `CLAUDE_CODE_REMOTE` unset — otherwise it mutates your working branch. Self-drive: `orchestrator run <slug> --schema <s> --seed-only` seeds `.orchestrator/<slug>/`, then loop `next`/`done`.
- **Backlog tickets are HTTP REST now.** Ticket steps (`load-ticket-context`, `ticket-start`/`review`/`qa`, `ticket-done`) source `config/steps/lib/backlog-api.sh` and call the Backlog REST API using **`BACKLOG_URL` + `BACKLOG_TOKEN` + `BACKLOG_PROJECT_ID`** (VM env vars → add via the Secrets panel). This repo's `spec/project.yaml` has `ticketing: backlog`, so these steps **fail the run (exit 1) when the creds are missing** — the three secrets are required to drive a real workflow here (`load-ticket-context` is the second step after `create-worktree`). Repos with a non-backlog `ticketing` no-op these steps.
- **Backlog MCP is HTTP.** `.mcp.json` defines an HTTP MCP at `${BACKLOG_URL}/projects/${BACKLOG_PROJECT_ID}/mcp` (`Authorization: Bearer ${BACKLOG_TOKEN}`) in Claude Code format. For **Cursor Cloud agents**, HTTP MCP servers are *not* loaded from repo files — register the backlog server via the Cursor cloud MCP config (personal dropdown at `cursor.com/agents` or team Dashboard → Integrations & MCP) with the token in `headers`. The engine's own ticket transitions do not need the MCP — they use the REST env vars above.
- **bats shell tests (`tests/*.bats`):** need `bats` (not in the update script; `sudo apt-get install -y bats`). Most are **stale** — they reference scripts removed in the June-2026 simplification and fail on a clean checkout regardless of environment. Only `tests/bats/render-retro.bats` and `tests/bats/inline_script_cwd_anchor.bats` pass. Not part of the canonical verify.

---
