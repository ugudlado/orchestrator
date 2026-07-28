"""Closed-loop prompt optimization for the packs whose steps ran this change.

Per evaluable pack: `prompt-optimize gepa` -> `prompt-eval compare --split dev`
-> `compare --split holdout` -> `prompt-optimize promote`. A gate failure
(compare exit 1) retries GEPA with a fresh run, up to
ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_RETRIES extra attempts; a hard error
(compare exit 2, or gepa/promote nonzero) aborts that pack without retrying.

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

from eval_prompts import _config_root, _load_state, evaluable_packs  # noqa: E402

_RUN_ARTIFACT_RE = re.compile(r"^run artifact: (.+)$", re.MULTILINE)


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
    max_metric_calls = os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZE_MAX_METRIC_CALLS") or ""
    gepa_cmd = [*optimize_cmd, "gepa", "--pack", str(pack_dir)]
    if max_metric_calls:
        gepa_cmd += ["--max-metric-calls", max_metric_calls]

    attempts = 0
    while attempts <= max_retries:
        attempts += 1
        _log(f"{pack_dir.name}: gepa attempt {attempts}/{max_retries + 1}")
        gepa = subprocess.run(gepa_cmd, capture_output=True, text=True, env=env)
        sys.stderr.write(gepa.stderr)
        sys.stderr.write(gepa.stdout)
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
        _log("no completed prompt step has a pack with scenarios/ — nothing to optimize")
        _emit("completed", "no optimizable packs")
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
        f"{sum(1 for r in results if r['outcome'] == 'error')} error",
        results,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
