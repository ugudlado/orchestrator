# Phase Review — Specify — done-verb-level-aware-writes

**Date**: 2026-04-25
**Reviewer**: reviewer agent (claude-sonnet-4-6)
**Schema**: feature / non-light
**Review score min**: 9 (per feature.yaml specify phase, non-light)
**Round**: 2 (retry; no first-pass bonus available)
**Round 1 score**: 5 (FAIL — C-1, I-1, I-2 blocked)

---

## Artifacts Reviewed

- `.state/done-verb-level-aware-writes/spec.md`
- `.state/done-verb-level-aware-writes/design.md`
- `.state/done-verb-level-aware-writes/tasks.md`
- `.state/done-verb-level-aware-writes/discovery.md`

---

## Round-1 Finding Resolution

### C-1: Resolved

spec.md adds FR-6a and AC-6a with the full subagent synthetic-row specification (agent name from `agent-<id>.meta.json`, `step_id = subagent-<id>`, `phase = meta`, fail-soft per row, idempotency check, JSONL parse outside BEGIN). The "What Changes" section and Architecture table both describe `_resolve_subagent_rows()` + `_write_subagent_events()` absorbing `_ingest_subagents_main`. design.md's Key Abstractions, Low-Level Design, Trade-offs, and Decisions all add matching entries. tasks.md adds T-8a (RED) and T-8b (GREEN) with concrete verify clauses, and extends T-11/T-12 to cover the subagent path atomicity. The resolution is atomic across all four artifacts — no artifact claims "absorbs" while another claims "deleted".

Verification:
- `grep "_resolve_subagent_rows\|_write_subagent_events" spec.md` — matches in FR-6a, AC-6a, What Changes, Architecture.
- `grep "_resolve_subagent_rows\|_write_subagent_events" design.md` — matches in Key Abstractions, Low-Level Design code block, data flow, Trade-offs, Decisions.
- `grep -P "^- \[[ x]\] T-8a:" tasks.md` — match confirmed (T-8a RED, T-8b GREEN).

**C-1: RESOLVED.**

### I-1: Resolved

T-22 verify clause now states: "After this task only, `bash scripts/m8-gates.sh` exits non-zero with a 'banner still mentions `record`' message". T-23 checkpoint explicitly states: "`m8-gates.sh` is intentionally red until T-25 — this checkpoint does NOT assert gate exit 0." T-25 verify states: "`bash scripts/m8-gates.sh` now exits 0 (the T-22 strict gate goes green once banner no longer mentions `record`)."

The sequencing contradiction is resolved: T-22 is TDD-red intent for the gate, T-23 acknowledges the red state, T-25 makes it green. No verify clause asserts `m8-gates.sh exits 0` before T-25.

**I-1: RESOLVED.**

### I-2: Resolved

Format verification:
- `grep -P "^- \[[ x]\] T-\d+:" tasks.md | wc -l` → **29** (T-tasks only, FT entries separately match `^- \[[ x]\] FT-\d+:` → **3**). Total: 32 entries.
- `grep "(depends:" tasks.md | wc -l` → **0**. No inline `(depends:)` embedded in titles.
- `grep "\*\*Verify\*\*\|\*\*Why\*\*" tasks.md` → One match: FT-3's resolution prose body (describes the reformatting that was done). This is not a task header — it is prose inside a completed fix task describing the contract. No task uses `**Verify**:` or `**Why**:` as a structural field header.
- `Verify:` and `depends:` lines are indented 2 spaces as required.

**I-2: RESOLVED.**

---

## Verification Gates

### Assertions

| Assertion | Status |
|-----------|--------|
| spec.md exists in state dir | PASS |
| design.md exists in state dir | PASS |
| tasks.md exists with at least one task (29 + 3 FT tasks present) | PASS |
| spec.md has Acceptance Criteria section with testable criteria (AC-1 through AC-10 + AC-6a, all Given/When/Then) | PASS |
| FR-6a in spec.md matches design.md `_resolve_subagent_rows` + `_write_subagent_events` description | PASS |
| T-22 verify says gate exits non-zero; T-23 says gate is intentionally red; T-25 says gate exits 0 | PASS |

