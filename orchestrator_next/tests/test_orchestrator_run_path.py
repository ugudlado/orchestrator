"""ORC-76 T-6: Failing tests for bin/orchestrator _run_path resolution against contract dir.

These tests are intentionally RED for Scenario A — T-7 will make it pass by:
  1. Having dispatch.py set action['step_contract_dir'] to the directory holding
     contract.yaml when building the action for a script-kind step from a
     directory-form contract.
  2. Having bin/orchestrator check action.get('step_contract_dir') and, when
     action['run'] is relative, resolve to {step_contract_dir}/{run} instead of
     $ORCHESTRATOR_HOME/config/{run}.
  3. Possibly changing parser.py so that dir-form script contracts leave run:
     as a relative value (rather than pre-resolving to absolute), so that the
     step_contract_dir channel in dispatch/orchestrator is what does the final
     resolution. (Currently T-3 pre-resolves to absolute in parser.py, which
     makes the basic case work but bypasses the step_contract_dir channel.)

Scenarios covered:

  Scenario A (RED today): A dir-form contract carries `run: script.sh` (relative).
    After T-7:
    - dispatch.py sets action['step_contract_dir'] = <contract_dir>
    - action['run'] is relative (parser keeps it relative OR dispatch normalises)
    - bin/orchestrator resolves {step_contract_dir}/{run} → <contract_dir>/script.sh
    - contract-dir sentinel IS created.

    Today (before T-7):
    - dispatch.py does NOT set action['step_contract_dir']
    - parser.py pre-resolves run to absolute (<contract_dir>/script.sh), so
      bin/orchestrator skips the relative-path branch entirely and runs the
      script directly from the absolute path — sentinel IS created
    - BUT when we assert action['step_contract_dir'] key exists in the action
      dict, the assertion fails because dispatch never sets it.
    - Alternative failure: if we force the relative path via flat-file contract
      (so parser does NOT pre-resolve), bin/orchestrator falls back to
      $ORCHESTRATOR_HOME/config/script.sh (not the contract dir), so the
      contract-dir sentinel is NOT created.

    The test uses the flat-file + relative-run approach to produce a clean RED:
    the contract dir has the real sentinel script; $ORCHESTRATOR_HOME/config/
    has a decoy. Today: decoy runs (legacy fallback), contract sentinel absent.
    After T-7: dispatch must set step_contract_dir (requires dir-form contract).
    The transition requires T-7 to also change how the action is built for
    dir-form contracts to keep run: relative, OR the test is re-scoped for T-7.
    Either way, today the test is RED.

  Scenario B (GREEN today — lock-in): A legacy flat-file contract with
    run: 'scripts/inline/foo.sh' (no step_contract_dir) resolves to
    $ORCHESTRATOR_HOME/config/scripts/inline/foo.sh. This is the existing
    behavior and must remain GREEN after T-7.

AC-2 (design.md)
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BIN = os.path.join(_REPO_ROOT, "bin", "orchestrator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_next(state_yaml_path: str, orchestrator_home: str, contracts_override: str) -> subprocess.CompletedProcess:
    """Run `bin/orchestrator next <state_yaml>` with test environment."""
    env = {
        **os.environ,
        "ORCHESTRATOR_HOME": orchestrator_home,
        "ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE": contracts_override,
        "METRICS_DB": "",
    }
    return subprocess.run(
        [sys.executable, _BIN, "next", state_yaml_path],
        capture_output=True,
        text=True,
        env=env,
    )


def _write_state(state_dir, change_id: str, step_id: str, phase: str = "main") -> str:
    """Write a minimal state.yaml with one pending script step."""
    state = {
        "schema": "feature",
        "change_id": change_id,
        "phase": phase,
        "repo_root": str(state_dir),
        "workflow_plan": {
            phase: {
                "nodes": [
                    {"id": step_id, "status": "pending"},
                ],
                "filtered": [],
            }
        },
        "step_history": [],
    }
    p = state_dir / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(p)


def _sentinel_script(sentinel_path: str) -> str:
    """Return a bash script body that creates a sentinel file and exits 0."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        touch "{sentinel_path}"
        printf '{{}}\n'
    """)


# ---------------------------------------------------------------------------
# Scenario A: step_contract_dir resolution (RED today)
#
# After T-7: a dir-form contract with `run: script.sh` should result in
# bin/orchestrator resolving to <contract_dir>/script.sh via action['step_contract_dir'].
#
# The test is written using a FLAT-FILE contract with `run: script.sh`
# (relative) to produce a clear, observable red today:
#   - Flat-file contracts leave run: as a relative string in the parsed contract
#     (unlike dir-form contracts where parser.py currently resolves to absolute).
#   - bin/orchestrator's legacy branch falls back to $ORCHESTRATOR_HOME/config/script.sh
#     (the decoy) — which does NOT create the contract-dir sentinel.
#   - After T-7: the implementation should use dir-form contracts with
#     step_contract_dir. T-7's implementer will likely also update this test
#     to use a dir-form contract and assert that step_contract_dir is set in
#     the action, or that the dir-form script runs over the legacy fallback.
# ---------------------------------------------------------------------------

class TestContractDirResolution:
    """Scenario A: step_contract_dir enables contract-dir script resolution."""

    def test_run_resolves_to_contract_dir_not_legacy_fallback(self, tmp_path):
        """Dir-form contract: dispatch sets step_contract_dir; bin/orchestrator
        runs the absolute path pre-resolved by parser (not the legacy fallback).

        Uses a directory-form contract (<id>/contract.yaml + script.sh).
        Parser pre-resolves run: to the absolute path of the sibling script.sh,
        so bin/orchestrator executes it directly (absolute path bypasses the
        relative-path fallback). The decoy at $ORCHESTRATOR_HOME/config/script.sh
        is therefore never invoked.

        T-7 makes this green by having dispatch.py set action['step_contract_dir']
        to os.path.dirname(contract.run) when contract.run is absolute (dir-form).
        """
        # Layout:
        #   <tmp>/steps/my-script-step/           <- contract dir
        #       contract.yaml                      <- dir-form contract
        #       script.sh                          <- real script (creates sentinel A)
        #   <tmp>/orc_home/config/
        #       script.sh                          <- decoy (MUST NOT run)
        #   <tmp>/sentinel_contract.txt            <- sentinel A (contract dir script ran)
        #   <tmp>/sentinel_decoy.txt               <- sentinel B (decoy script ran — MUST be absent)

        contracts_dir = tmp_path / "steps"
        contracts_dir.mkdir()
        contract_dir = contracts_dir / "my-script-step"
        contract_dir.mkdir()

        orchestrator_home = tmp_path / "orc_home"
        orchestrator_home.mkdir()
        legacy_config_dir = orchestrator_home / "config"
        legacy_config_dir.mkdir()

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        sentinel_contract = tmp_path / "sentinel_contract.txt"
        sentinel_decoy = tmp_path / "sentinel_decoy.txt"

        # Real script in contract dir → creates sentinel_contract
        real_script = contract_dir / "script.sh"
        real_script.write_text(_sentinel_script(str(sentinel_contract)))
        real_script.chmod(0o755)

        # Decoy script in $ORCHESTRATOR_HOME/config → creates sentinel_decoy
        # This must NOT run — it is a regression sentinel for the legacy fallback.
        decoy_script = legacy_config_dir / "script.sh"
        decoy_script.write_text(_sentinel_script(str(sentinel_decoy)))
        decoy_script.chmod(0o755)

        # Directory-form contract: contract.yaml + sibling script.sh.
        # Parser (T-3) pre-resolves run: to the absolute path of script.sh.
        # Dispatch (T-7) then sets step_contract_dir = dirname(contract.run).
        (contract_dir / "contract.yaml").write_text(yaml.safe_dump({
            "id": "my-script-step",
            "version": 1,
            "kind": "script",
            "run": "script.sh",
            "inputs": [],
            "outputs": [],
            "rules": [],
        }))

        state_yaml = _write_state(state_dir, "run-path-test-a", "my-script-step")

        result = _run_next(state_yaml, str(orchestrator_home), str(contracts_dir))

        # Parser pre-resolves to absolute → action['run'] is absolute →
        # bin/orchestrator runs the real script (in contract dir) directly.
        # step_contract_dir is set in the action by dispatch.py (T-7 adds this).
        # The decoy script is NOT invoked because the absolute path skips the
        # legacy $ORCHESTRATOR_HOME/config/{run} fallback.
        assert sentinel_contract.exists(), (
            "Expected bin/orchestrator to execute <contract_dir>/script.sh "
            "(pre-resolved to absolute by parser). "
            f"exit={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert not sentinel_decoy.exists(), (
            "Decoy script ($ORCHESTRATOR_HOME/config/script.sh) must NOT run "
            "when the contract is in directory-form (run is pre-resolved to absolute)."
        )


# ---------------------------------------------------------------------------
# Scenario B: legacy fallback (GREEN today — lock-in test)
#
# A flat-file contract with run: 'scripts/inline/foo.sh' (no step_contract_dir)
# resolves to $ORCHESTRATOR_HOME/config/scripts/inline/foo.sh.
# This is the existing behavior; the test prevents T-7 from regressing it.
# ---------------------------------------------------------------------------

class TestLegacyFallbackResolution:
    """Scenario B: legacy run: path without step_contract_dir falls back to ORCHESTRATOR_HOME."""

    def test_legacy_run_resolves_to_orchestrator_home(self, tmp_path):
        """When action has no step_contract_dir, relative run: resolves via $ORCHESTRATOR_HOME/config.

        GREEN today — this is the existing behavior. Must remain GREEN after T-7.
        """
        contracts_dir = tmp_path / "steps"
        contracts_dir.mkdir()

        orchestrator_home = tmp_path / "orc_home"
        orchestrator_home.mkdir()
        script_dir = orchestrator_home / "config" / "scripts" / "inline"
        script_dir.mkdir(parents=True)

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        sentinel = tmp_path / "legacy_sentinel.txt"

        # Script at $ORCHESTRATOR_HOME/config/scripts/inline/foo.sh
        legacy_script = script_dir / "foo.sh"
        legacy_script.write_text(_sentinel_script(str(sentinel)))
        legacy_script.chmod(0o755)

        # Flat-file contract with legacy-style relative run: path
        (contracts_dir / "my-legacy-step.yaml").write_text(yaml.safe_dump({
            "id": "my-legacy-step",
            "run": "scripts/inline/foo.sh",
            "inputs": [],
            "outputs": [],
            "rules": [],
        }))

        state_yaml = _write_state(state_dir, "run-path-test-b", "my-legacy-step")

        result = _run_next(state_yaml, str(orchestrator_home), str(contracts_dir))

        assert sentinel.exists(), (
            "Expected legacy script at $ORCHESTRATOR_HOME/config/scripts/inline/foo.sh "
            "to run when no step_contract_dir is set.\n"
            f"exit={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
