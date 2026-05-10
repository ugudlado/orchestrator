# Design: Add started_at to seed-state.sh canonical state.yaml

## Context

`seed-state.sh` constructs the canonical-minimum `state.yaml` via an inline
Python block (lines 124–244). The dict at lines 223–238 sets `created_at`
but never sets `started_at`. Downstream, `_resolve_feature_metrics`
(record.py:816) raises `RuntimeError` when `state.get("started_at")` is
falsy for `feature` or `bugfix` schemas. The two producers and consumers
disagree about the canonical field set. See `diagnose.md` for the full
trace.

## Goals / Non-Goals

### Goals

- Producer (`seed-state.sh`) writes `started_at` so the consumer
  (`_resolve_feature_metrics`) succeeds without a workaround.
- Test (`test_seed_state.py`) pins the contract so this regresses loudly
  if the dict is edited again.

### Non-Goals

- Migrating existing seeded `state.yaml` files on disk.
- Changing the consumer side (`_resolve_feature_metrics`) — its contract
  is correct.
- Decoupling `created_at` and `started_at` semantically (no use case yet).
- Touching `workflow-init` (already writes `started_at` independently).

## Approaches Considered

### Approach 1 — Add `started_at` to the seeder's `state` dict (XS, selected)

Add one key to the existing inline Python dict, bound to the same expression
already computing the `created_at` timestamp. Add one assertion to the
existing test. Total diff: ~3 lines.

- Pros: Minimal, single-file producer fix; matches the precedent of
  `workflow-init`; restores producer/consumer agreement; trivially testable.
- Cons: None of consequence.

### Approach 2 — Fall back to `created_at` in `_resolve_feature_metrics` (S)

Make the consumer accept `created_at` when `started_at` is missing.

- Pros: Tolerates older state files on disk.
- Cons: Pushes a workaround into the consumer; entrenches the gap; other
  consumers will need the same fallback; semantically wrong (creation vs.
  start are different events even if they coincide today).

### Approach 3 — Refactor seeder into a typed `State` dataclass / pydantic model (M–L)

Introduce a typed schema for the seed-time state dict.

- Pros: Future-proof against new field omissions.
- Cons: Disproportionate to a one-key omission; no current need; "while
  I'm here" refactor explicitly excluded by repo guidelines.

### Selected Approach

**Approach 1.** Lowest complexity (XS), highest reuse (matches existing
pattern in the same file), and the only approach that fixes the producer
where the bug actually lives. Approach 2 is a band-aid; Approach 3 violates
the minimal-fix principle.

## High-Level Design

### Architecture Overview

```
seed-state.sh (inline Python)
   └── builds `state` dict
        └── now writes started_at == created_at
              └── yaml.safe_dump → state.yaml
                    └── (later) orchestrator done
                          └── _resolve_feature_metrics: state["started_at"] OK
```

### Key Abstractions

None introduced. The change is a single dict key.

## Low-Level Design

### Components

- **`seed-state.sh:237`** — the inline Python `state = {...}` dict.
  - Compute the timestamp once into a local variable (e.g.,
    `now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`).
  - Set both `"created_at": now_iso` and `"started_at": now_iso`.

- **`test_seed_state.py`** — extend the existing dispatch-ready test (or add
  a sibling assertion) to:
  - Load the seeded `state.yaml`.
  - Assert `"started_at" in state` and `"created_at" in state`.
  - Assert `state["started_at"] == state["created_at"]`.

### Data Flow

Single-step: timestamp computed once → written into both keys → serialized.

### State Management

The seeded `state.yaml` is the only artifact. No in-memory state lives past
the script's exit.

### Error Handling

No new error paths. The existing `seed-state.sh` failure modes (missing
`project.yaml`, write permission, YAML serialization) are unchanged.

## Constraints

- Must remain a single inline Python block — no new imports beyond what's
  already in scope.
- Must not change the on-disk timestamp format (`%Y-%m-%dT%H:%M:%SZ`).

## Trade-offs

- `started_at == created_at` at seed time means we lose the ability to
  measure "time spent queued before start." Acceptable: the seeder is the
  point of start; nothing queues before it. If that ever changes, the
  values diverge naturally at that future producer.

## Decisions

- Use a single shared local variable for the timestamp → guarantees the
  two fields are bit-identical → eliminates a class of "off by one second"
  flake in the new test.
- Do not migrate existing on-disk state files → out of scope; transient
  workflow state.

## Open Questions

None.
