# Plan: learned rules, minimal evals, split prompts, evidence-backed briefings

Status: proposed · 2026-07-15 · revised after subagent review (premises verified against repo)

> **Partially superseded** (2026-07-18): the learning pipeline changed — no
> `<!-- learned: -->` metadata, no learnings.md; the learn cycle appends eval
> scenarios directly to the step pack's `scenarios/train.jsonl`, and retention
> is judged from per-scenario eval scores (prompt-optimizer `report`).

## Problems

1. **Rule routing is unspecified.** `run-learn-cycle/prompt.md:24-25` says "rule
   routing, hit/miss update, decay evaluation" but never defines them. Rules with
   `<!-- learned: -->` metadata exist in 4 step prompts (design-and-draft-artifacts
   ×11, run-phase-review ×3, implement-tasks ×2, run-learn-cycle ×1) — routed by an
   agent improvising, not by contract. Only 9 steps are prompt-based (the rest are
   scripts), so the unrouted population is 5 steps — worth fixing, but the payoff
   is the contract, not coverage.
2. **Gates can pass without meeting criteria.** `run-phase-review` references
   schema `verify.*` blocks deleted in the June/July simplification — steps 2–4,
   plus a stray `verify.metrics.review_score.min` at line 91. `design-review` has
   no deterministic checks and a hardcoded pass bar (7, prompt.md:39) that ignores
   `quality_bar.min_phase_review_score` (9, project.yaml:332).
3. **Prompts are bloated and always fully loaded.** `parser.py:149-156` inlines the
   whole prompt.md into every spawn. design-and-draft-artifacts is 337 lines,
   run-phase-review 211 — much of it reference material irrelevant to most runs,
   and run-phase-review duplicates contracts owned by producer prompts.
4. **Self-review has no evidence contract.** Steps claim completion; nothing forces
   "these commands ran with these results, this decision was made for these
   reasons". The one-line `briefing` exists but carries no verification backing.

## Scope: the engine is generic — learning is two-tier

These steps execute against **any target repo**, not just orchestrator. A lesson
learned while running in repo X is one of two kinds, and they evolve different
files:

| Tier                 | What it's about                                   | Where it lives                                                                                       | Example                                                    |
| -------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **Workflow-generic** | The step procedure itself — true in any repo      | `config/steps/<step>/prompt.md` in the engine config checkout                                        | "run-phase-review must check pending tasks before scoring" |
| **Repo-specific**    | This codebase's constraints, gotchas, conventions | target repo's `spec/project.yaml` `learnings:`, each entry tagged `step: <step_id>` (or `step: any`) | "in repo X, migrations need `make db-reset` before tests"  |

Mechanics:

- **Repo-specific rules load automatically**: `build_prompt` in run_loop.py (the
  same chokepoint as `_COMPLETION_CONTRACT`) gains one instruction — "Read the
  target repo's `spec/project.yaml` `learnings:` and honor entries whose `step:`
  matches this step or is `any`." One engine line covers every step in every
  repo; no prompt edits, and a repo's rules travel with the repo.
- **Workflow-generic rules** route to the engine config checkout — only possible
  when it IS a checkout (A0 guard). On wheel installs, generic candidates are
  appended to the target repo's learnings tagged `scope: upstream-candidate` so
  they're preserved for later harvesting into the engine repo instead of lost.
- `parser._contract_search_dirs` already lets a target repo override a whole step
  dir (`$ORCHESTRATOR_WORKFLOW_DIR/config/steps/` wins over canonical) — that
  stays the escape hatch for repos needing a fully custom step, but it is NOT the
  rule-routing target (all-or-nothing dir override is too heavy for one rule).

## Review-established constraints (shape everything below)

- **No env vars for agents.** `run_agent_step` passes only `os.environ` to the
  spawned tool; `$WORKTREE_ARTIFACT_DIR`/`$CHANGE_ID` in prompts are text
  conventions, not env vars. Agents DO inherit `REPO_ROOT` and
  `ORCHESTRATOR_CONFIG`. → every script/reference path in a prompt must be
  `$ORCHESTRATOR_CONFIG`-anchored with explicit arguments, no env-var reliance
  inside scripts.
- **The config root may be an installed wheel**, not a git checkout
  (post-packaging). Anything that _writes_ to `config/steps/` must guard for this.
- **The learn commit chokepoint no longer exists.** The old
  `commit-worktree-learn-updates.sh` was deleted (its bats test dangles). Nothing
  commits learn edits today — it must be rebuilt, not "kept".
