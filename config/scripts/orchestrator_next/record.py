"""HL-287 M5: orchestrator record subcommand.

Accepts a completed step's outputs + usage + evidence via stdin JSON,
validates against the contract's `expected_outputs`, writes a terminal
`step_history` entry with uniform `started_at` / `completed_at` / `usage`,
and advances `next_step` per `workflow_plan`.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.parser import ContractError, load_contract_for_step, load_state
from orchestrator_next.upsert import upsert_step_event


# ---------------------------------------------------------------------------
# Boundary detection (FR-4)
# ---------------------------------------------------------------------------

class BoundaryKind(str, Enum):
    NONE = "none"
    PHASE = "phase"
    FEATURE = "feature"


def _detect_boundary(
    workflow_plan: dict,
    phase: str,
    step_id: str,
    status: str,
) -> BoundaryKind:
    """Return BoundaryKind based on workflow_plan and current step.

    Returns NONE for any status != 'completed'.
    Returns NONE if step_id is not the last element in workflow_plan[phase].active.
    Returns PHASE if step_id is last in active AND phase is not the last key in workflow_plan.
    Returns FEATURE if step_id is last in active AND phase IS the last key in workflow_plan.
    """
    if status != "completed":
        return BoundaryKind.NONE

    phase_block = workflow_plan.get(phase) or {}
    active = phase_block.get("active") or []
    if not active or step_id != active[-1]:
        return BoundaryKind.NONE

    # step_id is the last in active — at minimum a phase boundary
    phase_keys = list(workflow_plan.keys())
    if phase_keys and phase == phase_keys[-1]:
        return BoundaryKind.FEATURE
    return BoundaryKind.PHASE


# ---------------------------------------------------------------------------
# Phase boundary write helper (FR-5)
# ---------------------------------------------------------------------------

_INSERT_PHASE_EVENT = """
INSERT OR REPLACE INTO phase_events (
  repo_root, change_id, phase, attempt,
  step_count, cost_usd, input_tokens, output_tokens,
  cache_read_input_tokens, cache_creation_input_tokens,
  duration_ms, started_at, ended_at
)
SELECT
  ? AS repo_root,
  ? AS change_id,
  ? AS phase,
  ? AS attempt,
  COUNT(*)                                      AS step_count,
  COALESCE(SUM(cost_usd), 0.0)                 AS cost_usd,
  COALESCE(SUM(input_tokens), 0)               AS input_tokens,
  COALESCE(SUM(output_tokens), 0)              AS output_tokens,
  COALESCE(SUM(cache_read_input_tokens), 0)    AS cache_read_input_tokens,
  COALESCE(SUM(cache_creation_input_tokens), 0) AS cache_creation_input_tokens,
  COALESCE(SUM(duration_ms), 0)                AS duration_ms,
  MIN(started_at)                               AS started_at,
  MAX(ended_at)                                 AS ended_at
