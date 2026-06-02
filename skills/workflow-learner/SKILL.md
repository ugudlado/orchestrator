---
name: workflow-learner
description: "Evaluate workflow compliance and route learnings to step contracts and project.yaml. Invoked by the run-learn-cycle step in the complete/autopilot workflows."
user-invocable: true
---

## Variables

# REPO_ROOT is derived from the CURRENT working directory, not the inherited
# env var. When dispatched into a feature worktree (cwd = worktree), this
# resolves to the worktree; standalone (cwd = main checkout) it resolves to
# main. The orchestrator exports REPO_ROOT=<main> during complete-phase, so we
# must NOT honour that fallback when running inside a worktree — learn edits
# belong on the branch they were learned on.
REPO_ROOT=$(git rev-parse --show-toplevel)
REPO_NAME=$(basename "$REPO_ROOT")
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
WORKFLOW_STATE_DIR="${WORKFLOW_STATE_DIR:-$REPO_ROOT/spec/changes}"
WORKTREE_ARTIFACT_DIR="${WORKTREE_ARTIFACT_DIR:-$REPO_ROOT/spec/changes}"

## Learn from Last Feature

$ARGUMENTS

## Overview

This skill runs the evaluation + self-improvement loop after a feature is completed. It assesses workflow compliance, routes learned rules to step contracts in `$ORCHESTRATOR_HOME/config/steps/` and project-specific learnings to `spec/project.yaml` `learnings:` section. Rules go into step contracts (deterministic, enforced at execution time). Project-specific learnings go into project.yaml (agent-agnostic, persists across sessions).

## Process

### 0. Resolve scope

Parse `--scope` from `$ARGUMENTS` (default `all`). It biases — does not replace — the pipeline below; every scope still runs Find Context → Route Findings → Backlog Sync → Report.

| `--scope` | Emphasis in §3 evaluation & §4 routing | Replaces legacy skill |
|---|---|---|
| `all` | Full workflow-compliance pass (all axes) | — |
| `errors` | Error patterns from logs/metrics → step-contract rule fixes | `/diagnose` |
| `workflow` | Hook / step-contract / schema infrastructure defects | `/workflow-improve` |
| `session` | Session mistakes → `spec/project.yaml` `learnings:` (skip step-contract routing) | `/reflect` |

A narrow scope still produces the §5 report; it just weights which findings are surfaced and where they route in §4.

### 1. Find Context

Locate the feature under evaluation (this step runs **before** `archive-completed-change`
merge/archive/worktree teardown):

1. If the dispatch prompt includes `state_yaml_path=...`, read that file first.
2. Else if `worktree_path=...` is in the prompt, read
   `<worktree_path>/spec/changes/<change_id>/state.yaml`.
3. Else read `$REPO_ROOT/spec/changes/<change_id>/state.yaml` when present.
4. Only for cross-feature fleet analysis (§2b, §4 decay, quality trend), scan
   `spec/changes/archive/*/state.yaml` — not for the just-finished change's primary
   inputs while it is still active.

If `$ARGUMENTS` contains a feature ID, resolve that change_id in the paths above.
Do not require `status: completed` on the active file — this step runs while
`status` may still be `active`; `mark-change-completed` stamps completed later.

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

1. **Collect archive data**: Prefer `learn_metrics.retry_hotspots_csv` when the run-learn-cycle step supplied it (stdout JSON). Else run `orchestrator_next/scripts/metrics/metrics-query.sh retry-hotspots --fleet --limit 10`; if that exits non-zero or returns empty, fall back to listing `spec/changes/archive/*/state.yaml` sorted by modification time (most recent first, limit 10). For each record, extract:
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
   - Apply the rule inline to the target step contract (same path as other workflow issues)

### 3. Run Workflow Evaluation

