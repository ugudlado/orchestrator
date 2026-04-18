---
feature-id: hl-287
linear-ticket: HL-287
---

# HL-287 — Tasks (verification-only)

This feature is documentation-only; the implement phase performs structural
verification on `spec.md` (which absorbs the audit + a single `§ Rework —
Execution Plan`). No TDD framing — the deliverable is a document, not code.

- [x] T-1: Verify `spec.md` contains § Rework — Execution Plan with between
  6 and 10 milestones, each declaring all required fields (Goal, Exit
  Criteria, Dependencies, Agent(s), Concrete Changes, Risk / Rollback, Size).
  Also verifies audit section presence, the three misclassified step_ids,
  and the Role Definition Template note.

  Files (read-only check):
  - `.spec/hl-287/spec.md`

  Verify:
  ```bash
  set -e
  SPEC=/Users/spidey/code/feature_worktrees/hl-287/.spec/hl-287/spec.md
  test -f "$SPEC"

  # Canonical audit sections still present
  grep -q "^## Audit — Canonical Categorization$"      "$SPEC"
  grep -q "^### Misclassified-Math Steps$"              "$SPEC"
  grep -q "^### Ambiguous Cases — Resolved$"            "$SPEC"
  grep -q "^### Bootstrap Follow-Up Stub$"              "$SPEC"

  # Three misclassified step_ids named
  grep -q "compute-swe-metrics"         "$SPEC"
  grep -q "compute-prediction-accuracy" "$SPEC"
  grep -q "archive-completed-change"    "$SPEC"

  # Single rework plan section exists
  grep -q "^## Rework — Execution Plan$" "$SPEC"
  grep -q "^### Sequencing Rationale$"   "$SPEC"
  grep -q "^### Milestones$"             "$SPEC"

  # Milestone count in [6,10]
  MCOUNT=$(grep -cE "^#### M[0-9]+ —" "$SPEC")
  if [ "$MCOUNT" -lt 6 ] || [ "$MCOUNT" -gt 10 ]; then
    echo "T-1 FAIL: milestone count $MCOUNT not in [6,10]"; exit 1
  fi

  # Every milestone declares each required field. Extract per-milestone slices
  # and grep within. Required fields: Goal, Exit Criteria, Dependencies,
  # Agent(s), Concrete Changes, Risk / Rollback, Size.
  python3 - "$SPEC" <<'PY'
  import re, sys
  spec = open(sys.argv[1]).read()
  # Split on milestone headers
  parts = re.split(r'(?m)^#### (M\d+) — ', spec)
  # parts[0] = preamble; then alternating id, body, id, body...
  if len(parts) < 3:
      print("T-1 FAIL: could not parse milestones"); sys.exit(1)
  required = ["**Goal**", "**Exit Criteria**", "**Dependencies**",
              "**Agent(s)**", "**Concrete Changes**", "**Risk / Rollback**",
              "**Size**"]
  it = iter(parts[1:])
  for mid in it:
      body = next(it)
      # Stop at next top-level or h2/h3 boundary marker (keep until next ####)
      missing = [f for f in required if f not in body]
      if missing:
          print(f"T-1 FAIL: {mid} missing fields: {missing}"); sys.exit(1)
  print("T-1 milestone-field check OK")
  PY

  # AC-10: Role Definition Template with 5 sections + typed-I/O-on-contracts note
  grep -q "^##### Role Definition Template$" "$SPEC"
  for sec in "**Purpose**" "**Philosophy**" "**Responsibilities**" \
             "**Constraints**" "**Evidence standards**"; do
    grep -qF "$sec" "$SPEC"
  done
  grep -q "typed inputs or outputs" "$SPEC"
  grep -q "step contract" "$SPEC"

  echo "T-1 PASS"
  ```

  Acceptance: exit code 0 and final line `T-1 PASS`. Traces: AC-1, AC-2,
  AC-3, AC-4, AC-5, AC-9, AC-10.

- [x] T-2: Verify the milestone dependency graph is acyclic — every declared
  dependency is either `none` or refers to an earlier milestone ID (numeric
  suffix strictly lower than the declaring milestone).

  Files (read-only check):
  - `.spec/hl-287/spec.md`

  Verify:
  ```bash
  set -e
  SPEC=/Users/spidey/code/feature_worktrees/hl-287/.spec/hl-287/spec.md
  python3 - "$SPEC" <<'PY'
  import re, sys
  spec = open(sys.argv[1]).read()
  parts = re.split(r'(?m)^#### (M\d+) — ', spec)
  it = iter(parts[1:])
  errors = []
  for mid in it:
      body = next(it)
      m = re.search(r'\*\*Dependencies\*\*:\s*([^\n]+)', body)
      if not m:
          errors.append(f"{mid}: no Dependencies line"); continue
      deps_raw = m.group(1).strip()
      if deps_raw.lower().startswith("none"):
          continue
      own_n = int(mid[1:])
      for dep in re.findall(r'M(\d+)', deps_raw):
          if int(dep) >= own_n:
              errors.append(f"{mid}: dep M{dep} is not strictly earlier")
  if errors:
      print("T-2 FAIL:"); [print("  " + e) for e in errors]; sys.exit(1)
  print("T-2 PASS")
  PY
  ```

  Acceptance: exit code 0 and final line `T-2 PASS`. Traces: AC-6, NFR-3.

- [x] T-3: Cross-reference check — every `fold-into` row in the audit table
  maps to at least one milestone's Concrete Changes (by target step_id); every
  action in the Consolidation Map maps to at least one milestone's Concrete
  Changes (by file name).

  Files (read-only check):
  - `.spec/hl-287/spec.md`

  Verify:
  ```bash
  set -e
  SPEC=/Users/spidey/code/feature_worktrees/hl-287/.spec/hl-287/spec.md
  python3 - "$SPEC" <<'PY'
  import re, sys
  spec = open(sys.argv[1]).read()

  # Extract milestone bodies concatenated (for substring lookup)
  parts = re.split(r'(?m)^#### (M\d+) — ', spec)
  plan_body = "\n".join(parts[2::2])

  # Fold-into rows: filenames of deleted contracts that must be mentioned
  fold_into_files = [
      "design-exploration.yaml",
      "create-or-refresh-artifacts.yaml",
      "validate-artifacts.yaml",
      "run-implement-review.yaml",
      "final-signoff.yaml",
      "phase-signoff.yaml",
      "verify-spike-findings.yaml",
  ]
  missing = [f for f in fold_into_files if f not in plan_body]
  if missing:
      print(f"T-3 FAIL: fold-into files not referenced in plan: {missing}")
      sys.exit(1)

  # Consolidation Map actions: each source file must appear in plan body
  consolidation_files = [
      "ideator.md", "workflow-improver.md",
      "debugger.md", "humanizer.md",
      "haiku-agent.md", "sonnet-agent.md",
      "designer.md", "learner.md",
      "reviewer.md",
  ]
  missing = [f for f in consolidation_files if f not in plan_body]
  if missing:
      print(f"T-3 FAIL: consolidation files not referenced in plan: {missing}")
      sys.exit(1)

  print("T-3 PASS")
  PY
  ```

  Acceptance: exit code 0 and final line `T-3 PASS`. Traces: AC-7, AC-8.
