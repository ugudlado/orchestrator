"""Every workflow in the pack runs to completion on the stub agent, driven only through the CLI."""

from __future__ import annotations

import time

import pytest
import yaml
from conftest import pack_dir, pack_workflows


def question_step(workflow: str) -> tuple[str, str] | None:
    """(step, enum key) of the first step in the workflow whose contract can ask a question (await_input)."""
    pack = pack_dir()
    for entry in yaml.safe_load((pack / "workflows" / f"{workflow}.yaml").read_text())["steps"]:
        step = entry if isinstance(entry, str) else entry.get("id")
        contract = pack / "steps" / str(step) / "contract.yaml"
        if not contract.is_file():
            continue
        for key, spec in (yaml.safe_load(contract.read_text()).get("out") or {}).items():
            if isinstance(spec, dict) and "await_input" in (spec.get("values") or []):
                return step, key
    return None


CASES = [(w, False) for w in pack_workflows()] + [(w, True) for w in pack_workflows() if question_step(w)]


def drive(env, slug: str, timeout: float = 300) -> dict:
    """Approve every gate and answer every question as they come, until the run ends."""
    gates, questions = [], []
    env.start()
    seen = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = env.run(slug)
        w = r["waiting"]
        if r["status"] in ("done", "failed", "stopped"):
            return {"run": r, "gates": gates, "questions": questions}
        if w and (r["status"], w["step_id"], w.get("attempt") or w.get("wait")) != seen:
            seen = (r["status"], w["step_id"], w.get("attempt") or w.get("wait"))
            if w["kind"] == "gate":
                gates.append(w["step_id"])
                env.cli("decide", slug, w["step_id"], w["attempt"], "approved")
            else:
                questions.append(w["step_id"])
                env.cli("answer", slug, w["step_id"], w["wait"], "--answer", "go ahead")
        time.sleep(0.05)
    raise AssertionError(f"timeout; run is {env.run(slug)['status']}")


@pytest.mark.parametrize("workflow,ask", CASES or [("<no pack>", False)])
def test_workflow_runs_to_completion(env, workflow, ask):
    """ask: the first step able to ask a question does, once, and the run continues from the answer."""
    step_key = question_step(workflow) if ask else None
    env.submit(workflow, {step_key[0]: {step_key[1]: "await_input"}} if step_key else None, workflow=workflow)
    res = drive(env, workflow)
    r = res["run"]
    assert r["status"] == "done", str(r["outcome"])[:800]
    hist = r["outcome"]["history"]
    assert hist and r["loop"]["state"]["step_history"] == hist
    assert res["questions"] == ([step_key[0]] if step_key else [])
    assert set(res["gates"]) == {h["step"] for h in hist if h.get("gate")}
    assert 0 < len(res["gates"]) <= env.cfg_workflows()[workflow]["gates"]  # a routed-around gate is not opened
    state = env.run_dir(workflow) / "state.yaml"
    assert state.exists() and "step_history" in state.read_text()
    print(
        f"\nE2E {workflow} ask={ask}: {len(hist)} history entries, gates={res['gates']}, "
        f"questions={res['questions']}, status={r['status']}"
    )