### Caller-Site Spot-Check (Cycle-16 Rule)

| Claim | Evidence | Status |
|-------|----------|--------|
| `bin/orchestrator:334` — verb dispatch tuple | Line 332: `args[0] not in ("next", "record", "doctor", "ingest-driver", "ingest-subagents")` — design describes Stage A addition of `done`; line matches pre-Stage-A state (as expected — implementation hasn't happened yet) | PASS |
| `bin/orchestrator:84,170` — `_compute_cost_usd` imports | Line 84 and 169-170 confirmed in `_ingest_driver_main` and `_ingest_subagents_main` respectively | PASS |
| `agents/developer.md:213` — `orchestrator record` mandate | Line 213 confirmed | PASS |
| `scripts/m8-gates.sh:45` — banner assertion | Not re-verified (unchanged since round 1 PASS) | PASS |

---

## Dimension Scores

| Dimension | Score | Round 1 Score | Key Findings |
|-----------|-------|---------------|--------------|
| spec_compliance | **9** | 5 (critical) | C-1 resolved; M-1, M-2 persist (minor) |
| correctness | **9** | 7 (important) | I-1 resolved; M-3 persists (minor) |
| security | **9** | 9 | No findings |
| simplicity | **9** | 9 | No findings |
| code_quality | **9** | 7 (important) | I-2 resolved; M-4 partially resolved |
| **Overall** | **9** | **5** | min(all dimensions) |

**Bonus**: Not applicable (retry round — bonus cap removed per contract).

---

## Overall Verdict: PASS (9 >= 9)

**Round delta: 5 → 9 (+4)**

---

## Findings

### Critical

None.

### Important

None.

### Minor (carried from round 1, none resolved in this round)

**M-1 [spec_compliance]: discovery.md deviates from Discovery Brief Format Contract**

Status: **Unchanged from round 1.** Three deviations persist:
1. Section header is `## Personas` — contract requires `## Personas & Actors`.
2. Use Cases section has no `### Happy Path` or `### Error & Edge Cases` subsections (contract requires both).
3. Error case identifiers use `UC-EN-N` (e.g., `UC-EN-1`) — contract requires `UC-E<N>` format.

Traceability is internally consistent: both discovery.md and spec.md use `UC-EN-1/2/3`, so AC traces work. Format-only; not blocking.

**M-2 [spec_compliance]: FR-9 mislabels SKILL.md reference count**

Status: **Unchanged from round 1.** FR-9 prose says "3 dispatch references on lines 88, 135, 139, 174" — that is 4 lines, not 3. `grep -n "orchestrator record" skills/orchestrate/SKILL.md` returns 6 lines (88, 135, 139, 155, 166, 174). T-19's verify clause uses a zero-match grep that will catch all 6 at implementation time, making the undercount self-correcting. Not blocking.

**M-3 [correctness]: discovery.md UC-1 mentions `feature_metrics` write — contradicts Phase 4/5 boundary**

Status: **Unchanged from round 1.** UC-1 says `done` "atomically writes a `feature_metrics` row update and a `driver_sessions` row." spec.md and design.md explicitly carve `feature_metrics` to Phase 5. The normative spec is correct; the divergence is in the non-normative discovery artifact. Documented-divergence exception applies. Not blocking.

**M-4 [spec_compliance]: design.md line-range for `_ingest_driver_main` partially corrected**

Status: **Partially resolved.** Line 142 updated from "53-137" to "53-138" (correct). Line 330 still reads "Lifted from `bin/orchestrator:_ingest_driver_main` (lines 53-137):" — off by one. Internal inconsistency within design.md (two different line ranges for the same function). Cosmetic; does not affect implementation correctness.

---

## Baseline Comparison

Historical average `review_score_avg` across archived feature-schema entries: **8.97** (6 entries: 9.0, 8.3, 9.0, 9.0, 9.0, 9.5). Current round-2 score: **9.0**. Delta: +0.03 above historical average. No regression warning.

---

## State Update

This review PASSES at score 9/10. No fix tasks required.
