"""
Reconcile in_progress entries between state.yaml and DuckDB.

Public API:
  reconcile_in_progress(state, db, context) — mutate state.step_history in place
                                               so it matches DuckDB in_progress truth.

Design:
  - DB wins. YAML-only in_progress entries are stripped (FR-4).
  - DB-only in_progress rows are materialised into state.step_history (FR-5).
  - Non-in_progress entries (completed, failed, etc.) are never touched.
  - No disk writes — caller is responsible for persisting state.yaml if needed.
  - Parameterised SQL only (no string interpolation).
"""
from __future__ import annotations

import re

from orchestrator_next.parser import State, StepHistoryEntry

# Slug guard — match upsert.py pattern.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_SELECT_IN_PROGRESS = """
SELECT phase, step_id, attempt, agent_name, started_at
FROM step_events
WHERE repo_root = ?
  AND change_id = ?
  AND status = 'in_progress'
"""


def reconcile_in_progress(state: State, db, context: dict) -> None:
    """Mutate state.step_history in place so it matches DuckDB in_progress truth.

    Rules:
      - For each in_progress row in DuckDB for (repo_root, change_id):
          if state.step_history lacks a matching (phase, step_id, attempt, status='in_progress')
          entry, append a synthesised StepHistoryEntry with started_at from the DB row.
      - For each in_progress entry in state.step_history for the current change_id:
          if DuckDB has no matching row, remove that entry from state.step_history.

    DB wins. No writes to state.yaml in this helper — only in-memory mutation. The caller
    (bin/orchestrator) does not persist the mutated state.yaml to disk here; the write-back
    happens naturally when `next` appends its own in_progress entry and any subsequent code
    writes state.yaml. If `next` exits without a write path (e.g. complete_workflow), the
    mutated state is discarded — which is correct, because no step was dispatched.

    Args:
        state:   in-memory State object (step_history mutated in place)
        db:      open duckdb.DuckDBPyConnection (schema already ensured)
        context: dict with keys 'repo_root' and 'change_id'

    Raises:
        ValueError: if change_id violates the slug guard
    """
    change_id: str = context["change_id"]
    repo_root: str = context.get("repo_root", "")

    # Slug guard — reject invalid change_id before any query.
    if not _SLUG_RE.match(change_id):
        raise ValueError(
            f"change_id '{change_id}' violates slug guard. "
            f"Must match ^[a-z0-9][a-z0-9-]*$ (lowercase alphanumeric and hyphens only, "
            f"no leading hyphen)."
        )

    # Fetch all in_progress rows from DuckDB for this (repo_root, change_id).
    db_rows = db.execute(
        _SELECT_IN_PROGRESS,
        [repo_root, change_id],
    ).fetchall()

    # Build key set from DB rows: (phase, step_id, attempt)
    db_keys = {(r[0], r[1], r[2]) for r in db_rows}

    # FR-4: strip YAML-only in_progress entries whose key is absent in DB.
    kept = []
    for e in state.step_history:
        if e.status == "in_progress" and (e.phase, e.step_id, e.attempt) not in db_keys:
            continue  # orphan — drop
        kept.append(e)
    state.step_history = kept

    # FR-5: materialise DB rows that are missing from YAML.
    yaml_keys = {
        (e.phase, e.step_id, e.attempt)
        for e in state.step_history
    }
    for phase, step_id, attempt, agent_name, started_at in db_rows:
        if (phase, step_id, attempt) in yaml_keys:
            continue  # already present — leave alone
        state.step_history.append(StepHistoryEntry(
            step_id=step_id,
            phase=phase,
            status="in_progress",
            agent=agent_name,
            attempt=attempt,
            started_at=str(started_at) if started_at is not None else None,
            ended_at=None,
            usage={},
            escalation=None,
            raw={
                "step_id": step_id,
                "phase": phase,
                "status": "in_progress",
                "agent": agent_name,
                "attempt": attempt,
                "started_at": str(started_at) if started_at is not None else None,
            },
        ))
