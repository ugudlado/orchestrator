# Learnings — subprocess-per-step-observability

Extracted: 2026-04-18
Source feature: subprocess-per-step-observability

---

## composite-pk-audit-trail

**ID**: composite-pk-audit-trail
**Learned**: 2026-04-18
**Source**: subprocess-per-step-observability
**Rule**: When an audit table may legitimately have multiple terminal rows per logical attempt (e.g. escalation then completion), include `status` in the composite primary key.
**Context**: The `step_events` table PK `(repo_root, change_id, phase, step_id, attempt, status)` was chosen to preserve the two-row case where a step emits `escalate_to_architect` then `completed` at the same attempt number. A pure `(entity, attempt)` key would silently overwrite the escalation row. This pattern applies to any audit log where more than one terminal state per attempt is a valid business event.
**Applies to**: Designing audit/event tables in DuckDB or any SQL store; any step tracking schema with retry or escalation semantics.

---

## inline-steps-are-tokenless

**ID**: inline-steps-are-tokenless
**Learned**: 2026-04-18
**Source**: subprocess-per-step-observability
**Rule**: Do not attempt per-step token capture for inline-executed steps in a Claude Code session — the parent-context token counter is not exposed to the running conversation.
**Context**: Five recent archived state.yaml files were audited at feature start; all 25 usage blocks showed 0 token counts for inline steps. Claude Code does not surface parent-context token counters to an in-flight session. Any design that depends on per-step token attribution must move step execution outside the parent context (subprocess-per-step).
**Applies to**: Designing observability for Claude Code workflows; scoping metrics capture features; deciding whether to invest in inline instrumentation vs subprocess dispatch.

---

## cross-artifact-drift-requires-atomic-edits

**ID**: cross-artifact-drift-requires-atomic-edits
**Learned**: 2026-04-18
**Source**: subprocess-per-step-observability
**Rule**: When editing a name or identifier that appears in multiple prose artifacts (spec.md, design.md, tasks.md), grep all artifacts for every variant before committing any single edit.
**Context**: The specify-phase R1 reviewer found 6 important findings — all stemming from one root cause: a Python-edit pass renamed `claude-discoverer.sh` to `claude_discoverer.py` in spec.md but did not propagate to design.md and tasks.md. The reviewer returned a 7/10 retry. An atomic fix commit cleared all 6 findings in one pass, and R2 scored 9/10. Prose artifacts by design duplicate structural facts; that duplication is a latent drift source every time an edit touches one artifact without grep-checking the others.
**Applies to**: Any specify phase where a late design decision renames a module, file, or identifier; review fix passes; inline user edits applied to a single artifact.

---

## bash-fragility-prefer-python-for-new-code

**ID**: bash-fragility-prefer-python-for-new-code
**Learned**: 2026-04-18
**Source**: subprocess-per-step-observability
**Rule**: For new YAML/JSON manipulation, state-machine logic, or arithmetic in this repo, write Python; reserve bash only for shell-native wrappers (PATH wiring, env var forwarding, process exec).
**Context**: The cost_usd=0 bug and YAML timestamp corruption in prior archives were both traceable to bash quoting and associative-array limitations (e.g., `declare -A` failing on bash 3.2). The feature replaced all new bash with Python and confirmed the stack shrinks cleanly: the Python CLI passes 35 tests where the equivalent bash would have been fragile to quote and edge-case issues. The `estimate-cost.sh` bash 3.2 failure surfaced in this very feature's `preview-route` step, reinforcing the pattern.
**Applies to**: Choosing implementation language for new orchestrator scripts; reviewing PRs that add bash for data processing; deciding when to migrate existing bash.

---

## reviewer-retry-cost-is-atomic-vs-design

**ID**: reviewer-retry-cost-is-atomic-vs-design
**Learned**: 2026-04-18
**Source**: subprocess-per-step-observability
**Rule**: Scope review-gate fixes so each is resolvable in a single atomic commit; if a finding requires design revisit, surface it as a blocker before the review step rather than absorbing it mid-retry.
**Context**: Both review gates in this feature (specify R1: 6 findings → 7/10; implement R1: 3 findings → retry) were cleared in exactly one follow-up commit each, producing R2 scores of 9/10. The fix commits were cheap because all findings within each retry were structural-drift-fixable (wrong names, forgotten checkboxes, stale counts) rather than design rethinks. The pattern holds: review retries are cheap when findings are mechanically fixable, expensive when they expose architectural drift that was never surfaced.
**Applies to**: Scoping validate-artifacts steps; deciding when to escalate a finding vs patch it; estimating review-gate budget in new features.

---

## orchestrator-should-dispatch-not-execute

**ID**: orchestrator-should-dispatch-not-execute
**Learned**: 2026-04-18
**Source**: subprocess-per-step-observability
**Rule**: The orchestrator skill must be a pure dispatcher — it reads state and returns an action; cognitive or compute work runs in a named worker process, never inline in the orchestrator's own context.
**Context**: The current orchestrator mixes dispatch (what step is next?) with execution (doing the step inline). This coupling makes per-step metrics impossible and ties the orchestrator to whichever LLM tool is hosting it, violating the agent-agnostic rule. The feature's `orchestrator next` CLI formalises the separation: it is pure-read, returns a JSON action, and the caller (a shell, a cron, a CI job) is responsible for spawning the worker and writing back results. Any step that is "a bit of both" is a refactor candidate.
**Applies to**: Designing new step contracts; evaluating whether a step should be inline or subprocess; reviewing any change to the orchestrator skill that adds execution logic.
