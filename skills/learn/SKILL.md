---
name: learn
description: "Evaluate last feature's workflow compliance and route learned rules to step contracts and project.yaml learnings. Use after completing a feature, or when the user says \"learn\", \"evaluate workflow\", \"what did we learn\", \"update rules\"."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID to evaluate (defaults to most recently completed feature)
    required: false
---

## Variables

REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
REPO_NAME=${REPO_NAME:-$(basename "$REPO_ROOT")}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}

## Learn from Last Feature

$ARGUMENTS

## Overview

This skill runs the evaluation + self-improvement loop after a feature is completed. It assesses workflow compliance, routes learned rules to step contracts in `$ORCHESTRATOR_HOME/config/steps/` and project-specific learnings to `spec/project.yaml` `learnings:` section. Rules go into step contracts (deterministic, enforced at execution time). Project-specific learnings go into project.yaml (agent-agnostic, persists across sessions).

## Process

### 1. Find Context

Locate the most recent completed feature:
- Scan `$WORKFLOW_STATE_DIR/*/state.yaml` for the most recent file with `status: completed` or `phase: complete`
- Also check `$WORKFLOW_STATE_DIR/*/state.yaml` (state files are no longer in worktree-relative paths)
- Read the state.yaml for feature_id, schema, quality scores, phases
- Find the project root from the state.yaml path or cwd
- Read git log for the feature's commits and diff

If `$ARGUMENTS` contains a feature ID, use that instead of auto-detecting.

### 2. Gather Inputs

Collect the evaluator's inputs from state.yaml:
- **Step history**: read `step_history[]` — the full audit trail of every step (completed, skipped, failed, retried)
- **Per-step learnings**: extract all `learnings[]` entries from each step_history entry
- **Aggregate metrics**: read `metrics{}` — total steps, retries, skips, durations, token usage
- **Skip analysis**: read `metrics.skip_reasons{}` — why steps were skipped, how often
- **Retry analysis**: read `metrics.retry_reasons{}` — what caused retries, patterns
- **Quality report**: from `quality_scores[]` and step-level `metrics.review_score`
- **Project root**: path to spec/project.yaml (for learnings and vision context)

### 2b. Cross-Feature Retry Analysis

Before evaluating, scan archived state.yaml files across recent features to detect systemic retry patterns.

1. **Collect archive data**: Run `config/scripts/metrics-query.sh retry-hotspots --fleet --limit 10`; if the helper exits non-zero or returns empty, fall back to listing `spec/changes/archive/*/state.yaml` sorted by modification time (most recent first, limit 10). For each record, extract:
   - `feature_id`
   - `step_history[].retries` (retry count per step entry)
   - `step_history[].retry_reasons[]` (list of reason strings per retry)
   - `metrics.retry_reasons{}` (aggregate retry reason map, if present)

2. **Aggregate by step + reason**: Build a map:
   ```
   patterns[step_id][reason_category] = {
     feature_count: N,   // how many features had this step+reason combo
     total_retries: M,   // total retries across all features
     feature_ids: [...]  // which features
   }
   ```
   Normalize reason text by lowercasing and collapsing whitespace before grouping.

3. **Flag systemic patterns**: A pattern is systemic if:
   - `feature_count >= 3` (same step+reason appears in 3 or more features), AND
   - Retry rate for that step > 30% across those features (total_retries / total step executions > 0.30)

4. **Prepare pattern report**: For each systemic pattern, record:
   ```yaml
   systemic_retry_patterns:
     - step_id: <step_id>
       reason: <normalized reason>
       feature_count: N
       total_retries: M
       feature_ids: [...]
       suggested_target: <path to $ORCHESTRATOR_HOME/config/steps/<step_id>.yaml>
   ```

5. **Pass to evaluator**: Include `systemic_retry_patterns` in the evaluator prompt (step 3). If no systemic patterns are found, omit the section. When patterns exist, instruct the evaluator to:
   - Treat each pattern as a workflow design issue requiring a preventive rule or pre-check
   - For each: propose a concrete rule addition to the target step contract that would prevent the root cause
   - Route the fix to `workflow-improver` (same as other workflow issues)

### 3. Run Workflow Evaluation

