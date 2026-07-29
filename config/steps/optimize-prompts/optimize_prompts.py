"""Closed-loop prompt optimization for the packs whose steps ran this change.

Per evaluable pack: `prompt-optimize gepa` -> `prompt-eval compare --split dev`
-> `compare --split holdout` -> `prompt-optimize promote`. A gate failure
(compare exit 1) retries GEPA with a fresh run, up to
ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_RETRIES extra attempts; a hard error
(compare exit 2, or gepa/promote nonzero other than gepa-noop) aborts that
pack without retrying. Seed-identical GEPA (exit 3 / gepa-noop) records
outcome `noop` and skips compare + retries.

Promotion rewrites only the pack's own charter body (leaf-overlay write), so
the next workflow run picks the improved prompt up automatically. The learn
step's train.jsonl appends feed each subsequent optimization round.

Off unless ORCHESTRATOR_PROMPT_OPTIMIZE=1: GEPA invokes candidate, judge, and
reflection models. Advisory step — always emits status completed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Pack discovery is shared with the sibling eval-prompts step.
_EVAL_STEP_DIR = Path(__file__).resolve().parent.parent / "eval-prompts"
if str(_EVAL_STEP_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_STEP_DIR))

from eval_prompts import (  # noqa: E402
    _config_root,
    _load_state,
    _prompt_search_dirs,
    evaluable_packs,
)

_RUN_ARTIFACT_RE = re.compile(r"^run artifact: (.+)$", re.MULTILINE)

# Mirror prompt-optimizer/optimize.py floors (DSPy GEPA default minibatch = 3).
_REFLECTION_MINIBATCH_SIZE = 3
_DEFAULT_MAX_METRIC_CALLS = 40
# prompt-optimize gepa exit when candidate == baseline (see GEPA_NOOP_EXIT).
_GEPA_NOOP_EXIT = 3


def all_scenario_packs(repo_root: str, config_root: Path) -> list[tuple[str, Path]]:
    """Every pack with scenarios/ across the prompt search dirs (first hit wins).

    Standalone-mode discovery for the `optimize` workflow, where state.yaml
    carries no completed prompt steps to derive packs from. The correlation
    step_id is the pack name.
    """
    packs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for root in _prompt_search_dirs(repo_root, config_root):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.name in seen or not (child / "scenarios").is_dir():
                continue
            seen.add(child.name)
            packs.append((child.name, child.resolve()))
    return packs


def has_new_scenarios(pack_dir: Path) -> bool:
    """True when train.jsonl changed after the newest optimizer run started.

    The "truly needed" gate: optimization is only worth re-running once
    learning has appended scenarios the last GEPA run never saw.
    """
    # ponytail: mtime heuristic — compares train.jsonl against the newest
    # runs/<id>/ dir; switch to reading manifest timestamps if mtimes prove
    # unreliable (e.g. git checkouts resetting them).
    runs = pack_dir / "runs"
    run_dirs = [d for d in runs.iterdir() if d.is_dir()] if runs.is_dir() else []
    if not run_dirs:
        return True  # never optimized
    train = pack_dir / "scenarios" / "train.jsonl"
    if not train.is_file():
        return False
    return train.stat().st_mtime > max(d.stat().st_mtime for d in run_dirs)


def _train_line_count(pack_dir: Path) -> int:
    """Count non-blank lines in the pack's train.jsonl (leaf bank only)."""
    train = pack_dir / "scenarios" / "train.jsonl"
    if not train.is_file():
        return 0
    return sum(1 for line in train.read_text().splitlines() if line.strip())


def metric_call_floor(train_size: int) -> int:
    """``len(train) + reflection_minibatch_size`` — matches prompt-optimizer."""
    return train_size + _REFLECTION_MINIBATCH_SIZE


def external_call_floor(train_size: int) -> int:
    """Seed full-valset + one mutate cycle — matches prompt-optimizer.

    ``4 * train + 2 * minibatch + 1``
    """
    return 4 * train_size + 2 * _REFLECTION_MINIBATCH_SIZE + 1


def _emit(status: str, summary: str, packs: list[dict] | None = None) -> None:
    print(json.dumps({
        "status": status,
        "outputs": {"prompt_optimize": {"status": status, "packs": packs or []}},
        "evidence": {"summary": summary},
    }))


def _log(msg: str) -> None:
    print(f"optimize-prompts: {msg}", file=sys.stderr)


def _command_prefix() -> tuple[list[str], list[str]] | None:
    """(prompt-optimize argv prefix, prompt-eval argv prefix), or None.

    ORCHESTRATOR_PROMPT_OPTIMIZE_BIN / ORCHESTRATOR_PROMPT_EVAL_BIN win when
    set (tests point them at stubs); otherwise both run from the
    prompt-optimizer checkout through uv.
    """
    optimize_bin = os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_BIN") or ""
    eval_bin = os.environ.get("ORCHESTRATOR_PROMPT_EVAL_BIN") or ""
    if optimize_bin:
        return [optimize_bin], [eval_bin or optimize_bin]
    optimizer_dir = os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR") or ""
    if not optimizer_dir or not Path(optimizer_dir).is_dir():
        return None
    uv = ["uv", "run", "--locked", "--project", optimizer_dir]
    return [*uv, "prompt-optimize"], [*uv, "prompt-eval"]


