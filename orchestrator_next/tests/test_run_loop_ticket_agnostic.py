"""The engine has no ticketing knowledge — no fetch logic, no backend names,
no ticket-shaped params on build_prompt. Ticketing lives entirely in
config/steps/ (see config/tests/test_backlog_rest.py); this only guards the
orchestrator_next side of that boundary (originally ORC-125).
"""
from __future__ import annotations

import inspect

from orchestrator_next import run_loop


def test_run_loop_has_no_ticket_fetch_or_cli():
    src = inspect.getsource(run_loop)
    assert "_fetch_ticket_context" not in src
    assert '["backlog"' not in src and "backlog task" not in src
    sig = inspect.signature(run_loop.build_prompt)
    assert "ticket_context" not in sig.parameters
    assert "ticket_id" not in sig.parameters


def test_build_prompt_omits_ticket_block():
    text = run_loop.build_prompt("do work", "{}", "meta=1")
    assert "Ticket / bug report" not in text
    assert "do work" in text
    assert "Step context:" in text