Evaluate the feature with:
- The step_history audit trail (not a reconstructed report — the raw data)
- Per-step learnings aggregated by type (mistakes, insights, retries, decisions, skips)
- Aggregate metrics for pattern detection
- **Systemic retry patterns** from step 2b (if any) — include the full `systemic_retry_patterns` list with step IDs, reasons, counts, and suggested target contracts
- The step contracts directory path: `$ORCHESTRATOR_HOME/config/steps/`
- The step contract conventions: `$ORCHESTRATOR_HOME/config/steps/CONVENTIONS.md` (must read before suggesting changes)
- Instruction to run all 5 parts: compliance → step analysis → pattern detection → step contract updates → metrics write

**Step analysis**: The evaluator examines:
- **Skipped steps**: Were they justified? Do skip_reasons indicate a workflow design issue (step should be conditional)?
- **Retried steps**: Are retry_reasons systemic? Should a step contract rule prevent the root cause?
- **Mistakes**: Which ones are repeats of known issues? Which are new?
- **Insights**: Which should become rules in the appropriate step contract?
- **Duration outliers**: Steps taking >2x average may need decomposition
- **Drift events**: `skip_reason: "model drift"` entries indicate the workflow lost the model — tighten instructions
- **SRP violations**: Flag step contracts where the intent has multiple unrelated verbs, or where instruction contains rule-like paragraphs that belong in `rules:`. See `$ORCHESTRATOR_HOME/config/steps/CONVENTIONS.md`.

### 4. Route Findings

Classify each finding and route it to the right handler.

**Routing targets:**
- **Agent prompts** → `agents/<name>.md` (tighten instructions when the agent skipped something a contract already enforces)
- **Workflow rules** → step contracts in `$ORCHESTRATOR_HOME/config/steps/` (deterministic, enforced at execution time, shared across repos) — or `.orchestrator/` override for repo-specific shape changes
- **Project-specific learnings** → `spec/project.yaml` `learnings:` section (agent-agnostic, persists across sessions, repo-scoped)
- **Never write to CLAUDE.md** — it's a pointer file only.

#### 4a. Classifier (three axes)

Every finding belongs to exactly one of three buckets. The axes are
**who owns the miss**, not *what the fix looks like*.

| Bucket               | Owner of the miss                                   | Target                                                  |
|----------------------|-----------------------------------------------------|---------------------------------------------------------|
| `agent_improvement`  | Agent ignored or skipped an existing contract rule  | `agents/<name>.md` — tighten prompt/instructions        |
| `workflow_improvement` | Step/phase/gate is missing or wrong                | Step contract (global by default) or `.orchestrator/` override |
| `project_learning`   | Tech-stack / command / domain / path fact needed    | `spec/project.yaml` `learnings[]` or `rules[]`          |

**Classification order** — check in this order, first match wins. The
order matters: most misrouting happens when an agent miss gets rewritten
as a new workflow rule, which silently duplicates existing contract text.

1. **Does an existing step contract already cover this concern?**
   Read the relevant step contract (`run-implement-review.yaml`,
   `run-phase-review.yaml`, `execute-next-task.yaml`, etc.) before
   classifying. If the rule is already there and the agent's output
   shows it was skipped or handled incorrectly → **`agent_improvement`**.
   Do NOT add a duplicate rule to the contract. Fix the agent prompt.

   Signals: spec said X, step contract says "verify X", agent's review
   never mentioned X. That's an agent miss, not a workflow gap.

2. **Is the finding about a tool, command, path, domain entity, or
   repo subsystem?** (`pnpm`, `pytest`, a specific module name, legacy
   areas, per-team conventions) → **`project_learning`**. The workflow
   stays generic; the learning supplies the repo-specific fact the
   agent reads at runtime.

3. **Otherwise, the step/phase/gate is genuinely missing or wrong** →
   **`workflow_improvement`**. Then decide scope:
   - **Global** (default): the gap would affect any repo running this
     schema. Write to `$ORCHESTRATOR_HOME/config/steps/<step>.yaml` or
     the relevant schema in `config/workflows/`.
   - **Repo override** (rare): the change only makes sense for this
     repo (different phase ordering, an extra gating step this repo
     needs). Write to `$REPO_ROOT/.orchestrator/<path>` per the
     workflow-override contract. If a learning would solve it, prefer
     that instead.

