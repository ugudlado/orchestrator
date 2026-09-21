"""The engine has no ticketing knowledge — no fetch logic, no backend names,
no ticket-shaped params on build_prompt. Ticketing lives entirely in the pack's
steps; this only guards the orchestrator_next side of that boundary
(originally ORC-125).
"""
from __future__ import annotations
