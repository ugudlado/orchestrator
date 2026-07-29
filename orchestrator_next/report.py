"""Workflow metrics report — per-step duration/model/tokens/cost, plus
cross-workflow aggregation.

Single-run mode reads one state.yaml (merging sibling `*_state.yaml` and any
archived state for the same change_id) and prints a per-step table to stderr,
returning the same figures as a dict for structured output.

Aggregate mode (`orchestrator report --all`) scans every state.yaml it can
find under a repo (active .orchestrator/ runs + spec/changes/archive/) and
groups step_history by step_id across change_ids: run count, avg duration,
avg cost, retry rate, failure rate.
"""
from __future__ import annotations

import glob as _glob
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

import yaml


def load_state(path: str | Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


def change_id_of(state: dict) -> str:
    return str(state.get("change_id") or state.get("slug") or "")


def resolve_state(state_path: str, repo_root: str) -> tuple[Path, dict] | tuple[None, None]:
    path = Path(state_path)
    if path.is_file():
        return path, load_state(path)
    cid_hint = path.parent.name
    pattern = os.path.join(repo_root, "spec", "changes", "archive", f"*{cid_hint}", "state.yaml")
    for p in sorted(_glob.glob(pattern)):
        candidate = Path(p)
        if candidate.is_file():
            return candidate, load_state(candidate)
    return None, None


def collect_all_states(primary_path: Path, primary_state: dict, repo_root: str) -> list[dict]:
    """Collect step_history from all state files for this change_id (feature + complete runs)."""
    cid = change_id_of(primary_state)
    if not cid:
        return [primary_state]

    # Gather all state files from the .orchestrator/<cid>/ dir (siblings of primary)
    state_dir = primary_path.parent
    sibling_files = sorted(state_dir.glob("*_state.yaml"))

    # Also check archive dir for any archived state
    archive_pattern = os.path.join(repo_root, "spec", "changes", "archive", f"*{cid}", "state.yaml")
    archive_files = [Path(p) for p in sorted(_glob.glob(archive_pattern))]

    seen = set()
    states = []
    for f in sibling_files + archive_files:
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        s = load_state(f)
        if change_id_of(s) == cid:
            states.append(s)

    return states if states else [primary_state]


def render_report(step_history: list, issues: list) -> dict:
    """Print per-step Duration/Model/In/Out/Cost table to stderr; return the same
    figures as a plain dict for structured (JSON) output."""
    if not step_history:
        return {
            "steps": [],
            "totals": {
                "duration_ms": 0,
                "tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cost_usd": 0.0,
            },
        }

    # Collapse entries by step_id: accumulate tokens/cost across all attempts,
    # track final status and total attempt count. Last model wins (KD-2).
    rows: OrderedDict = OrderedDict()
    for entry in step_history:
        if not isinstance(entry, dict):
            continue
        step_id = entry.get("step_id") or "?"
        status = entry.get("status") or "?"
        attempt = entry.get("attempt") or 1
        usage = entry.get("usage") or {}
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        # Cache tokens are billed (pricing.py reads them) and dominate real spend
        # on agent steps; keep them disjoint from input_tokens so columns sum.
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_creation = usage.get("cache_creation_input_tokens") or 0
        tokens = input_tokens + output_tokens + cache_read + cache_creation
        cost = usage.get("cost_usd") or 0.0
        duration_ms = usage.get("duration_ms") or 0
        model = usage.get("model") or ""
        entry_outputs = entry.get("outputs") or {}
        briefing = entry_outputs.get("briefing") or entry.get("briefing") or ""
        # Every attempt's briefing is kept (not just the last one) so a failed
        # attempt's "why it failed" survives alongside the retry that fixed it.
        briefing_entry = {"attempt": attempt, "status": status, "briefing": briefing}
        if step_id not in rows:
            rows[step_id] = {
                "status": status,
                "attempts": attempt,
                "tokens": tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "cost": cost,
                "duration_ms": duration_ms,
                "model": model,
                "briefings": [briefing_entry],
                "outputs": dict(entry_outputs),
            }
        else:
            rows[step_id]["status"] = status  # last status wins
            rows[step_id]["attempts"] = max(rows[step_id]["attempts"], attempt)
            rows[step_id]["outputs"] = dict(entry_outputs)  # last attempt wins
            rows[step_id]["tokens"] += tokens
            rows[step_id]["input_tokens"] += input_tokens
            rows[step_id]["output_tokens"] += output_tokens
            rows[step_id]["cache_read_input_tokens"] += cache_read
            rows[step_id]["cache_creation_input_tokens"] += cache_creation
            rows[step_id]["cost"] += cost
            rows[step_id]["duration_ms"] += duration_ms
            if model:
                rows[step_id]["model"] = model  # last model wins
            rows[step_id]["briefings"].append(briefing_entry)

    sys.stderr.write("\n## Workflow step report\n\n")
    sys.stderr.write(
        f"{'Step':<35} {'Status':<12} {'Att':>4} {'Duration':>10} "
        f"{'Model':<14} {'In':>9} {'CacheR':>11} {'CacheW':>10} {'Out':>8} {'Cost':>10}\n"
    )
    sys.stderr.write(
        f"{'-'*35} {'-'*12} {'-'*4} {'-'*10} {'-'*14} {'-'*9} {'-'*11} "
        f"{'-'*10} {'-'*8} {'-'*10}\n"
    )

    total_tokens = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0
    total_cost = 0.0
    total_ms = 0

    for step_id, r in rows.items():
        attempts = r["attempts"]
        duration_ms = r["duration_ms"]
        tokens = r["tokens"]
        input_tokens = r["input_tokens"]
        output_tokens = r["output_tokens"]
        cache_read = r["cache_read_input_tokens"]
        cache_creation = r["cache_creation_input_tokens"]
        cost = r["cost"]
        model = r["model"]
        briefings = r["briefings"]

        total_ms += duration_ms
        total_tokens += tokens
        total_input += input_tokens
        total_output += output_tokens
        total_cache_read += cache_read
        total_cache_creation += cache_creation
        total_cost += cost

        att_str = f"{attempts} ✗" if attempts > 1 else "1"
        dur_str = f"{duration_ms / 1000:.1f}s" if duration_ms else "—"
        model_str = model if model else "—"
        in_str = f"{input_tokens:,}" if input_tokens else "—"
        cr_str = f"{cache_read:,}" if cache_read else "—"
        cw_str = f"{cache_creation:,}" if cache_creation else "—"
        out_str = f"{output_tokens:,}" if output_tokens else "—"
        cost_str = f"${cost:.4f}" if cost else "—"

        sys.stderr.write(
            f"{step_id:<35} {r['status']:<12} {att_str:>4} {dur_str:>10} "
            f"{model_str:<14} {in_str:>9} {cr_str:>11} {cw_str:>10} "
            f"{out_str:>8} {cost_str:>10}\n"
        )
        # One line per attempt, full text (no truncation) — a failed attempt's
        # "why it failed" must survive next to the retry that fixed it.
        for b in briefings:
            if not b["briefing"]:
                continue
            tag = f"attempt {b['attempt']} ({b['status']})" if len(briefings) > 1 else b["status"]
            text = b["briefing"].replace("\n", " ")
            sys.stderr.write(f"    [{tag}] {text}\n")
        # Print novel output keys (skip briefing, already rendered above).
        for key, val in (r.get("outputs") or {}).items():
            if key == "briefing":
                continue
            val_str = str(val)
            sys.stderr.write(
                f"    [output] {key}={val_str[:80]}{'…' if len(val_str) > 80 else ''}\n"
            )
        # Print any novel output keys (truncated) from the last attempt's outputs.
        for key, value in (r.get("outputs") or {}).items():
            if key == "briefing":
                continue  # already rendered above
            v_str = str(value)
            truncated = v_str[:80] + "…" if len(v_str) > 80 else v_str
            sys.stderr.write(f"    [output] {key}={truncated}\n")

    sys.stderr.write(
        f"\n{'TOTAL':<35} {'':12} {'':>4} {total_ms/1000:>9.1f}s "
        f"{'':14} {total_input:>9,} {total_cache_read:>11,} "
        f"{total_cache_creation:>10,} {total_output:>8,} ${total_cost:>9.4f}\n"
    )
    sys.stderr.write(
        f"{'':<35} {'':12} {'':>4} {'':>10} {'':14} "
        f"{'all tokens: ' + format(total_tokens, ',')}\n"
    )

    if issues:
        sys.stderr.write(f"\n## Workflow issues ({len(issues)})\n\n")
        sys.stderr.write("| Severity | Category | Detail | Fix direction |\n")
        sys.stderr.write("|---|---|---|---|\n")
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            sev = issue.get("severity") or "—"
            cat = issue.get("category") or "—"
            det = (issue.get("detail") or "").replace("\n", " ")[:120]
            fix = (issue.get("fix_direction") or "—").replace("\n", " ")
            sys.stderr.write(f"| {sev} | {cat} | {det} | {fix} |\n")
        sys.stderr.write("\n")

    return {
        "steps": [
            {
                "step_id": step_id,
                "status": r["status"],
                "attempts": r["attempts"],
                "duration_ms": r["duration_ms"],
                "tokens": r["tokens"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_input_tokens": r["cache_read_input_tokens"],
                "cache_creation_input_tokens": r["cache_creation_input_tokens"],
                "model": r["model"] or None,
                "cost_usd": round(r["cost"], 6),
                "briefings": [b for b in r["briefings"] if b["briefing"]],
            }
            for step_id, r in rows.items()
        ],
        "totals": {
            "duration_ms": total_ms,
            "tokens": total_tokens,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_input_tokens": total_cache_read,
            "cache_creation_input_tokens": total_cache_creation,
            "cost_usd": round(total_cost, 6),
        },
    }


def report_for_state(state_path: str, repo_root: str) -> dict | None:
    """Single-run report payload for one state.yaml (None when unresolvable)."""
    path, state = resolve_state(state_path, repo_root)
    if path is None:
        sys.stderr.write(f"report: state.yaml not found at {state_path}\n")
        return None
    cid = change_id_of(state)
    if not cid:
        sys.stderr.write("report: change_id missing in state.yaml\n")
        return None

    all_states = collect_all_states(path, state, repo_root)
    step_history: list = []
    issues: list = []
    schemas_run: list = []
    for s in all_states:
        step_history.extend(s.get("step_history") or [])
        issues.extend(s.get("workflow_issues") or [])
        schema = s.get("schema")
        if schema and schema not in schemas_run:
            schemas_run.append(schema)

    report = render_report(step_history, issues)
    payload: dict = {
        "steps_reported": len(step_history),
        "workflow_report": {"change_id": cid, "schemas_run": schemas_run, **report},
    }
    if issues:
        payload["workflow_issues_count"] = len(issues)
    return payload


def find_state_files(repo_root: str) -> list[Path]:
    """Every state.yaml under a repo: active runs and archived ones."""
    patterns = (
        os.path.join(repo_root, ".orchestrator", "*", "state.yaml"),
        os.path.join(repo_root, ".orchestrator", "*", "*_state.yaml"),
        os.path.join(repo_root, "spec", "changes", "archive", "*", "state.yaml"),
        os.path.join(repo_root, "spec", "changes", "archive", "*", "*_state.yaml"),
    )
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for p in sorted(_glob.glob(pattern)):
            path = Path(p)
            if path not in seen and path.is_file():
                seen.add(path)
                files.append(path)
    return files


def aggregate(state_files: list[Path]) -> dict:
    """Cross-workflow per-step aggregates over many state.yaml files.

    A "run" is one step_id appearing in one workflow (change_id): attempts are
    collapsed per run first, so retry_rate = runs that needed >1 attempt and
    failure_rate = runs whose final status was not completed.
    """
    # (step_id, change_id) -> collapsed run
    runs: dict[tuple[str, str], dict] = {}
    workflows: set[str] = set()
    for path in state_files:
        state = load_state(path)
        cid = change_id_of(state) or str(path)
        for entry in state.get("step_history") or []:
            if not isinstance(entry, dict):
                continue
            step_id = str(entry.get("step_id") or "?")
            workflows.add(cid)
            usage = entry.get("usage") or {}
            key = (step_id, cid)
            run = runs.setdefault(
                key,
                {"attempts": 0, "duration_ms": 0, "cost_usd": 0.0, "status": "?"},
            )
            run["attempts"] = max(run["attempts"], entry.get("attempt") or 1)
            run["duration_ms"] += usage.get("duration_ms") or 0
            run["cost_usd"] += usage.get("cost_usd") or 0.0
            run["status"] = entry.get("status") or run["status"]

    per_step: dict[str, dict] = {}
    for (step_id, _cid), run in runs.items():
        agg = per_step.setdefault(
            step_id,
            {"runs": 0, "retried": 0, "failed": 0, "duration_ms": 0, "cost_usd": 0.0},
        )
        agg["runs"] += 1
        agg["retried"] += 1 if run["attempts"] > 1 else 0
        agg["failed"] += 1 if run["status"] != "completed" else 0
        agg["duration_ms"] += run["duration_ms"]
        agg["cost_usd"] += run["cost_usd"]

    steps = [
        {
            "step_id": step_id,
            "runs": a["runs"],
            "avg_duration_ms": round(a["duration_ms"] / a["runs"]),
            "avg_cost_usd": round(a["cost_usd"] / a["runs"], 6),
            "total_cost_usd": round(a["cost_usd"], 6),
            "retry_rate": round(a["retried"] / a["runs"], 3),
            "failure_rate": round(a["failed"] / a["runs"], 3),
        }
        for step_id, a in sorted(
            per_step.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True
        )
    ]
    return {
        "workflows": len(workflows),
        "state_files": len(state_files),
        "steps": steps,
        "totals": {
            "cost_usd": round(sum(s["total_cost_usd"] for s in steps), 6),
            "runs": sum(s["runs"] for s in steps),
        },
    }


def render_aggregate(agg: dict) -> None:
    sys.stderr.write(
        f"\n## Cross-workflow step report "
        f"({agg['workflows']} workflow(s), {agg['state_files']} state file(s))\n\n"
    )
    sys.stderr.write(
        f"{'Step':<35} {'Runs':>5} {'AvgDur':>9} {'AvgCost':>10} "
        f"{'TotCost':>10} {'Retry%':>7} {'Fail%':>7}\n"
    )
    sys.stderr.write(f"{'-'*35} {'-'*5} {'-'*9} {'-'*10} {'-'*10} {'-'*7} {'-'*7}\n")
    for s in agg["steps"]:
        sys.stderr.write(
            f"{s['step_id']:<35} {s['runs']:>5} "
            f"{s['avg_duration_ms']/1000:>8.1f}s ${s['avg_cost_usd']:>9.4f} "
            f"${s['total_cost_usd']:>9.4f} {s['retry_rate']*100:>6.0f}% "
            f"{s['failure_rate']*100:>6.0f}%\n"
        )
    totals = agg["totals"]
    sys.stderr.write(
        f"\n{'TOTAL':<35} {totals['runs']:>5} {'':>9} {'':>10} "
        f"${totals['cost_usd']:>9.4f}\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    state_path = ""
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT") or ""
    as_json = False
    all_mode = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--state" and i + 1 < len(args):
            state_path = args[i + 1]
            i += 2
        elif a == "--repo" and i + 1 < len(args):
            repo_root = args[i + 1]
            i += 2
        elif a == "--json":
            as_json = True
            i += 1
        elif a == "--all":
            all_mode = True
            i += 1
        else:
            sys.stderr.write(
                "usage: orchestrator report --state <state.yaml> [--json]\n"
                "       orchestrator report --all [--repo PATH] [--json]\n"
            )
            return 2

    if all_mode:
        root = repo_root or os.getcwd()
        agg = aggregate(find_state_files(root))
        render_aggregate(agg)
        if as_json:
            print(json.dumps(agg))
        return 0

    if not state_path:
        state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH", "")
    if not state_path:
        sys.stderr.write("error: --state or ORCHESTRATOR_STATE_YAML_PATH required\n")
        return 1
    payload = report_for_state(state_path, repo_root)
    if payload is None:
        return 1
    if as_json:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