FROM step_events
WHERE repo_root = ? AND change_id = ? AND phase = ?
"""


def _write_phase_event(db, repo_root: str, change_id: str, phase: str, attempt: int) -> None:
    """Insert a phase_events row aggregated from step_events.

    Caller is responsible for transaction control (BEGIN/COMMIT/ROLLBACK).
    All SQL uses parameterised execution (NFR-5).
    """
    db.execute(_INSERT_PHASE_EVENT, [
        repo_root, change_id, phase, attempt,
        repo_root, change_id, phase,
    ])


# ---------------------------------------------------------------------------
# Driver session resolution helpers (FR-6)
# ---------------------------------------------------------------------------

_INSERT_DRIVER_SESSION = """
INSERT OR REPLACE INTO driver_sessions (
  repo_root, change_id, session_id, model,
  total_tokens, input_tokens, output_tokens, cost_usd,
  started_at, ended_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _resolve_driver_session(state: dict, change_id: str, db=None) -> dict:
    """Resolve the driver session and return its usage dict.

    Resolution order:
      1. $ORCHESTRATOR_DRIVER_SESSION_ID env var.
      2. Scan ~/.claude/projects/<repo-slug>/ for most recent *.jsonl by mtime.

    Raises RuntimeError if session_id cannot be resolved.

    Args:
        state: parsed state dict (needs 'repo_root').
        change_id: feature change_id for logging.
        db: optional open DuckDB connection for cost computation.

    Returns:
        dict with keys: session_id, model, total_tokens, input_tokens,
        output_tokens, cache_read_input_tokens, cache_creation_input_tokens,
        cost_usd, started_at, ended_at.
    """
    from orchestrator_next.jsonl_usage import extract_driver_usage

    repo_root = state.get("repo_root") or ""

    # Step 1: env var
    session_id = os.environ.get("ORCHESTRATOR_DRIVER_SESSION_ID") or ""

    # Step 2: scan for most recent JSONL by mtime
    if not session_id:
        from pathlib import Path as _Path

        def _repo_slug(rr: str) -> str:
            return rr.replace("/", "-")

        slug_dir = _Path.home() / ".claude" / "projects" / _repo_slug(repo_root)
        if slug_dir.exists():
            jsonl_files = list(slug_dir.glob("*.jsonl"))
            if jsonl_files:
                newest = max(jsonl_files, key=lambda p: p.stat().st_mtime)
                session_id = newest.stem

    if not session_id:
        raise RuntimeError(
            f"[done] driver session_id not resolvable for change_id={change_id!r}: "
            f"set $ORCHESTRATOR_DRIVER_SESSION_ID or ensure a JSONL exists under "
            f"~/.claude/projects/<repo-slug>/"
        )

    usage = extract_driver_usage(repo_root, session_id)
    if not usage:
        usage = {}

    # Compute cost_usd if a DB is available
    if db is not None and usage:
        model, cost = _compute_cost_usd(db, "driver-loop", usage)
        if cost is not None:
            usage["cost_usd"] = cost
        if model and not usage.get("model"):
            usage["model"] = model

    input_tok = usage.get("input_tokens") or 0
    output_tok = usage.get("output_tokens") or 0
    return {
        "session_id": session_id,
        "model": usage.get("model"),
        "total_tokens": input_tok + output_tok,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
        "cost_usd": usage.get("cost_usd"),
        "started_at": None,  # JSONL aggregate doesn't expose session start/end directly
        "ended_at": None,
    }


def _write_driver_session(db, repo_root: str, change_id: str, session: dict) -> None:
    """Insert a driver_sessions row. Caller controls transaction.

    Args:
        db: open DuckDB connection (transaction open, caller controls).
        repo_root: absolute path to the repo root.
        change_id: feature identifier.
        session: dict returned by _resolve_driver_session.
    """
    db.execute(_INSERT_DRIVER_SESSION, [
        repo_root,
        change_id,
        session["session_id"],
        session.get("model"),
        session.get("total_tokens") or 0,
        session.get("input_tokens") or 0,
        session.get("output_tokens") or 0,
        session.get("cost_usd") or 0.0,  # NOT NULL — default to 0.0 when unresolved
        session.get("started_at"),
        session.get("ended_at"),
    ])


# ---------------------------------------------------------------------------
# Subagent absorption helpers (FR-6a)
# ---------------------------------------------------------------------------

def _resolve_subagent_rows(
    repo_root: str,
    change_id: str,
    session_id: str,
) -> list[dict]:
    """Discover sub-agent JSONLs and parse usage. Pure parsing — no DB operations.

    Returns a list of dicts:
        {"agent_id", "agent_name", "step_id", "phase", "usage"}

    Per-row failures:
      - Missing or malformed meta.json → agent_name='subagent-unknown', row still emitted.
      - JSONL with no usable turns → row skipped (logged to stderr).
      - Other parse errors → row skipped (logged to stderr).

    Runs OUTSIDE the boundary transaction (keeps BEGIN/COMMIT window short).
    """
    from orchestrator_next.jsonl_usage import (
        discover_subagents,
        extract_agent_usage,
        locate_subagent_jsonl_path,
    )

    agent_ids = discover_subagents(repo_root, session_id)
    result = []

    for agent_id in agent_ids:
        step_id = f"subagent-{agent_id}"
        agent_name = "subagent-unknown"

        # Read agentType from meta.json sidecar (fail-soft: fallback to unknown)
        try:
            jsonl_path = locate_subagent_jsonl_path(
                repo_root, agent_id, driver_session_hint=session_id
            )
            if jsonl_path is not None:
                meta_path = jsonl_path.parent / f"agent-{agent_id}.meta.json"
                if meta_path.exists():
                    import json as _json
                    meta = _json.loads(meta_path.read_text())
                    agent_type = meta.get("agentType") or ""
                    if agent_type:
                        agent_name = agent_type
        except Exception as exc:  # noqa: BLE001 — meta parse is best-effort
            sys.stderr.write(
                f"[done] subagent meta read failed for agent_id={agent_id!r}: {exc}\n"
            )
            # agent_name stays "subagent-unknown"

        # Extract usage from JSONL — skip row if no usable turns
        try:
            usage = extract_agent_usage(
                repo_root, agent_id, driver_session_hint=session_id
            )
            if not usage:
                sys.stderr.write(
                    f"[done] subagent {agent_id!r}: no usable JSONL turns, skipping row\n"
                )
                continue
        except Exception as exc:  # noqa: BLE001 — JSONL parse is best-effort
            sys.stderr.write(
                f"[done] subagent JSONL parse failed for agent_id={agent_id!r}: {exc}\n"
            )
            continue

        result.append({
            "agent_id": agent_id,
            "agent_name": agent_name,
            "step_id": step_id,
            "phase": "meta",
            "usage": usage,
        })

    return result


def _write_subagent_events(
    db,
    repo_root: str,
    change_id: str,
    rows: list[dict],
) -> None:
    """Insert one synthetic step_events row per subagent tuple via upsert_synthetic_event.

    Honors the legacy idempotency check: skip if a row already exists for
    (repo_root, change_id, phase='meta', step_id, attempt=1) with non-zero input_tokens.

    Computes cost_usd via _compute_cost_usd per row when the DB has pricing rows.

    Per-row insert errors are logged to stderr but do not raise (fail-soft per row,
    consistent with the discovery pass).

    Caller controls the transaction (BEGIN/COMMIT/ROLLBACK).
    """
    from orchestrator_next.upsert import upsert_synthetic_event as _upsert_syn

    for row in rows:
        agent_id = row["agent_id"]
        agent_name = row["agent_name"]
        step_id = row["step_id"]
        phase = row["phase"]
        usage = dict(row["usage"])  # copy — we may mutate it

        try:
            # Idempotency check: skip if row already exists with non-zero input_tokens
            existing = db.execute(
                "SELECT input_tokens FROM step_events "
                "WHERE repo_root=? AND change_id=? AND phase=? AND step_id=? AND attempt=1",
                [repo_root, change_id, phase, step_id],
            ).fetchone()
            if existing is not None and existing[0] is not None and existing[0] > 0:
                continue

            # Compute cost_usd via pricing DB
            model, cost = _compute_cost_usd(db, agent_name, usage)
            if cost is not None:
                usage["cost_usd"] = cost
            if model and not usage.get("model"):
                usage["model"] = model

            _upsert_syn(
                db,
                {"repo_root": repo_root, "change_id": change_id},
                agent_name=agent_name,
                step_id=step_id,
                phase=phase,
                usage=usage,
            )
        except Exception as exc:  # noqa: BLE001 — per-row fail-soft
            sys.stderr.write(
                f"[done] subagent write failed for agent_id={agent_id!r}: {exc}\n"
            )


# ---------------------------------------------------------------------------
# Cost computation helpers (ISSUE-17)
# ---------------------------------------------------------------------------

def _orchestrator_home() -> Path:
    """Return the orchestrator repo root.

    Prefers ORCHESTRATOR_HOME env var; falls back to the parent of the
    `config/scripts/` tree this module lives in.
    """
    env = os.environ.get("ORCHESTRATOR_HOME")
    if env:
        return Path(env)
    # __file__ is config/scripts/orchestrator_next/record.py
    return Path(__file__).parent.parent.parent.parent


@functools.lru_cache(maxsize=1)
def _load_routes() -> dict:
    """Load scripts/routes.yaml (cached per process)."""
    path = _orchestrator_home() / "scripts" / "routes.yaml"
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


_LOOKUP_SQL = (
    "SELECT input_usd, output_usd, cache_read_usd, cache_creation_usd "
    "FROM pricing "
    "WHERE model_id = ? AND effective_from <= ? "
    "ORDER BY effective_from DESC LIMIT 1"
)

# Per-connection pricing row cache keyed by id(db).
# Populated on first lookup; stores ALL rows so subsequent lookups are pure-Python
# dict accesses (NFR-1: 1000 calls < 50 ms).
# Shape: {db_key: {model_id: [(effective_from, input, output, cache_read, cache_creation), ...]}}
# Rows are stored sorted descending by effective_from so we can do a linear scan.
_pricing_cache: dict[int, dict[str, list]] = {}

_LOAD_ALL_SQL = (
    "SELECT model_id, effective_from, input_usd, output_usd, "
    "cache_read_usd, cache_creation_usd "
    "FROM pricing ORDER BY model_id, effective_from DESC"
)


def _ensure_pricing_cache(db) -> dict[str, list]:
    """Load all pricing rows into the in-process cache for this connection."""
    db_key = id(db)
    if db_key in _pricing_cache:
        return _pricing_cache[db_key]
    rows = db.execute(_LOAD_ALL_SQL).fetchall()
    by_model: dict[str, list] = {}
    for row in rows:
        mid, eff, inp, out, cr, cc = row
        by_model.setdefault(mid, []).append((eff, inp, out, cr, cc))
    _pricing_cache[db_key] = by_model
    return by_model


def _lookup_price(db, model_id: str, effective_at: "_dt.datetime") -> dict | None:
    """Look up pricing rates for model_id at effective_at from the DuckDB pricing table.

    Returns a dict with keys: input, output, cache_read, cache_creation (float, $/MTok).
    Returns None (with a stderr warning) if:
      - db is None (offline/test path, FR-3(a))
      - no row exists for model_id AND no __default__ row exists

    The __default__ fallback is transparent — no warning is emitted on that path.

    Performance: all pricing rows are loaded into an in-process cache on the first
    call per connection so subsequent calls are pure-Python dict lookups (NFR-1).

    Args:
        db: open DuckDB connection, or None for the offline path.
        model_id: model identifier to look up.
        effective_at: datetime; the most recent row with effective_from <= this is used.
    """
    if db is None:
        sys.stderr.write(
            f"[record] pricing: db=None; skipping price lookup for {model_id!r}\n"
        )
        return None

    by_model = _ensure_pricing_cache(db)

    def _pick_row(mid: str):
        """Return the most-recent row for mid with effective_from <= effective_at."""
        candidates = by_model.get(mid, [])
        # Rows are pre-sorted descending by effective_from.
        for eff, inp, out, cr, cc in candidates:
            if eff <= effective_at:
                return (inp, out, cr, cc)
        return None

    row = _pick_row(model_id)
    if row is None:
        row = _pick_row("__default__")
    if row is None:
        sys.stderr.write(
            f"[record] pricing: no price entry for model {model_id!r} and no "
            f"__default__ row; skipping cost computation\n"
        )
        return None

    inp, out, cr, cc = row
    return {
        "input": float(inp),
        "output": float(out),
        "cache_read": float(cr),
        "cache_creation": float(cc) if cc is not None else 0.0,
    }


def _billable_token_units(usage: dict | None) -> int:
    """Sum of input/output/cache token counts for cost eligibility (ISSUE-inline-cost)."""
    if not isinstance(usage, dict):
        return 0
    return int(
        (usage.get("input_tokens") or 0)
        + (usage.get("output_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
    )


def _compute_cost_usd(
    db, agent: str, usage: dict, *, now: "_dt.datetime | None" = None
) -> tuple[str | None, float | None]:
    """Resolve agent → model_id and compute cost from usage token counts via DuckDB.

    Resolution order:
      0. usage['model']  — billing-truth from JSONL, always preferred.
      1. routes.agents[agent] → backend_name
      2a. routes.backends[backend_name] → model_id  (for native_* keys)
      2b. routes.models[backend_name].model → model_id  (for proxy models)
      2c. If still unresolved but billable tokens > 0, use model_id '__default__'
          so DuckDB pricing applies (same table row _lookup_price already falls back to).
    Price lookup: _lookup_price(db, model_id, now) with __default__ fallback.

    Returns (model_id, cost_usd) or (model_id, None) if pricing unavailable,
    or (None, None) if model resolution fails.
    Missing token fields default to 0.
    """
    routes = _load_routes()

    # Step 0: prefer usage.model when present (JSONL-sourced billing truth).
    # This path lets synthetic rows (driver-loop) compute cost without being
    # registered in routes.yaml.
    model_id: str | None = usage.get("model") if isinstance(usage, dict) else None
    bills = _billable_token_units(usage)

    if not model_id:
        # Step 1: agent → backend
        backend = (routes.get("agents") or {}).get(agent)
        if backend:
            # Step 2: backend → model_id
            backends_map = routes.get("backends") or {}
            if backend in backends_map:
                model_id = backends_map[backend]
            else:
                # Try routes.models.<backend>.model (proxy path)
                model_entry = (routes.get("models") or {}).get(backend)
                if isinstance(model_entry, dict):
                    model_id = model_entry.get("model")
            if not model_id and bills == 0:
                sys.stderr.write(
                    f"[record] cost_usd: backend {backend!r} for agent {agent!r} "
                    f"not resolved to a model_id; skipping cost computation\n"
                )

        # Step 2c: token-backed fallback — inline / driver-loop / unknown agents
        # often have JSONL totals but no model string; price via __default__ row.
        if not model_id and bills > 0:
            model_id = "__default__"

        if not model_id:
            if not backend:
                sys.stderr.write(
                    f"[record] cost_usd: agent {agent!r} not in routes.yaml and "
                    f"usage.model not set; skipping cost computation\n"
                )
            return None, None

    # Step 3: look up price from DuckDB (with __default__ fallback inside _lookup_price)
    effective_at = now if now is not None else _dt.datetime.utcnow()
    price = _lookup_price(db, model_id, effective_at)
    if price is None:
        return model_id, None

    # Step 4: compute cost
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_read_tokens = usage.get("cache_read_input_tokens") or 0
    cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0
    cost = (
        input_tokens * price["input"] / 1_000_000
        + output_tokens * price["output"] / 1_000_000
        + cache_read_tokens * price["cache_read"] / 1_000_000
        + cache_creation_tokens * price["cache_creation"] / 1_000_000
    )
    return model_id, cost


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Phase 5: six computation functions lifted verbatim from
# scripts/inline/ingest-feature-metrics.py (FR-1).
# Signatures and logic are byte-equivalent; _SLUG_RE is dropped because
# upsert_feature_metrics already enforces the slug guard (design.md Component 1).
# ---------------------------------------------------------------------------

def parse_tasks(tasks_md: Path) -> dict:
    """Count [x], [ ], and [~] task markers.

    Returns:
        tasks_total, tasks_completed, tasks_failed, resolve_rate
    """
    text = tasks_md.read_text()
    total = len(re.findall(r"^\s*-\s*\[", text, re.MULTILINE))
    completed = len(re.findall(r"^\s*-\s*\[x\]", text, re.MULTILINE | re.IGNORECASE))
    skipped = len(re.findall(r"^\s*-\s*\[~\]", text, re.MULTILINE))
    failed = total - completed - skipped
    resolve_rate = completed / total if total > 0 else 0.0
    return {
        "tasks_total": total,
        "tasks_planned": total,
        "tasks_added": 0,
        "tasks_completed": completed,
        "tasks_failed": max(failed, 0),
        "resolve_rate": round(resolve_rate, 6),
    }


def compute_retries(state: dict) -> dict:
    """Sum retries.* keys and extract human_interventions.

    Returns:
        retries_total, human_interventions
    """
    retries_section = state.get("retries") or {}
    if isinstance(retries_section, dict):
        retries_total = sum(
            v for v in retries_section.values() if isinstance(v, (int, float))
        )
    else:
        retries_total = 0

    human_interventions = state.get("human_interventions") or 0
    return {
        "retries_total": int(retries_total),
        "human_interventions": int(human_interventions),
    }


def compute_resolution(
    tasks_total,
    tasks_completed,
    retries_total: int,
    step_history: list,
    quarantine_events,
) -> dict:
    """Derive pass_at_1, pass_at_2, regressions, regression_rate.

    Approximation note: state.yaml retries are keyed by step_id (e.g.
    "execute-next-task"), not by task_id — so per-task attempt granularity
    is unavailable. We use:
      pass_at_1 = max(0, tasks_total - retries_total) / tasks_total
      pass_at_2 = tasks_completed / tasks_total
    This satisfies the monotonicity invariant pass_at_2 >= pass_at_1 and
    is the tightest approximation possible without per-task retry records.
    quarantine_events would normally reduce the numerator, but since
    quarantined tasks are not counted in tasks_completed either, the formula
    stays consistent.

    Returns all-None when tasks_total is None or zero (spike path).
    """
    if not tasks_total:
        return {
            "pass_at_1": None,
            "pass_at_2": None,
            "regressions": None,
            "regression_rate": None,
        }

    tc = tasks_completed if isinstance(tasks_completed, int) else 0

    pass_at_1 = round(max(0, tasks_total - retries_total) / tasks_total, 6)
    pass_at_2 = round(tc / tasks_total, 6)

    regressions = sum(
        1 for e in step_history
        if isinstance(e, dict) and e.get("regression")
    )
    regression_rate = round(regressions / tasks_total, 6)

    return {
        "pass_at_1": pass_at_1,
        "pass_at_2": pass_at_2,
        "regressions": regressions,
        "regression_rate": regression_rate,
    }


def run_git_churn(worktree: str, change_id: str) -> dict:
    """Count files_changed, insertions, deletions, total_commits, rework_commits.

    Searches git log for commits whose message contains the change_id or
    feature/<change_id> branch name. Falls back to zeros on any git failure.
    """
    import subprocess as _subprocess
    defaults: dict = {
        "files_changed": 0,
        "insertions": 0,
        "deletions": 0,
        "total_commits": 0,
        "rework_commits": 0,
        "rework_rate": 0.0,
    }
    try:
        result = _subprocess.run(
            ["git", "-C", worktree, "log",
             "--grep", change_id,
             "--no-merges",
             "--format=%H %s"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return defaults

        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        total_commits = len(lines)
        # Match legacy compute-swe-metrics.sh: grep -c "^fix:" behavior (NFR-3)
        rework_commits = sum(
            1 for ln in lines if re.match(r"^(fix|rework):", ln)
        )
        rework_rate = rework_commits / total_commits if total_commits > 0 else 0.0

        if not lines:
            return defaults

        # Get first and last commit SHA for diff range
        last_sha = lines[-1].split()[0]
        first_sha = lines[0].split()[0]

        # files_changed via --name-only diff
        name_result = _subprocess.run(
            ["git", "-C", worktree, "diff", "--name-only", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        files_changed = len([
            ln for ln in name_result.stdout.splitlines() if ln.strip()
        ]) if name_result.returncode == 0 else 0

        # insertions/deletions via --numstat
        num_result = _subprocess.run(
            ["git", "-C", worktree, "diff", "--numstat", f"{last_sha}^..{first_sha}"],
            capture_output=True, text=True, timeout=10,
        )
        insertions = 0
        deletions = 0
        if num_result.returncode == 0:
            for row in num_result.stdout.splitlines():
                parts = row.split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    insertions += int(parts[0])
                    deletions += int(parts[1])

        return {
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
            "total_commits": total_commits,
            "rework_commits": rework_commits,
            "rework_rate": round(rework_rate, 6),
        }
    except Exception:
        return defaults


def extract_review_scores(state: dict) -> dict:
    """Extract review_score.overall from step_history entries.

    Returns:
        scores_list (list of ints/floats), avg (float or None)
    """
    step_history = state.get("step_history") or []
    scores: list = []
    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        review_score = entry.get("review_score")
        if isinstance(review_score, dict):
            overall = review_score.get("overall")
            if overall is not None:
                try:
                    scores.append(float(overall))
                except (TypeError, ValueError):
                    pass

    avg = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "scores_list": scores,
        "avg": avg,
    }


def wall_clock_minutes(state: dict):
    """Compute wall clock in minutes from state started_at and completed_at.

    Returns None if either timestamp is missing or unparseable.
    """
    started_at = state.get("started_at")
    completed_at = state.get("completed_at")
    if not started_at or not completed_at:
        return None

    def _parse_ts(ts):
        if isinstance(ts, _dt.datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=_dt.timezone.utc)
            return ts
        s = str(ts).strip()
        # Normalize space-separated UTC offset to ISO 8601
        s = s.replace(" ", "T")
        s = re.sub(r"\+00:00$", "Z", s)
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = _dt.datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
                return parsed.replace(tzinfo=_dt.timezone.utc)
            except ValueError:
                continue
        return None

    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return round(delta / 60.0, 4)


# ---------------------------------------------------------------------------
# Phase 5: _resolve_feature_metrics_tasks_path — tasks.md path resolver
# ---------------------------------------------------------------------------

def _resolve_workflow_artifact_path(state_raw: dict[str, Any], filename: str) -> Path | None:
    """Unified resolver for workflow artifact files (tasks.md, design.md, etc.).

    Resolution order:
      1. If filename == "tasks.md" and state_raw has an explicit ``tasks_path``
         override, return that path immediately.
      2. If ``worktree_path`` is set AND that directory exists on disk, return
         ``<worktree_path>/spec/changes/<change_id>/<filename>``.
      3. Else if ``repo_root`` is set, return
         ``<repo_root>/spec/changes/<change_id>/<filename>``.
      4. Return None when no candidate can be constructed at all.
    """
    # 1. Explicit tasks_path override (tasks.md only).
    if filename == "tasks.md":
        raw_path = state_raw.get("tasks_path")
        if isinstance(raw_path, str) and raw_path:
            return Path(os.path.expanduser(raw_path))

    change_id = state_raw.get("change_id")
    if not (isinstance(change_id, str) and change_id):
        return None

    # 2. Worktree path — only when the directory actually exists.
    worktree_path = state_raw.get("worktree_path")
    if isinstance(worktree_path, str) and worktree_path:
        wt = Path(os.path.expanduser(worktree_path))
        if wt.is_dir():
            return wt / "spec" / "changes" / change_id / filename

    # 3. Fall back to repo_root.
    repo_root = state_raw.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        return Path(os.path.expanduser(repo_root)) / "spec" / "changes" / change_id / filename

    return None


def _resolve_feature_metrics_tasks_path(state: dict) -> Path:
    """Thin wrapper: resolve tasks.md path for feature_metrics computation."""
    return _resolve_workflow_artifact_path(state, "tasks.md") or Path("")


# ---------------------------------------------------------------------------
# Phase 5: _resolve_feature_metrics and _write_feature_metrics (FR-1, FR-2)
# ---------------------------------------------------------------------------

def _resolve_feature_metrics(state: dict, change_id: str) -> dict:
    """Pure compute. Returns kwargs dict for upsert_feature_metrics.

    Raises:
        FileNotFoundError: tasks.md missing for feature/bugfix schemas.
        RuntimeError:      started_at or completed_at missing on feature/bugfix.
    """
    schema = str(state.get("schema") or "feature")
    worktree = str(state.get("worktree_path") or state.get("repo_root") or "")

    if schema in ("feature", "bugfix"):
        if not state.get("started_at") or not state.get("completed_at"):
            raise RuntimeError(
                f"_resolve_feature_metrics: state missing started_at/completed_at "
                f"for schema={schema}"
            )

    tasks_md = _resolve_feature_metrics_tasks_path(state)
    if schema in ("feature", "bugfix") and not tasks_md.is_file():
        raise FileNotFoundError(
            f"_resolve_feature_metrics: tasks.md not found at {tasks_md} "
            f"(required for schema={schema})"
        )

    if tasks_md.is_file():
        task_counts = parse_tasks(tasks_md)
    else:
        task_counts = {
            "tasks_total": None, "tasks_planned": None, "tasks_added": None,
            "tasks_completed": None, "tasks_failed": None, "resolve_rate": None,
        }

    retries = compute_retries(state)
    resolution = compute_resolution(
        tasks_total=task_counts.get("tasks_total"),
        tasks_completed=task_counts.get("tasks_completed"),
        retries_total=retries["retries_total"],
        step_history=state.get("step_history") or [],
        quarantine_events=state.get("quarantine_events"),
    )
    churn = run_git_churn(worktree, change_id)
    reviews = extract_review_scores(state)
    wc = wall_clock_minutes(state)

    return {
        "schema_name": schema,
        **task_counts,
        **retries,
        **resolution,
        **churn,
        "review_scores_json": json.dumps(reviews["scores_list"]),
        "review_score_avg": reviews["avg"],
        "wall_clock_minutes": wc,
        "source": f"done@{_utcnow_iso()}",
    }


def _write_feature_metrics(db, repo_root: str, change_id: str, data: dict) -> None:
    """Calls upsert_feature_metrics. Caller controls transaction. Exceptions propagate."""
    from orchestrator_next.upsert import upsert_feature_metrics as _upsert_fm
    _upsert_fm(db, repo_root=repo_root, change_id=change_id, **data)


def _resolve_tasks_md(state_raw: dict[str, Any]) -> Path | None:
    """Thin wrapper: resolve tasks.md path from state.yaml fields."""
    return _resolve_workflow_artifact_path(state_raw, "tasks.md")


def _check_all_tasks_completed(state_raw: dict[str, Any]) -> bool:
    """Return True iff no unchecked `- [ ]` items remain in tasks.md.

    Returns True (fail-open) only when path is None — i.e., state has no fields
    from which a candidate path can even be constructed.  When a candidate path
    is constructible but the file is missing or unreadable, returns False
    (fail-closed) so the workflow does not silently skip unfinished tasks.
    """
    path = _resolve_tasks_md(state_raw)
    if path is None:
        return True
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return False  # fail-closed: expected file missing → tasks not yet complete
    return re.search(r"^\s*-\s*\[\s*\]", text, re.MULTILINE) is None


REPEAT_PREDICATES = {
    "all_tasks_completed": _check_all_tasks_completed,
}


def _compute_next_step(
    state_raw: dict[str, Any],
    just_completed_step_id: str,
    state_yaml_path: str,
) -> dict[str, Any] | None:
    """Return the next_step dict for state.yaml, or None if phase complete."""
    phase = state_raw.get("phase", "")
    plan = state_raw.get("workflow_plan", {}).get(phase, {})
    active = plan.get("active", []) if isinstance(plan, dict) else []
    history = state_raw.get("step_history") or []
    completed = {
        (e.get("phase"), e.get("step_id"))
        for e in history
        if isinstance(e, dict) and e.get("status") == "completed"
    }
    # ISSUE-16: before marking step as completed, check repeat_until predicate.
    # If predicate returns False, re-emit the same step instead of advancing.
    try:
        contract = load_contract_for_step(just_completed_step_id, state_yaml_path)
    except (FileNotFoundError, ContractError):
        contract = None
    if contract is not None and contract.repeat_until:
        predicate = REPEAT_PREDICATES.get(contract.repeat_until)
        if predicate is None:
            sys.stderr.write(
                f"[record] unknown repeat_until predicate "
                f"{contract.repeat_until!r} on {just_completed_step_id}; "
                f"treating as absent\n"
            )
        elif not predicate(state_raw):
            return {"phase": phase, "step_id": just_completed_step_id}
    # Include the step we just completed (may not be in history yet if caller
    # invoked record before appending).
    completed.add((phase, just_completed_step_id))
    for sid in active:
        # Handle `{id: foo, ...}` entries plus plain strings and `step if flag`.
        if isinstance(sid, dict):
            sid = sid.get("id", "")
        elif isinstance(sid, str):
            sid = sid.split(" if ")[0].strip()
        if not sid:
            continue
        if (phase, sid) not in completed:
            return {"phase": phase, "step_id": sid}
    return None


def record(
    state_yaml_path: str, payload: dict[str, Any], *, db=None
) -> tuple[dict[str, Any], int]:
    """Apply a record operation to state.yaml. Returns (result, exit_code).

    Validation: payload.outputs must cover every name in
    contract.expected_outputs (from the contract `outputs:` field).

    Args:
        state_yaml_path: path to the state.yaml file to update.
        payload: the record payload dict.
        db: open DuckDB connection for pricing lookups, or None for the
            offline/test path (cost computation is skipped with a warning).
    """
    required = {"step_id", "phase", "outputs"}
    missing = required - payload.keys()
    if missing:
        return (
            {"action": "error", "reason": "payload_missing_keys", "missing": sorted(missing)},
            3,
        )

    step_id = payload["step_id"]
    phase = payload["phase"]
    # status is optional; default 'completed' for backward compat (FR-2).
    status = payload.get("status", "completed")

    _VALID_STATUSES = {"completed", "recovered", "abandoned"}
    if status not in _VALID_STATUSES:
        return (
            {
                "action": "error",
                "reason": "invalid_status",
                "status": status,
                "valid_statuses": sorted(_VALID_STATUSES),
            },
            3,
        )

    outputs: dict[str, Any] = payload.get("outputs") or {}

    # Load contract to validate expected_outputs
    try:
        contract = load_contract_for_step(step_id, state_yaml_path)
    except FileNotFoundError:
        contract = None

    if contract is not None and status == "completed":
        missing_out = [k for k in contract.outputs if k not in outputs]
        if missing_out:
            return (
                {
                    "action": "validation_error",
                    "reason": "missing_outputs",
                    "step_id": step_id,
                    "missing_outputs": missing_out,
                },
                3,
            )

    # Check A: workflow_plan.active shape on workflow-init completion.
    # Root cause of ISSUE-1: dispatcher reads .active to build the work queue;
    # an empty or missing list causes it to immediately return complete_workflow.
    if step_id == "workflow-init" and status == "completed":
        plan = outputs.get("workflow_plan") or {}
        bad_phases = [
            p for p, body in plan.items()
            if not isinstance(body, dict)
            or not isinstance(body.get("active"), list)
            or len(body["active"]) == 0
        ]
        if bad_phases:
            return (
                {
                    "action": "validation_error",
                    "reason": "workflow_plan_active_missing_or_empty",
                    "phases": bad_phases,
                    "hint": "workflow_plan[<phase>].active must be a non-empty list of step IDs",
                },
                3,
            )

    # Check B: usage required for agent (non-inline) steps on completion.
    # Root cause of ISSUE-10.1: empty usage means cost report is blank and
    # telemetry has no data for the step.
    # JSONL enrichment path: if agent_id is present, usage will be populated
    # from the sub-agent JSONL below — accept the payload even if tokens=0.
    agent = payload.get("agent", "inline")
    payload_usage = payload.get("usage") or {}
    has_agent_id = bool(payload.get("agent_id") or payload_usage.get("agent_id"))
    if status == "completed" and agent != "inline" and not has_agent_id:
        has_tokens = (
            (isinstance(payload_usage.get("input_tokens"), (int, float)) and payload_usage["input_tokens"] > 0)
            or (isinstance(payload_usage.get("output_tokens"), (int, float)) and payload_usage["output_tokens"] > 0)
        )
        if not has_tokens:
            return (
                {
                    "action": "validation_error",
                    "reason": "agent_step_missing_usage",
                    "step_id": step_id,
                    "agent": agent,
                    "hint": "agent steps must record usage.input_tokens or usage.output_tokens > 0, or pass agent_id for JSONL enrichment",
                },
                3,
            )

    path = Path(state_yaml_path)

    # Check C: detect corrupted state.yaml before AND after write.
    # Root cause of ISSUE-7: hand-edits that corrupt YAML surface far downstream
    # (dispatch crashes on malformed YAML three calls later). Capture pre-write
    # bytes so we can restore the file if either the initial parse or post-write
    # parse fails.
    with open(path, "rb") as f:
        pre_write_bytes = f.read()

    try:
        state_raw = yaml.safe_load(pre_write_bytes.decode("utf-8")) or {}
    except yaml.YAMLError as e:
        return (
            {
                "action": "error",
                "reason": "state_yaml_parse_failure",
                "detail": str(e),
                "hint": (
                    "state.yaml failed to parse before record. "
                    "Likely an earlier hand-edit corrupted the file."
                ),
            },
            4,
        )

    # Determine attempt number for this (phase, step_id).
    # Exclude in_progress entries from the count — they are placeholders, not
    # completed attempts, and must not inflate the attempt number (T-11 / FR-6).
    history = list(state_raw.get("step_history") or [])
    prior_attempts = [
        e.get("attempt") for e in history
        if isinstance(e, dict)
        and e.get("phase") == phase
        and e.get("step_id") == step_id
        and e.get("attempt")
        and e.get("status") != "in_progress"
    ]
    attempt = (max(prior_attempts) + 1) if prior_attempts else 1

    now = _utcnow_iso()

    # ISSUE-17: compute cost_usd live when absent from the payload.
    # Work on a local copy so we never mutate the caller's dict.
    usage: dict[str, Any] = dict(payload.get("usage") or {})

    # JSONL enrichment (telemetry-unify): when the caller passes an agent_id
    # (from the Agent tool's result), pull billing-truth usage from the
    # sub-agent JSONL. JSONL wins for input/output/cache_* and model; we keep
    # caller-provided tool_calls and duration_ms only if JSONL lacks them.
    agent_id = payload.get("agent_id") or usage.get("agent_id")
    if agent_id:
        try:
            from orchestrator_next.jsonl_usage import (
                extract_agent_usage,
                extract_tool_calls,
                locate_subagent_jsonl_path,
            )
            repo_root = state_raw.get("repo_root") or ""
            jsonl_usage = extract_agent_usage(repo_root, agent_id)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "model",
                "turns",
            ):
                if jsonl_usage.get(key) is not None:
                    usage[key] = jsonl_usage[key]
            # duration_ms / tool_calls only if caller didn't supply them.
            if usage.get("duration_ms") is None and jsonl_usage.get("duration_ms") is not None:
                usage["duration_ms"] = jsonl_usage["duration_ms"]
            if not usage.get("tool_calls") and jsonl_usage.get("tool_calls"):
                usage["tool_calls"] = jsonl_usage["tool_calls"]
            # Per-tool-call detail (name + wall-clock duration per invocation).
            # upsert.py writes one tool_calls row per entry with duration_ms populated.
            jsonl_path = locate_subagent_jsonl_path(repo_root, agent_id)
            if jsonl_path is not None:
                detail = extract_tool_calls(jsonl_path)
                if detail:
                    usage["tool_calls_detail"] = detail
            usage["agent_id"] = agent_id  # persist it so upsert writes the column
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
            sys.stderr.write(f"[record] jsonl enrichment failed for agent_id={agent_id}: {exc}\n")

    if db is not None and not usage.get("cost_usd"):
        resolved_model, computed_cost = _compute_cost_usd(db, agent, usage)
        if resolved_model is not None and computed_cost is not None:
            usage["model"] = resolved_model
            usage["cost_usd"] = computed_cost

    entry: dict[str, Any] = {
        "step_id": step_id,
        "phase": phase,
        "status": status,
        "agent": payload.get("agent", "inline"),
        "attempt": payload.get("attempt", attempt),
        "started_at": payload.get("started_at", now),
        "ended_at": now,
        "usage": usage,
        "evidence": {"outputs": outputs, **(payload.get("evidence") or {})},
    }
    # BEFORE appending, strip any in_progress placeholder for this (step_id, phase).
    # The terminal record supersedes the in_progress entry; keeping both would
    # cause duplicate entries and break invariant checks (T-11 / FR-6, AC-3).
    history[:] = [
        h for h in history
        if not (
            h.get("status") == "in_progress"
            and h.get("step_id") == step_id
            and h.get("phase") == phase
        )
    ]
    history.append(entry)
    state_raw["step_history"] = history

    # FR-2: abandoned status → set state.yaml.status = blocked
    if status == "abandoned":
        state_raw["status"] = "blocked"

    # Advance next_step
    next_step = _compute_next_step(state_raw, step_id, state_yaml_path)
    if next_step:
        state_raw["next_step"] = next_step

    with open(path, "w") as f:
        yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)

    # Check C (post-write): verify the written file is valid YAML.
    # If serialization produced invalid content, restore the pre-write bytes.
    try:
        with open(path) as f:
            yaml.safe_load(f)
    except yaml.YAMLError as e:
        with open(path, "wb") as f:
            f.write(pre_write_bytes)
        return (
            {
                "action": "error",
                "reason": "state_yaml_parse_failure",
                "detail": str(e),
                "hint": (
                    "state.yaml parse failed after record. "
                    "File has been restored to pre-write state. "
                    "Likely an earlier hand-edit corrupted the file."
                ),
            },
            4,
        )

    # T-11 / FR-7: Delete the in_progress DB row now that state.yaml is durably
    # written. This runs after the post-write YAML parse check so that if the
    # write or parse fails (and the file is restored) we do NOT delete the DB row
    # — the next reconcile will then materialise from DB, preserving crash safety.
    # Best-effort: a DELETE failure must not block the caller.
    if db is not None:
        try:
            change_id_val = state_raw.get("change_id") or ""
            repo_root_val = state_raw.get("repo_root") or ""
            db.execute(
                "DELETE FROM step_events"
                " WHERE repo_root = ?"
                " AND change_id = ?"
                " AND phase = ?"
                " AND step_id = ?"
                " AND status = 'in_progress'",
                [repo_root_val, change_id_val, phase, step_id],
            )
        except Exception as exc:  # noqa: BLE001 — DB cleanup is best-effort
            sys.stderr.write(
                f"[record] warning: failed to delete in_progress DB row for "
                f"{step_id!r} phase={phase!r}: {exc}\n"
            )

    # FR-2/FR-5/FR-6/FR-6a/Phase-5: step_events upsert + boundary writes.
    # Non-boundary calls: fail-soft (preserves current record.py behavior).
    # Boundary calls: fatal-on-failure (BEGIN/COMMIT/ROLLBACK).
    # Subagent JSONL parsing runs OUTSIDE the transaction to keep the
    # BEGIN/COMMIT window short (design.md Trade-offs).
    if db is not None:
        from orchestrator_next.parser import _parse_history_entry

        change_id_val = state_raw.get("change_id") or ""
        repo_root_val = state_raw.get("repo_root") or ""
        ctx = {"repo_root": repo_root_val, "change_id": change_id_val}
        workflow_plan = state_raw.get("workflow_plan") or {}

        # Build a StepHistoryEntry from the entry dict for upsert_step_event
        _step_entry = _parse_history_entry(entry)

        # Phase 5 (FR-3): absorbed feature_metrics write.
        # Fires when step_id == "mark-change-completed" AND status == "completed".
        # Resolve runs OUTSIDE BEGIN (git-log + tasks.md parsing — keeps tx window short).
        if step_id == "mark-change-completed" and status == "completed":
            try:
                fm_data = _resolve_feature_metrics(state_raw, change_id_val)
            except Exception as exc:
                sys.stderr.write(f"[done] feature_metrics resolution failed: {exc}\n")
                return (
                    {
                        "action": "error",
                        "reason": "feature_metrics_resolution_failed",
                        "detail": str(exc),
                    },
                    5,
                )
            db.execute("BEGIN")
            try:
                upsert_step_event(db, _step_entry, ctx)
                _write_feature_metrics(db, repo_root_val, change_id_val, fm_data)
                db.execute("COMMIT")
            except Exception as exc:
                db.execute("ROLLBACK")
                sys.stderr.write(f"[done] feature_metrics write failed: {exc}\n")
                return (
                    {
                        "action": "error",
                        "reason": "feature_metrics_write_failed",
                        "detail": str(exc),
                    },
                    5,
                )
            _phase5_handled = True
        else:
            _phase5_handled = False

        if _phase5_handled:
            # Phase 5 path handled the step write — skip the Phase 4 boundary path.
            # mark-change-completed is NOT phase-last in _complete-phase.yaml so
            # _detect_boundary would have returned NONE anyway; the trigger upgrades
            # it to a fatal transactional write for that one step.
            pass
        else:
            boundary = _detect_boundary(workflow_plan, phase, step_id, status)
            if boundary == BoundaryKind.NONE:
                # Non-boundary: fail-soft step write
                try:
                    upsert_step_event(db, _step_entry, ctx)
                except Exception as exc:  # noqa: BLE001 — non-boundary is fail-soft
                    sys.stderr.write(f"[done] step write failed: {exc}\n")
            else:
                # Boundary path: JSONL parsing runs BEFORE BEGIN to keep tx window short.
                subagent_rows: list[dict] = []
                session: dict = {}
                if boundary == BoundaryKind.FEATURE:
                    # _resolve_driver_session and _resolve_subagent_rows run OUTSIDE BEGIN.
                    try:
                        session = _resolve_driver_session(state_raw, change_id_val, db=db)
                    except Exception as _exc:
                        sys.stderr.write(
                            f"[done] driver session resolution failed: {_exc}\n"
                        )
                        return (
                            {
                                "action": "error",
                                "reason": "driver_session_resolution_failed",
                                "detail": str(_exc),
                            },
                            5,
                        )
                    subagent_rows = _resolve_subagent_rows(
                        repo_root_val, change_id_val, session.get("session_id", "")
                    )

                # Atomic boundary write — fatal on failure (NFR-2, NFR-3, OQ-2).
                db.execute("BEGIN")
                try:
                    upsert_step_event(db, _step_entry, ctx)
                    _write_phase_event(
                        db, repo_root_val, change_id_val, phase, entry["attempt"]
                    )
                    if boundary == BoundaryKind.FEATURE:
                        _write_driver_session(db, repo_root_val, change_id_val, session)
                        _write_subagent_events(db, repo_root_val, change_id_val, subagent_rows)
                    db.execute("COMMIT")
                except Exception as _exc:
                    db.execute("ROLLBACK")
                    sys.stderr.write(
                        f"[done] boundary write failed ({boundary.value}): {_exc}\n"
                    )
                    return (
                        {
                            "action": "error",
                            "reason": "boundary_write_failed",
                            "boundary": boundary.value,
                            "detail": str(_exc),
                        },
                        5,
                    )  # fatal non-zero exit

    # Workflow-issue retro: if the payload carries workflow_issues, append
    # each one to spec/changes/<change_id>/retro.md. Best-effort — any
    # failure here never blocks the record.
    _retro_appended = 0
    issues = payload.get("workflow_issues")
    if isinstance(issues, list) and issues:
        worktree = state_raw.get("worktree_path") or state_raw.get("repo_root") or ""
        change_id = state_raw.get("change_id") or state_raw.get("slug") or ""
        if worktree and change_id:
            try:
                import os as _os
                import subprocess as _sp
                script = _os.path.join(
                    state_raw.get("repo_root") or "",
                    "scripts", "inline", "append-retro.sh",
                )
                if not _os.path.isfile(script):
                    home = _os.environ.get("ORCHESTRATOR_HOME", "")
                    if home:
                        script = _os.path.join(home, "scripts", "inline", "append-retro.sh")
                if _os.path.isfile(script):
                    for issue in issues:
                        # per-issue phase/step fallback
                        issue.setdefault("surfaced_at", f"{phase}/{step_id}")
                    env = {
                        **_os.environ,
                        "WORKTREE_PATH": _os.path.expanduser(str(worktree)),
                        "CHANGE_ID": str(change_id),
                        "ISSUES_JSON": json.dumps(issues),
                    }
                    result = _sp.run(["bash", script], env=env, capture_output=True, text=True)
                    if result.returncode == 0 and result.stdout.strip():
                        try:
                            _retro_appended = int(json.loads(result.stdout.strip()).get("appended", 0))
                        except Exception:
                            _retro_appended = 0
            except Exception as exc:  # noqa: BLE001 — retro logging is best-effort
                sys.stderr.write(f"[record] retro append failed: {exc}\n")

    response: dict[str, Any] = {
        "action": "recorded",
        "step_id": step_id,
        "attempt": entry["attempt"],
        "next_step": next_step,
    }
    if _retro_appended:
        response["retro_appended"] = _retro_appended
    return (response, 0)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: orchestrator done <state.yaml>  (JSON payload on stdin)",
              file=sys.stderr)
        return 3
    state_yaml_path = argv[1]
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON payload — {exc}", file=sys.stderr)
        return 3

    # Resolve the metrics DB path: METRICS_DB env var, else $ORCHESTRATOR_HOME/metrics.duckdb.
    db_path_str = os.environ.get("METRICS_DB")
    if not db_path_str:
        db_path_str = str(_orchestrator_home() / "metrics.duckdb")
    db_path = Path(db_path_str)

    db = None
    if db_path.exists():
        try:
            import duckdb as _duckdb
            from orchestrator_next.upsert import ensure_schema as _ensure_schema
            db = _duckdb.connect(str(db_path))
            _ensure_schema(db)
        except Exception as exc:
            sys.stderr.write(
                f"[record] warning: failed to open metrics DB {db_path}: {exc}; "
                f"cost computation will be skipped\n"
            )
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
            db = None
    else:
        sys.stderr.write(
            f"[record] warning: metrics DB not found at {db_path}; "
            f"cost computation will be skipped\n"
        )

    try:
        result, code = record(state_yaml_path, payload, db=db)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    print(json.dumps(result, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