def _run_dir_from(stdout: str, pack_dir: Path) -> Path | None:
    match = _RUN_ARTIFACT_RE.search(stdout)
    if match:
        path = Path(match.group(1).strip())
        if path.is_dir():
            return path
    runs = pack_dir / "runs"
    if runs.is_dir():
        candidates = [d for d in runs.iterdir() if d.is_dir()]
        if candidates:
            return max(candidates, key=lambda d: d.stat().st_mtime)
    return None


def _resolve_gepa_budgets(pack_dir: Path) -> tuple[int, int] | dict:
    """Return (max_metric_calls, max_external_calls) or an underbudget result dict."""
    train_n = _train_line_count(pack_dir)
    if train_n <= 0:
        return {
            "pack": pack_dir.name,
            "outcome": "error",
            "detail": "train.jsonl missing or empty",
            "attempts": 0,
        }
    mmc_floor = metric_call_floor(train_n)
    ext_floor = external_call_floor(train_n)

    raw_mmc = (os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS") or "").strip()
    if raw_mmc:
        try:
            mmc = int(raw_mmc)
        except ValueError:
            return {
                "pack": pack_dir.name,
                "outcome": "error",
                "detail": f"invalid ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS={raw_mmc!r}",
                "attempts": 0,
            }
    else:
        mmc = _DEFAULT_MAX_METRIC_CALLS

    if mmc < mmc_floor:
        return {
            "pack": pack_dir.name,
            "outcome": "underbudget",
            "detail": (
                f"max_metric_calls {mmc} < floor {mmc_floor} "
                f"(train={train_n} + minibatch={_REFLECTION_MINIBATCH_SIZE})"
            ),
            "attempts": 0,
        }

    raw_ext = (os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_EXTERNAL_CALLS") or "").strip()
    if raw_ext:
        try:
            external = int(raw_ext)
        except ValueError:
            return {
                "pack": pack_dir.name,
                "outcome": "error",
                "detail": f"invalid ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_EXTERNAL_CALLS={raw_ext!r}",
                "attempts": 0,
            }
        if external < ext_floor:
            return {
                "pack": pack_dir.name,
                "outcome": "underbudget",
                "detail": (
                    f"max_external_calls {external} < floor {ext_floor} "
                    f"(4*train + 2*minibatch + 1; train={train_n})"
                ),
                "attempts": 0,
            }
    else:
        external = max(mmc * 3, ext_floor)

    return mmc, external


def _optimize_pack(
    optimize_cmd: list[str], eval_cmd: list[str], pack_dir: Path, max_retries: int,
    step_id: str = "",
) -> dict:
    """One pack through the gepa -> dev -> holdout -> promote loop."""
    # Correlation parity with eval-prompts: rows stamp the step whose prompt
    # is being optimized, not this step's own id.
    env = dict(os.environ)
    if step_id:
        env["ORCHESTRATOR_STEP_ID"] = step_id

    budgets = _resolve_gepa_budgets(pack_dir)
    if isinstance(budgets, dict):
        _log(f"{pack_dir.name}: {budgets['outcome']} — {budgets['detail']}")
        return budgets
    mmc, external = budgets
    gepa_cmd = [
        *optimize_cmd,
        "gepa",
        "--pack",
        str(pack_dir),
        "--max-metric-calls",
        str(mmc),
        "--max-external-calls",
        str(external),
    ]

    attempts = 0
    while attempts <= max_retries:
        attempts += 1
        _log(f"{pack_dir.name}: gepa attempt {attempts}/{max_retries + 1}")
        gepa = subprocess.run(gepa_cmd, capture_output=True, text=True, env=env)
        sys.stderr.write(gepa.stderr)
        sys.stderr.write(gepa.stdout)
        if gepa.returncode == _GEPA_NOOP_EXIT:
            run_dir = _run_dir_from(gepa.stdout, pack_dir)
            _log(f"{pack_dir.name}: gepa-noop (seed-identical); skipping compare/retry")
            result = {
                "pack": pack_dir.name,
                "outcome": "noop",
                "detail": "gepa-noop: seed-identical candidate",
                "attempts": attempts,
            }
            if run_dir is not None:
                result["run_dir"] = str(run_dir)
            return result
        if gepa.returncode == 2:
            return {
                "pack": pack_dir.name,
                "outcome": "underbudget",
                "detail": f"gepa preflight exit {gepa.returncode}",
                "attempts": attempts,
            }
        if gepa.returncode != 0:
            return {"pack": pack_dir.name, "outcome": "error",
                    "detail": f"gepa exit {gepa.returncode}", "attempts": attempts}
        run_dir = _run_dir_from(gepa.stdout, pack_dir)
        if run_dir is None:
            return {"pack": pack_dir.name, "outcome": "error",
                    "detail": "no run artifact found", "attempts": attempts}

        gate_failed = False
        for split in ("dev", "holdout"):
            compare = subprocess.run(
                [*eval_cmd, "compare", "--pack", str(pack_dir),
                 "--run-dir", str(run_dir), "--split", split],
                env=env,
            )
            if compare.returncode == 1:
                _log(f"{pack_dir.name}: {split} gate failed (attempt {attempts})")
                gate_failed = True
                break
            if compare.returncode != 0:
                return {"pack": pack_dir.name, "outcome": "error",
                        "detail": f"compare {split} exit {compare.returncode}",
                        "attempts": attempts, "run_dir": str(run_dir)}
        if gate_failed:
            continue  # fresh GEPA run; a new candidate may clear the gates

        promote = subprocess.run(
            [*optimize_cmd, "promote", "--pack", str(pack_dir), "--run-dir", str(run_dir)],
            env=env,
        )
        if promote.returncode != 0:
            return {"pack": pack_dir.name, "outcome": "error",
                    "detail": f"promote exit {promote.returncode}",
                    "attempts": attempts, "run_dir": str(run_dir)}
        _log(f"{pack_dir.name}: promoted {run_dir.name}")
        return {"pack": pack_dir.name, "outcome": "promoted",
                "attempts": attempts, "run_dir": str(run_dir)}

    return {"pack": pack_dir.name, "outcome": "gates-failed", "attempts": attempts}


def main() -> int:
    if os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE") != "1":
        _log("ORCHESTRATOR_PROMPT_OPTIMIZE is not 1 — skipping (optimization costs model calls)")
        _emit("completed", "prompt optimization disabled (ORCHESTRATOR_PROMPT_OPTIMIZE != 1)")
        return 0

    commands = _command_prefix()
    if commands is None:
        _log("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR unset or missing — skipping")
        _emit("completed", "prompt-optimizer not configured")
        return 0
    optimize_cmd, eval_cmd = commands

    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH") or os.environ.get("STATE_YAML_PATH") or ""
    state = _load_state(state_path) if state_path else {}
    repo_root = os.environ.get("ORCHESTRATOR_REPO_ROOT") or os.environ.get("REPO_ROOT") or ""
    packs = evaluable_packs(state, _config_root(), repo_root)
    if not packs:
        # Standalone `optimize` workflow: no prompt steps in state — sweep
        # every pack with scenarios instead.
        packs = all_scenario_packs(repo_root, _config_root())
    if not packs:
        _log("no pack with scenarios/ found — nothing to optimize")
        _emit("completed", "no optimizable packs")
        return 0

    if os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_FORCE") != "1":
        stale = [p for p in packs if not has_new_scenarios(p[1])]
        if stale:
            _log(
                "skipping (no new train scenarios since last run): "
                + ", ".join(d.name for _, d in stale)
            )
        packs = [p for p in packs if p not in stale]
    if not packs:
        _emit("completed", "all packs already optimized against current scenarios")
        return 0

    # Opt-in detach: optimization only writes optimizer artifacts and (on
    # success) the pack charter — nothing downstream in the run reads them.
    if (
        os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_ASYNC") == "1"
        and os.environ.get("_OPTIMIZE_PROMPTS_DETACHED") != "1"
    ):
        log_path = (Path(state_path).parent if state_path else Path.cwd()) / "optimize-prompts.log"
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__)],
                env={**os.environ, "_OPTIMIZE_PROMPTS_DETACHED": "1"},
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        names = [{"pack": pack_dir.name, "outcome": "detached"} for _, pack_dir in packs]
        _emit("completed", f"optimizing {len(packs)} pack(s) detached; log: {log_path}", names)
        return 0

    try:
        max_retries = int(os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_RETRIES") or "2")
    except ValueError:
        max_retries = 2

    seen: set[Path] = set()
    results: list[dict] = []
    for step_id, pack_dir in packs:
        if pack_dir in seen:
            continue  # several steps can share one pack; optimize it once
        seen.add(pack_dir)
        results.append(
            _optimize_pack(optimize_cmd, eval_cmd, pack_dir, max_retries, step_id)
        )

    promoted = sum(1 for r in results if r["outcome"] == "promoted")
    _emit(
        "completed",
        f"optimized {len(results)} pack(s): {promoted} promoted, "
        f"{sum(1 for r in results if r['outcome'] == 'gates-failed')} gates-failed, "
        f"{sum(1 for r in results if r['outcome'] == 'noop')} noop, "
        f"{sum(1 for r in results if r['outcome'] == 'underbudget')} underbudget, "
        f"{sum(1 for r in results if r['outcome'] == 'error')} error",
        results,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