Evaluate the feature with:
- The step_history audit trail (not a reconstructed report — the raw data)
- Per-step learnings aggregated by type (mistakes, insights, retries, decisions, skips)
- Aggregate metrics for pattern detection
- **Systemic retry patterns** from step 2b (if any) — include the full `systemic_retry_patterns` list with step IDs, reasons, counts, and suggested target contracts
- The step contracts directory path: `$ORCHESTRATOR_HOME/config/steps/`
- The step contracts directory for rule placement (when writing a learned rule, pick the section based on rule type):

  | Rule type | Target section in `prompt.md` |
  |-----------|-------------------------------|
  | Quality constraint | `### Rules (constraints on how)` |
  | Verification check | `## Verify` |
  | Process guidance | `## Instructions` (only if it's a step in the existing flow — prefer Rules) |

  **Never** add a rule as a paragraph in `## Instructions`. Instructions describe the flow; rules constrain it.
- Instruction to run all 5 parts: compliance → step analysis → pattern detection → step contract updates → metrics write

**Step analysis**: The evaluator examines:
- **Skipped steps**: Were they justified? Do skip_reasons indicate a workflow design issue (step should be conditional)?
- **Retried steps**: Are retry_reasons systemic? Should a step contract rule prevent the root cause?
- **Mistakes**: Which ones are repeats of known issues? Which are new?
- **Insights**: Which should become rules in the appropriate step contract?
- **Duration outliers**: Steps taking >2x average may need decomposition
- **Drift events**: `skip_reason: "model drift"` entries indicate the workflow lost the model — tighten instructions
- **SRP violations**: Flag step contracts where the intent has multiple unrelated verbs, or where instruction contains rule-like paragraphs that belong in `### Rules`.

### 4. Route Findings

Classify each finding and route it to the right handler.

**Routing targets:**
- **Agent prompts (base)** → `skills/<name>/SKILL.md` — fleet-shared identity; read for validation only during learn routing; never written by the learn loop
- **Agent prompt learnings (repo overlay)** → `$REPO_ROOT/.orchestrator/agents/<name>.md` — repo-scoped, stamped deltas appended at dispatch
- **Workflow rules** → step contracts in `$ORCHESTRATOR_HOME/config/steps/` (deterministic, enforced at execution time, shared across repos) — or `.orchestrator/` override for repo-specific shape changes
- **Project-specific learnings** → `spec/project.yaml` `learnings:` section (agent-agnostic, persists across sessions, repo-scoped)
- **Never write to CLAUDE.md** — it's a pointer file only.

#### 4a. Classifier (three axes)

Every finding belongs to exactly one of three buckets. The axes are
**who owns the miss**, not *what the fix looks like*.

| Bucket               | Owner of the miss                                   | Target                                                  |
|----------------------|-----------------------------------------------------|---------------------------------------------------------|
| `agent_improvement`  | Agent ignored or skipped an existing contract rule  | `$REPO_ROOT/.orchestrator/agents/<name>.md` — repo-scoped overlay (base `skills/<name>/SKILL.md` validated, never written) |
| `workflow_improvement` | Step/phase/gate is missing or wrong                | Step contract (global by default) or `.orchestrator/` override |
| `project_learning`   | Tech-stack / command / domain / path fact needed    | `spec/project.yaml` `learnings[]` or `rules[]`          |

**Classification order** — check in this order, first match wins. The
order matters: most misrouting happens when an agent miss gets rewritten
as a new workflow rule, which silently duplicates existing contract text.

1. **Does an existing step contract already cover this concern?**
   Read the relevant step contract (`run-implement-review.yaml`,
   `run-phase-review.yaml`, `execute-one-task.yaml`, etc.) before
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
- `agent_improvement` → append a stamped block to `$REPO_ROOT/.orchestrator/agents/<name>.md`.
  (1) Validate that `skills/<name>/SKILL.md` exists; if not, skip the write and log it.
  (2) Scaffold the overlay with a one-line generated-overlay header if the file is absent.
  (3) Append the learning with `<!-- learned: YYYY-MM-DD, source: FEATURE-ID, cycle: N, repo: NAME -->`
  metadata on the block. Leave `skills/<name>/SKILL.md` unchanged — the shared base stays pristine.
- `workflow_improvement` (global) → edit `$ORCHESTRATOR_HOME/config/steps/<step>.yaml`
  per the routing table below. Learned rule gets `<!-- learned: ... -->` metadata.
- `workflow_improvement` (repo override) → edit `$REPO_ROOT/.orchestrator/<path>`;
  copy global file first if the override doesn't exist. Override fully replaces
  the global file — no YAML merge. Repo-override path: `$REPO_ROOT/.orchestrator/<relative_path>`;
  fallback: `$ORCHESTRATOR_HOME/config/<relative_path>`.
- `project_learning` → append to `spec/project.yaml` `learnings[]` with
  `id`, `learned`, and `rule` fields. Never modify global step contracts.

**Routing decision tree:**

**Agent misses** (contract already enforces the concern, agent skipped it):
- Identify which agent owned the skipped step (reviewer, developer,
  architect, etc.) — check `state.yaml` step_history for the agent
  assigned to the failing step.
- Validate `skills/<name>/SKILL.md` exists; if not, skip the overlay write and log it.
- Append a stamped learning block to `$REPO_ROOT/.orchestrator/agents/<name>.md`
  (scaffold the overlay with a header on first write) to make the existing
  requirement harder to skip: add an explicit checklist item, name the artifact
  to inspect, or move the check earlier in the prompt.
- Leave `skills/<name>/SKILL.md` unchanged and do NOT add a rule to the step
  contract — the contract already has one.

**Workflow issues** (schema gaps, step contract bugs, hook problems):
- Resolve the target file path per scope:
  - Global → `$ORCHESTRATOR_HOME/config/steps/<step>.yaml`
  - Repo override → `$REPO_ROOT/.orchestrator/steps/<step>.yaml` (copy global first if missing)
- Only edit `### Rules`, `## Verify`, or `## Instructions` sections in `prompt.md` — never touch contract.yaml (routing only) or permanent rules (no `<!-- learned:` stamp).
- For repo overrides, the override file fully replaces its global counterpart — copy the global file first if the override doesn't exist, then edit.
- Apply the edit directly and stamp the rule with `<!-- learned: ... -->` metadata per § Rule metadata below.
- Fix is applied immediately to disk — improves the next workflow execution.

**Code/functionality issues** (bugs discovered, missing features, tech debt, test gaps):
- Create a Linear ticket with description, evidence, and suggested approach
- Do NOT fix inline — let the ideator prioritize it and the develop workflow execute it properly
- This ensures code changes go through full spec-first discipline

**Learned rules** (patterns to remember, gotchas discovered, quality checks):
- Apply the §4a classifier first. The bucket tells you WHERE to write;
  the routing table below (for `workflow_improvement` only) tells you
  WHICH step contract the rule belongs in.
- `agent_improvement` → append to `$REPO_ROOT/.orchestrator/agents/<name>.md` — skip this table entirely
- `workflow_improvement` (global) → `$ORCHESTRATOR_HOME/config/steps/<step>.yaml`
- `workflow_improvement` (repo override) → `$REPO_ROOT/.orchestrator/steps/<step>.yaml`
- `project_learning` → append to `spec/project.yaml` `learnings[]` — skip
  this table entirely (no step contract involved)

  | When to enforce | Target step contract | Where in the file |
  |---|---|---|
  | During diagnosis/investigation | `diagnose.yaml` | `instruction:` section |
  | During implementation | `execute-one-task.yaml` | `rules:` list |
  | During review | `run-phase-review.yaml` | `rules:` or `instruction:` |
  | During implement review | `run-implement-review.yaml` | `instruction:` section |
  | At phase boundaries | `phase-signoff.yaml` | `instruction:` pre-conditions |
  | During artifact/task creation | `create-or-refresh-artifacts.yaml` | `rules:` list |

- Apply the rule to the appropriate section of the target step contract
- **IMPORTANT — Rule metadata**: When writing a learned rule, you MUST append the metadata comment inline on the same line as the rule text:
  `<!-- learned: YYYY-MM-DD, source: FEATURE-ID, cycle: N, repo: REPO_NAME -->`
  Where: `YYYY-MM-DD` = today's date, `FEATURE-ID` = the feature being evaluated, `N` = current cycle count (use `learn_metrics.cycle_count_csv` from prep when present; else `orchestrator_next/scripts/metrics/metrics-query.sh cycle-count`; if that exits non-zero or is empty, fall back to `ls spec/changes/archive/*/state.yaml 2>/dev/null | wc -l`), `REPO_NAME` = `basename $(git rev-parse --show-toplevel)` (the repo that generated this rule).
  **Repo scoping**: Default to `repo: $REPO_NAME` (repo-scoped). Only use `repo: *` (universal) when the rule is about workflow mechanics itself (e.g., "always write next_step before spawn") and NOT about tech-stack, domain, or repo-specific patterns.
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
1. Get recent completed features: prefer `learn_metrics.recent_features_csv` from prep; else `orchestrator_next/scripts/metrics/metrics-query.sh recent-features --limit 10`; if that exits non-zero or returns empty, fall back to listing `spec/changes/archive/*/state.yaml`. Read the just-completed feature's `step_history[]` from the state.yaml.
2. Build a map: `step_retries[step_id] = total retry count for that step`.
   A step with no retries has count 0.
3. List all `$ORCHESTRATOR_HOME/config/steps/*.yaml` files.
4. For each file, grep for lines matching `<!-- learned:`. For each learned rule:
   - Parse the metadata fields: `learned`, `source`, `cycle`, `hits` (default 0), `misses` (default 0).
   - Determine the step_id from the filename (e.g., `execute-one-task.yaml` → `execute-one-task`).
   - If `step_retries[step_id] == 0`: increment `hits` by 1.
   - If `step_retries[step_id] > 0`: increment `misses` by 1.
   - If `step_id` was not executed in this feature (not in step_history): skip — do not update counters.
5. Rewrite the metadata comment inline with updated counters.
6. Log: `[learn] Rule effectiveness: updated N rules across M step contracts`

### 5b-decay. Rule Decay Evaluation (every 5th invocation)

This sub-step runs only when the current cycle count is a multiple of 5. It scans all
step contracts for ineffective learned rules and removes flagged rules.

**Trigger check**:
1. Count archived state.yaml files: prefer `learn_metrics.cycle_count_csv` from prep; else `orchestrator_next/scripts/metrics/metrics-query.sh cycle-count`; if that exits non-zero or returns empty, fall back to `ls spec/changes/archive/*/state.yaml 2>/dev/null | wc -l`. Use the result as cycle count K.
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
   - Remove or resolve the flagged rule from the step contract
   - ONLY remove rules with `<!-- learned:` metadata — never touch permanent rules (no metadata comment)
4. Log: `[learn] Rule decay: scanned N rules, flagged M for removal, K for resolution`

### 5c. Adaptive Quality Bar (every invocation)

This sub-step runs after §5b on every learn invocation. It reads recent performance metrics and adjusts `quality_bar.scoring.green_base` in `spec/project.yaml` when trends warrant it.

**Read metrics**:
1. Prefer `learn_metrics.quality_trend_csv` from prep; else `orchestrator_next/scripts/metrics/metrics-query.sh quality-trend --limit 5`; if that exits non-zero or returns empty, fall back to reading the last 5 archived state.yaml files via `ls -t spec/changes/archive/*/state.yaml | head -5`.
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

## Commit edits (final act)

Commit your edits as the **last action before returning**, on whatever branch is
checked out. Stage only learn's write targets — never whole dirs — so unrelated
WIP is left untouched:

```bash
for p in config/steps config/workflows .orchestrator spec/project.yaml orchestrator_next/tests; do
  [ -e "$p" ] && git add -A "$p" 2>/dev/null || true
done
git diff --cached --quiet || git commit -m "chore(${CHANGE_ID}): learn-cycle rule updates"
```

If there is nothing to commit, skip silently. Never fail the learn step over a commit error.

## Output

Return COMPLETION:
  status: completed (or abandoned if learn fails and is non-blocking)
  outputs:
    learn_result: {completed: true} or {skipped: true, reason: "..."}
  artifacts: []
