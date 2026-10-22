"""Stub step worker: does no work, satisfies the contract. For tests and dry runs (`agent: stub`).

judgment: python -m orchestrator_driver.workers.stub <payload.json> <result.json>
    writes every `out` artifact and a result JSON with one value per out_schema key.
exec:     python -m orchestrator_driver.workers.stub --exec -
    reads the payload from $ORCH_PAYLOAD and prints the engine's stdout protocol line.

Enum values default to the first non-`await_input` value; override per step with
$ORCH_STUB_CHOICES = {"<step_id>": {"<key>": <value>}}. Special keys: `_sleep` (seconds the step takes) and
`_keep_asking` (keep returning `await_input` after an answer).
"""

import json
import os
import sys
import time
from pathlib import Path


def stub_value(spec: dict):
    t = spec.get("type")
    if t == "enum":
        return next(v for v in spec["values"] if v != "await_input")
    if t == "boolean":
        return False
    if t == "number":
        return 0
    if t == "object":
        return {}
    return "stub"


def stub_outputs(payload: dict) -> dict:
    for path in payload.get("out", {}).values():
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".json":
            p.write_text("{}\n")
        elif p.suffix in (".yaml", ".yml"):
            p.write_text("stub: true\n")
        else:
            p.write_text(f"# stub\n\nWritten by the stub worker for {payload['step_id']}.\n")
    outs = {k: stub_value(v) for k, v in payload.get("out_schema", {}).items() if not v.get("optional")}
    choices = dict(json.loads(os.environ.get("ORCH_STUB_CHOICES") or "{}").get(payload["step_id"], {}))
    time.sleep(choices.pop("_sleep", 0))  # simulate a long step, for crash tests
    keep_asking = choices.pop("_keep_asking", False)  # ask again even when answered (question-limit tests)
    if os.environ.get("ORCHESTRATOR_USER_ANSWER") and not keep_asking:  # answered: stop asking
        choices = {k: v for k, v in choices.items() if v != "await_input"}
    outs.update(choices)
    return outs


def main() -> None:
    if sys.argv[1] == "--exec":
        payload = json.loads(os.environ["ORCH_PAYLOAD"])
        print(json.dumps({"status": "completed", "outputs": stub_outputs(payload)}))
        return
    payload = json.loads(Path(sys.argv[1]).read_text())
    result = {"status": "completed", **stub_outputs(payload), "summary": "stub"}
    Path(sys.argv[2]).write_text(json.dumps(result))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