- **DRIVE.md is a second protocol surface.** Cloud runs pipe raw JSON to
  `orchestrator done` and never see `_COMPLETION_CONTRACT` — protocol changes
  must touch both.
- **`verify_commands` already exists** top-level in spec/project.yaml
  (`verify_commands.test: pytest orchestrator_next/tests/ -q`). Reuse it; do not
  mint `quality_bar.verify_commands`.
- **workflow-report already surfaces briefings** per attempt (stderr table +
  JSON). No report change needed.

## Part A — rule routing contract in run-learn-cycle

Changes: `config/steps/run-learn-cycle/prompt.md` + one small commit script.
**Gate (from review F1/F2): A0 and A3 must land before any routing does.**

**A0. Write-target guard.** Before routing, verify `$ORCHESTRATOR_CONFIG` resolves
inside a writable git checkout (`git -C <config root> rev-parse` succeeds). If not
(installed wheel), route repo-wide learnings to `spec/project.yaml` only and skip
prompt edits with a logged reason. Also dirty-check `config/steps/` first — if
another run's uncommitted edits are present, skip routing this cycle (avoids
interleaving two runs' edits in one commit).

**A1. Routing procedure** (replaces the vague "rule routing, hit/miss update,
decay evaluation" wording). For each finding from the completed change:

1. Identify the originating step (from `step_history` / review artifacts).
2. **Classify the tier first**: would this lesson hold in a different repo?
   - **Repo-specific** (about this codebase) → target repo's
     `spec/project.yaml` `learnings:`, tagged `step: <step_id>` (or `any`).
     Loaded automatically via the build_prompt chokepoint. This is the
     default when in doubt — a wrongly-globalized rule pollutes every repo;
     a wrongly-localized one is merely under-shared.
   - **Workflow-generic** (about the step procedure) → the engine config
     checkout: that step's `prompt.md`, under `### Rules (constraints on
how)` (create if missing). Rules live in prompt.md core — never in
     split-out reference files.
3. Wheel install (no config checkout): generic candidates go to the target
   repo's learnings as `scope: upstream-candidate` (see Scope section).
4. Format contract: one imperative bullet ending with
   `<!-- learned: <date>, source: <change_id>, repo: <origin repo> -->`
   (origin repo recorded so a generic rule's birthplace is auditable). No
   hit/miss counters — staleness is judged, not counted.

**A2. Rule cleanup — inside run-learn-cycle, but NOT every run.**
Sweep only when the completed schema is `implement`, `feature`, or `bugfix`
(design-only runs skip it — otherwise the sweep fires on every schema of every
ticket, maximizing the self-modification race window). The sweep:

- Sweep **both tiers**: generic rules in the engine config checkout's prompts,
  and this target repo's `learnings:` entries. Re-validate each rule's premise
  against the repo it describes (generic → engine repo mechanics; repo-specific →
  the target repo): the file/step/mechanism it references still exists, the
  failure it guards is still possible. Premise gone → **delete** (unattended —
  appending/removing a bullet is low-risk). Other repos' learnings are never
  touched — each repo's rules are swept by its own runs.
- Dedupe before adding — a finding already covered sharpens the existing bullet
  instead of adding one.
- No hard cap; the learned-rules section is a staging area, not an archive. Each
  sweep decides per rule: promote, keep staging, or delete.
- **Promote — guarded (review F3).** A rule that keeps recurring or is fundamental
  to the step gets folded into the prompt's core Instructions. Because this
  rewrites procedure text unattended, cap it at **one promotion per cycle**,
  committed separately with prefix `chore(learn): promote <step>/<rule-slug>` so
  a bad promotion is one `git revert` away and reviewable in history. Bulk
  promotions go to a backlog ticket instead.
- Migration: strip `cycle/hits/misses` from the 17 existing rule comments to the
  new format.

**A3. Rebuild the commit chokepoint.** A deterministic script (the dangling
`tests/test_commit_worktree_learn_updates.bats` is the spec — revive or replace
it): after routing + sweep, commit each side that was touched, atomically per
cycle (promotions excepted, see A2):

- engine config checkout: `git add config/steps && git commit` (generic rules)
- target repo: `git add spec/project.yaml && git commit` (repo-specific rules)

run-learn-cycle's prompt instructs the agent to run it as its final act; no
edits may be left uncommitted on either side.

## Part B — minimal deterministic evals at the main gates

Principle: **eval = smallest script that hard-fails + smallest rubric that
scores.** Deterministic checks live in a script (exit code is evidence the LLM
cannot grade away); the prompt keeps only the rubric and pass/fail protocol.
Prose restating what the script checks gets deleted, not moved.

**B1. `design-review` gets `eval.sh`** (new, ~25 lines):

- Invocation (no env vars — review P9):
  `bash $ORCHESTRATOR_CONFIG/steps/design-review/eval.sh <design.md> <tasks.yaml>`
  — prompt spells out full explicit paths.
- Checks design.md required Design Format Contract sections.
- `[traces: UC-N]` check is **conditional on discovery.md existing** next to
  design.md (bugfix schema has diagnose, not explore — unconditional grep would
  fail every bugfix; review F7).
- Runs the existing `design-and-draft-artifacts/validate-tasks-yaml.sh` (takes an
  explicit path arg, cwd-agnostic — confirmed reusable).
- prompt.md: run eval.sh as step 0; non-zero exit → automatic `needs_work`
  regardless of scores, script output becomes the findings.

**B2. `run-phase-review` slims down**:

- Delete ALL dead schema-verify text: instruction steps 2–4, the
  `verify.metrics.review_score.min` reference in step 6 (line 91 — the live rule
  at line 130 already uses `quality_bar.min_phase_review_score`), and the
  line-140 pointer into the § Format Contract Reference once C2 deletes it.
- Replace with: run the **existing** top-level `verify_commands` map from the
  **target repo's** spec/project.yaml — per-repo by construction; each repo
  declares its own test/build commands (fix orchestrator's own value while
  here — its pytest suite is `orchestrator_next/tests/`). Any non-zero exit is
  a critical correctness finding — cannot pass this round.
- Fallback: no `verify_commands` configured → warning line in phase-review.md for
  **one migration release**, then hard-fail with a config error (a permanent warn
  quietly recreates the original problem — review F6).
- Keep pending-task and quarantine hard-fails (already good).
- Delete the § Format Contract Reference tail (~60 lines) — see C2.

**B3. Align thresholds**: design-review pass bar reads
`quality_bar.min_design_review_score` (add to project.yaml, default 7) instead of
a hardcoded number.

## Part C — split prompts: minimal core, reference loaded on demand

`parser.py` inlines prompt.md whole, so "loaded only when needed" = keep prompt.md
lean and let the agent `Read` sibling files only when a condition triggers. Zero
engine change.

**C1. Layout convention** (document in CLAUDE.md):

```
config/steps/<step>/
  contract.yaml
  prompt.md          # core: Intent, Inputs, Outputs, Instructions, Rules, Verify — target ≤80 lines
  reference/*.md     # format contracts, templates, edge-case procedures — Read on demand
  eval.sh            # optional deterministic gate check (Part B)
```

- Load triggers are explicit and **`$ORCHESTRATOR_CONFIG`-anchored** (agent cwd is
  the target-repo worktree — a bare relative `reference/...` path resolves against
  the wrong tree and the agent silently improvises; review F4):
  "Producing design.md? First Read
  `$ORCHESTRATOR_CONFIG/steps/design-and-draft-artifacts/reference/design-format.md`."
- **Graceful degradation**: the required-section _list_ stays in prompt.md core;
  only per-section detail moves to reference files. A skipped Read then degrades
  to "right sections, thinner content" instead of "invented format".
- Unconditional content stays in prompt.md — a file that would always be loaded is
  not split.

**C2. Contract ownership**: each format contract lives in exactly one `reference/`
file owned by its producer step. Consumers (run-phase-review, design-review) Read
the producer's file; run-phase-review's duplicated § Format Contract Reference is
deleted.

**C3. Link integrity test** (kills the rename hazard from cross-step Reads —
review F4): a small pytest that globs every `$ORCHESTRATOR_CONFIG/steps/...` path
mentioned in any prompt.md and asserts the file exists. Lands in the same commit
as the first split.

**C4. Split targets, in order of payoff** (line counts verified):

| Step                       | Now | Moves to reference/                                                                       |
| -------------------------- | --- | ----------------------------------------------------------------------------------------- |
| design-and-draft-artifacts | 337 | design-format.md, tasks-format.md, templates usage                                        |
| run-phase-review           | 211 | quarantine.md, baseline-comparison.md, ac-verification.md (+ delete contract duplication) |
| diagnose                   | 179 | diagnosis-format.md                                                                       |
| implement-tasks            | 153 | edge-case procedures                                                                      |
| explore                    | 151 | discovery-format.md                                                                       |

Steps already ≤~90 lines are left alone.

## Part D — evidence-backed self-review + briefing

The in-process protocol lives in `_COMPLETION_CONTRACT`
(`orchestrator_next/run_loop.py:37-60`) and `record.py` already merges an
`evidence` block into step_history — so the core is a ~10-line edit to one
string. **Plus one doc edit the "single place" framing hides: DRIVE.md's done
payload example must gain the same fields, or cloud runs produce evidence-less
step_history (review F8/P5).**

**D1. Extend `_COMPLETION_CONTRACT`** — evidence required on
**`completed`/`recovered` only** (engine-synthesized failure payloads carry only
a reason and can't comply; review F5):

```
COMPLETION:
  status: completed
  outputs: {...}
  evidence:
    verified:            # each ## Verify item from the step prompt, with proof
      - check: "pytest orchestrator_next/tests/ -q"
        result: "264 passed"
    decisions:           # non-obvious choices made, with reasons
      - "chose X over Y because Z"
  briefing: >
    What was done, how, and which evidence above supports it. 2–4 sentences.
```

- `evidence.verified` maps 1:1 to the step prompt's `## Verify` items.
- `decisions` may be empty for mechanical steps; never fabricated.
- `briefing` upgrades from one line to done/how/verified. workflow-report already
  surfaces briefings per attempt — no report change.
- `parse_completion.py` tolerates extra keys (verified) — D1 is safe to land
  first, existing tests pass.

**D2. Spot audit (anti-fabrication — review F5).** Self-reported evidence is
enforced by nobody; agents under retry pressure will emit plausible fake results.
run-phase-review gains one instruction: pick one `evidence.verified` entry from a
completed implement step in step_history and re-run its `check` — a mismatch with
the recorded `result` is a critical correctness finding. Fabrication goes from
safe to detectable for the cost of one command per review.

**D3. Step prompts change only their `## Verify` sections** — each item phrased as
a runnable check or inspectable artifact, done opportunistically during Part C
splits.

## Out of scope (deliberately)

- No offline eval framework / benchmark datasets — these are runtime gates.
- No engine changes beyond the `_COMPLETION_CONTRACT` string. on_failure /
  max_retries already enforce retry; workflow-report already shows briefings.
- No `### Rules` back-fill across prompts — sections created on first routed rule.
- No schema validation of the evidence block — record.py stays permissive; D2's
  spot audit is the enforcement.

## Execution order

| #   | Change                                                                                                                                                                                                                                     | Files                                                                   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| 1   | D1: evidence + briefing in completion contract + repo-learnings loader line in build_prompt + DRIVE.md payload example                                                                                                                     | `run_loop.py`, `DRIVE.md`                                               |
| 2   | B2+C2: slim run-phase-review, wire existing verify_commands (fix its value), delete contract duplication, D2 spot audit                                                                                                                    | `run-phase-review/prompt.md`, `spec/project.yaml`                       |
| 3   | B1+B3: design-review eval.sh + threshold                                                                                                                                                                                                   | `design-review/eval.sh`, `design-review/prompt.md`, `spec/project.yaml` |
| 4   | C1+C3+C4+D3: split the 5 big prompts, link-integrity pytest, tighten Verify sections                                                                                                                                                       | `config/steps/*/prompt.md` → `reference/`, CLAUDE.md, tests             |
| 5   | A0+A3: write-target guard + rebuilt commit chokepoint (MUST precede routing)                                                                                                                                                               | commit script, revive/replace dangling bats test                        |
| 6   | A1+A2: routing contract + guarded cleanup/promotion                                                                                                                                                                                        | `run-learn-cycle/prompt.md`                                             |
| 7   | Canary: one ticket through `--schema design`, one through `implement` — eval evidence in review artifacts, briefings in step_history, on-demand reference reads observed, a synthetic finding routes a rule, learn commit lands atomically | —                                                                       |
| 8   | Genericity canary: run one ticket in a **different target repo** (e.g. backlog) — repo-specific rule lands in THAT repo's project.yaml and is loaded on the next run there; orchestrator's prompts untouched by the repo-specific finding  | —                                                                       |

Verification per step: `pytest orchestrator_next/tests/ -q` after 1–4, plus the
step-7 canary — prompt behavior only proves out live.
