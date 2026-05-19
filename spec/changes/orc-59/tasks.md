# Tasks: Rename `linear_ticket_id` → `ticket_id` in the state contract

- [x] T-1 Update test fixtures to `ticket_id` (regression guard)
  - **Why**: AC-2, AC-5 — fixtures mirror the renamed producer's JSON shape;
    sequenced first per repo TDD convention. Note: no meaningful RED step —
    record.py validates `workflow_plan` shape, not this key, so the suite passes
    with either name. This task's verify is "suite stays green."
  - **Files**:
    - `config/scripts/orchestrator_next/tests/test_record_validation.py`
      (three `"linear_ticket_id": None,` dict keys in the `outputs` blocks,
      ~L68, L93, L120 — edit by content match)
  - **Verify**: From `config/scripts/orchestrator_next/`, run
    `python -m pytest tests/test_record_validation.py -q` → exits 0, all tests
    pass; `grep -n linear_ticket_id tests/test_record_validation.py` → no output.

- [ ] T-2 Rename across producer, consumer, schema doc, and skill docs (depends: T-1)
  - **Why**: AC-1, AC-4, AC-5 — atomically rename the single producer and single
    code consumer plus the schema/skill docs so producer and consumer key names
    match and the Linear name is fully de-leaked from the neutral contract.
  - **Files** (edit `linear_ticket_id` → `ticket_id` by content match; line
    numbers approximate due to uncommitted worktree edits):
    - `config/scripts/inline/workflow-init.sh` — JSON key (~L107) **and** the
      two doc-comment lines (~L8 `FLAGS_LINEAR ... linear_ticket_id always
      null`, ~L10 outputs-doc list)
    - `config/scripts/inline/mark-change-completed.sh` — ~L29; result must read
      `cid = d.get("change_id") or d.get("ticket_id") or "unknown"` (fallback
      chain otherwise byte-for-byte unchanged)
    - `config/steps/CONVENTIONS.md` — State Field Registry row (~L354); rename
      ONLY the field-name cell. Do **not** change the "Written By" column
      (`create-linear-ticket`) — out of scope, separate follow-up.
    - `skills/developer/SKILL.md` — field reference in the ticket-resolution
      instruction (~L44–50)
    - `skills/reviewer/SKILL.md` — field reference in the ticket-resolution
      instruction (~L44–50)
    - `skills/linear/SKILL.md` — three sites: frontmatter `description:` (~L3),
      the write instruction `Update ... field linear_ticket_id: HL-XXX` (~L73),
      and the state-fields table row (~L111)
  - **Do NOT touch** (FROZEN — append-only telemetry; direct state.yaml edits
    forbidden per CLAUDE.md):
    - `spec/changes/orc-58/state.yaml`, `spec/changes/orc-30/state.yaml`,
      `spec/changes/orc-59/state.yaml`
    - anything under `spec/changes/archive/`
    - `record.py` — zero occurrences; no change needed (do not add any)
  - **Verify**:
    - `grep -rn "linear_ticket_id" config/ skills/ --include='*.py'
      --include='*.sh' --include='*.md' --include='*.yaml'` → **zero** lines
      (returns exactly 13 before this task)
    - `git diff --name-only -- spec/changes/` → empty (no FROZEN state.yaml,
      archive, or feature-doc file modified)
    - `git diff -- config/scripts/orchestrator_next/record.py` → empty

- [ ] T-3 Review checkpoint (phase gate) (depends: T-2)
  - **Verify**: From `config/scripts/orchestrator_next/`,
    `python -m pytest -q` exits 0 (full suite green);
    `grep -rn "linear_ticket_id" config/ skills/ --include='*.py'
    --include='*.sh' --include='*.md' --include='*.yaml'` returns zero lines;
    `git diff --stat` shows only the 8 RENAME files changed (no state.yaml, no
    archive, no record.py).

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-xxx) = dependency -->
<!-- TDD note: pure mechanical rename — T-1 has no meaningful RED step
     (record.py does not validate this key); it is a regression guard sequenced
     first per convention. See design.md § Trade-offs. -->
</content>