**If unsure between buckets 1 and 3** — grep the target step contract
for the concern. If any rule already mentions it, bucket 1 wins.

**If unsure between buckets 2 and 3** — if the fix names a specific
command, file path, or stack tool, it's bucket 2. Workflow files stay
tool-agnostic.

After classification:
- `agent_improvement` → edit `agents/<name>.md` directly. No metadata
  comment, no `<!-- learned: -->` stamp. Agent prompts aren't subject to
  decay evaluation the way contract rules are.
- `workflow_improvement` (global) → spawn `workflow-improver` per the
  routing table below. Learned rule gets `<!-- learned: ... -->` metadata.
- `workflow_improvement` (repo override) → spawn `workflow-improver`
  with `$REPO_ROOT/.orchestrator/<path>` target; copy global file first
  if the override doesn't exist. Read `config/steps/contracts/workflow-override.md`.
- `project_learning` → append to `spec/project.yaml` `learnings[]` with
  `id`, `learned`, and `rule` fields. Never modify global step contracts.

**Routing decision tree:**

**Agent misses** (contract already enforces the concern, agent skipped it):
- Identify which agent owned the skipped step (reviewer, developer,
  architect, etc.) — check `state.yaml` step_history for the agent
  assigned to the failing step.
- Edit `agents/<name>.md` to make the existing requirement harder to
  skip: add an explicit checklist item, name the artifact to inspect,
  or move the check earlier in the prompt.
- Do NOT add a rule to the step contract — the contract already has one.

**Workflow issues** (schema gaps, step contract bugs, hook problems):
- Spawn the `workflow-improver` agent with:
  - The finding and its classifier bucket (always `workflow_improvement`)
  - Scope decision: global or repo override
  - The target file path resolved per the scope:
    - Global → `$ORCHESTRATOR_HOME/config/steps/<step>.yaml`
    - Repo override → `$REPO_ROOT/.orchestrator/steps/<step>.yaml` (copy global first if missing)
- Agent MUST read `$ORCHESTRATOR_HOME/config/steps/CONVENTIONS.md` before editing any step contract
- For repo overrides, agent MUST also read `$ORCHESTRATOR_HOME/config/steps/contracts/workflow-override.md` before writing under `.orchestrator/`
- Fix is applied immediately to disk — improves the next workflow execution

**Code/functionality issues** (bugs discovered, missing features, tech debt, test gaps):
- Create a Linear ticket with description, evidence, and suggested approach
- Do NOT fix inline — let the ideator prioritize it and the develop workflow execute it properly
- This ensures code changes go through full spec-first discipline

**Learned rules** (patterns to remember, gotchas discovered, quality checks):
- Apply the §4a classifier first. The bucket tells you WHERE to write;
  the routing table below (for `workflow_improvement` only) tells you
  WHICH step contract the rule belongs in.
- `agent_improvement` → edit `agents/<name>.md` — skip this table entirely
- `workflow_improvement` (global) → `$ORCHESTRATOR_HOME/config/steps/<step>.yaml`
- `workflow_improvement` (repo override) → `$REPO_ROOT/.orchestrator/steps/<step>.yaml`
- `project_learning` → append to `spec/project.yaml` `learnings[]` — skip
  this table entirely (no step contract involved)

  | When to enforce | Target step contract | Where in the file |
  |---|---|---|
  | During diagnosis/investigation | `diagnose.yaml` | `instruction:` section |
  | During implementation | `execute-next-task.yaml` | `rules:` list |
  | During review | `run-phase-review.yaml` | `rules:` or `instruction:` |
  | During implement review | `run-implement-review.yaml` | `instruction:` section |
  | At phase boundaries | `phase-signoff.yaml` | `instruction:` pre-conditions |
  | During artifact/task creation | `create-or-refresh-artifacts.yaml` | `rules:` list |

