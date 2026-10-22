"""Every workflow in the pack runs to completion on the stub agent, driven only through the CLI."""

from __future__ import annotations

import time

import pytest
import yaml
from conftest import pack_dir, pack_workflows


def question_steps(workflow: str) -> dict[str, str]:
    """{step: enum key} for every step in the workflow whose contract can ask a question (await_input)."""
    pack, found = pack_dir(), {}
    for entry in yaml.safe_load((pack / "workflows" / f"{workflow}.yaml").read_text())["steps"]:
        step = entry if isinstance(entry, str) else entry.get("id")
        contract = pack / "steps" / str(step) / "contract.yaml"
        if not contract.is_file():
            continue
        for key, spec in (yaml.safe_load(contract.read_text()).get("out") or {}).items():
            if isinstance(spec, dict) and "await_input" in (spec.get("values") or []):
                found[step] = key
    return found


CASES = [(w, False) for w in pack_workflows()] + [(w, True) for w in pack_workflows() if question_steps(w)]


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
    """ask: every step able to ask a question does, once, and the run continues from each answer."""
    asking = question_steps(workflow) if ask else {}
    env.submit(workflow, {step: {key: "await_input"} for step, key in asking.items()}, workflow=workflow)
    res = drive(env, workflow)
    r = res["run"]
    assert r["status"] == "done", str(r["outcome"])[:800]
    hist = r["outcome"]["history"]
    assert hist and r["loop"]["state"]["step_history"] == hist
    ran = [h["step"] for h in hist if h["step"] in asking]  # a routed-around step never asks
    assert res["questions"] == list(dict.fromkeys(ran)) and (res["questions"] or not ask)
    for step in res["questions"]:
        assert [h["attempt"] for h in hist if h["step"] == step] == [1, 2]
    assert set(res["gates"]) == {h["step"] for h in hist if h.get("gate")}
    assert 0 < len(res["gates"]) <= env.cfg_workflows()[workflow]["gates"]  # a routed-around gate is not opened
    state = env.run_dir(workflow) / "state.yaml"
    assert state.exists() and "step_history" in state.read_text()
    print(
        f"\nE2E {workflow} ask={ask}: {len(hist)} history entries, gates={res['gates']}, "
        f"questions={res['questions']}, status={r['status']}"
    )