- Apply the rule to the appropriate section of the target step contract
- **IMPORTANT — Rule metadata**: When the workflow-improver writes a learned rule, it MUST append the metadata comment inline on the same line as the rule text:
  `<!-- learned: YYYY-MM-DD, source: FEATURE-ID, cycle: N, repo: REPO_NAME -->`
  Where: `YYYY-MM-DD` = today's date, `FEATURE-ID` = the feature being evaluated, `N` = current cycle count (run `config/scripts/metrics-query.sh cycle-count`; if it exits non-zero or is empty, fall back to `ls spec/changes/archive/*/state.yaml 2>/dev/null | wc -l`), `REPO_NAME` = `basename $(git rev-parse --show-toplevel)` (the repo that generated this rule).
  **Repo scoping**: Default to `repo: $REPO_NAME` (repo-scoped). Only use `repo: *` (universal) when the rule is about workflow mechanics itself (e.g., "always write next_step before spawn") and NOT about tech-stack, domain, or repo-specific patterns.
  This is required by `$ORCHESTRATOR_HOME/config/steps/CONVENTIONS.md` § Rule Lifecycle Convention.
  Permanent (hand-written) rules already in the step contract MUST NOT receive a metadata comment.

**Tooling rules** (eslint, knip config, build settings):
- Log as manual TODO — these need human oversight

### 5. Report

```
[learn] Evaluation complete for [feature-id]
  Verdict: [CLEAN/PASS/FAIL]
  Step contracts: +N rules added to M step contracts
  Workflow fixes: [applied/none needed]
  Metrics: cycle K written to .claude/metrics.jsonl
  Consecutive clean: N/3
```

### 5b. Rule Effectiveness Update (every invocation)

This sub-step runs on every learn invocation. It updates hit/miss counters on
learned rules based on the just-completed feature's step retry data.

**Update counters**:
1. Get recent completed features: run `config/scripts/metrics-query.sh recent-features --limit 10`; if it exits non-zero or returns empty, fall back to listing `spec/changes/archive/*/state.yaml`. Read the just-completed feature's `step_history[]` from the state.yaml.
2. Build a map: `step_retries[step_id] = total retry count for that step`.
   A step with no retries has count 0.
3. List all `$ORCHESTRATOR_HOME/config/steps/*.yaml` files.
4. For each file, grep for lines matching `<!-- learned:`. For each learned rule:
   - Parse the metadata fields: `learned`, `source`, `cycle`, `hits` (default 0), `misses` (default 0).
   - Determine the step_id from the filename (e.g., `execute-next-task.yaml` → `execute-next-task`).
   - If `step_retries[step_id] == 0`: increment `hits` by 1.
   - If `step_retries[step_id] > 0`: increment `misses` by 1.
   - If `step_id` was not executed in this feature (not in step_history): skip — do not update counters.
5. Rewrite the metadata comment inline with updated counters.
6. Log: `[learn] Rule effectiveness: updated N rules across M step contracts`

### 5b-decay. Rule Decay Evaluation (every 5th invocation)

This sub-step runs only when the current cycle count is a multiple of 5. It scans all
step contracts for ineffective learned rules and removes flagged rules.

**Trigger check**:
1. Count archived state.yaml files: run `config/scripts/metrics-query.sh cycle-count`; if it exits non-zero or returns empty, fall back to `ls spec/changes/archive/*/state.yaml 2>/dev/null | wc -l`. Use the result as cycle count K.
2. If `K % 5 != 0`: skip this sub-step entirely. Log: `[learn] Rule decay: skipped (cycle K, next at cycle M)`.
3. If `K % 5 == 0`: proceed.

**Scan**:
1. List all `$ORCHESTRATOR_HOME/config/steps/*.yaml` files.
2. For each file, grep for lines matching `<!-- learned:` to collect all learned rules.
3. For each learned rule found, parse the metadata:
   - `date` from `learned: YYYY-MM-DD`
   - `source` from `source: FEATURE-ID`
   - `cycle` from `cycle: N`
   - `hits` from `hits: N` (default 0 if missing)
   - `misses` from `misses: N` (default 0 if missing)

**Flag for removal** (ineffective rule) when ANY of:
- `hits == 0 AND (K - cycle) > 5` — rule has never demonstrably helped after 5+ features
- `(hits + misses) > 0 AND misses / (hits + misses) > 0.7 AND (K - cycle) > 10` — rule is mostly ineffective (>70% miss rate) over sufficient sample
- `hits == 0` AND the rule's `source:` feature-id does NOT appear in any `retry_reasons` or evaluator findings from the last 10 archived state.yaml files

**Flag for retention** (effective rule — do NOT remove even if old):
- `hits > 0 AND (hits + misses) > 0 AND misses / (hits + misses) <= 0.7` — rule is demonstrably working

**Flag for resolution** (contradictory rule) when:
- Two learned rules in the same step contract's `rules:` section offer directly opposing advice on the same topic (e.g., "always X" vs "never X").
- The newer rule (higher `cycle:` value) is preferred; the older one is flagged for removal.

**Prune**:
1. Collect all flagged rules (removal + resolution candidates) with their file paths and line context.
2. If no rules are flagged: log `[learn] Rule decay: scanned N rules, nothing flagged` and stop.
3. For each flagged rule:
   - Read CONVENTIONS.md § Rule Lifecycle Convention before editing
   - Remove or resolve the flagged rule from the step contract
   - ONLY remove rules with `<!-- learned:` metadata — never touch permanent rules
4. Log: `[learn] Rule decay: scanned N rules, flagged M for removal, K for resolution`

### 5c. Adaptive Quality Bar (every invocation)

This sub-step runs after §5b on every learn invocation. It reads recent performance metrics and adjusts `quality_bar.scoring.green_base` in `spec/project.yaml` when trends warrant it.

**Read metrics**:
1. Run `config/scripts/metrics-query.sh quality-trend --limit 5`; if it exits non-zero or returns empty, fall back to reading the last 5 archived state.yaml files via `ls -t spec/changes/archive/*/state.yaml | head -5`.
2. For each state.yaml, extract review score and retry rate from the `metrics:` block:
   - **Review score**: `metrics.review_score_avg` → fallback to omit entry
   - **Retry rate**: `metrics.retries.total / metrics.resolution.tasks_total` if both exist → fallback to `0`
3. Compute aggregates from entries that yielded a valid score:
   - `avg_review_score` = mean of all extracted review scores
   - `avg_retry_rate` = mean of all extracted retry rates

If fewer than 2 valid entries exist: skip this sub-step entirely and log `[learn] Quality bar: insufficient data (N entries), skipping`.

**Read current bar**:
4. Read `spec/project.yaml` — extract `quality_bar.scoring.green_base` (current value).

**Apply adjustment rules**:
5. Evaluate conditions in order:
   - **Tighten**: if `avg_review_score >= 9.5` AND `avg_retry_rate < 0.10`:
     - `new_base = min(current_base + 0.25, 9.5)`
   - **Loosen**: if `avg_review_score < 8.0` OR `avg_retry_rate > 0.40`:
     - `new_base = max(current_base - 0.25, 7.0)`
   - **Stable**: otherwise → `new_base = current_base`

**Apply changes** (only if `new_base != current_base`):
6. Update `spec/project.yaml` — replace the `green_base:` line with:
   ```
   green_base: <new_base>  # auto-adjusted YYYY-MM-DD from X.X (avg: Y.Y, retry: Z%)
   ```
   Where YYYY-MM-DD is today's date, X.X is the old value, Y.Y is avg_review_score (1 decimal), Z% is avg_retry_rate as a percentage (0 decimals).
   Remove any previous `# auto-adjusted` comment that was on the `green_base:` line before replacing.
7. If the adjustment was a **loosen** (new_base < current_base):
   - Create a Linear ticket:
     - Title: `Quality bar lowered: investigate review score / retry rate trend`
     - Description: `avg_review_score=Y.Y, avg_retry_rate=Z%, green_base adjusted from X.X to new_base. Last N features analyzed.`
     - Team: "Home Labs", Labels: ["shell", "Improvement", "S"], Priority: 4
8. Log the result:
   - If adjusted: `[learn] Quality bar adjusted: green_base X.X → Y.Y (avg score: Z.Z, retry rate: W%)`
   - If stable: `[learn] Quality bar stable at X.X (avg score: Y.Y, retry rate: Z%)`
