"""
Generate plan.yaml from state.yaml + schema + step contracts + project.yaml.

Public API: generate_plan(state_yaml_path: str) -> None
Writes plan.yaml next to state.yaml, applying 5-tier rule merge per rule-merge.md.

Entry point: python -m orchestrator_next.generate_plan <state_yaml_path>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next import parser as _parser


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _orchestrator_home() -> Path:
    """Return ORCHESTRATOR_HOME as a Path, or raise if unset."""
    home = os.environ.get("ORCHESTRATOR_HOME", "")
    if not home:
        raise EnvironmentError("ORCHESTRATOR_HOME is not set")
    return Path(home)


def _load_schema(schema_name: str) -> dict[str, Any]:
    """Load a workflow schema YAML by name."""
    home = _orchestrator_home()
    path = home / "config" / "workflows" / f"{schema_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_include_phase(include_name: str) -> dict[str, Any]:
    """Load a _<name>.yaml include phase file from $ORCHESTRATOR_HOME/config/workflows/."""
    home = _orchestrator_home()
    path = home / "config" / "workflows" / f"{include_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Include phase file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_project(repo_root: str) -> dict[str, Any]:
    """Load spec/project.yaml from repo_root."""
    path = Path(repo_root) / "spec" / "project.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"spec/project.yaml not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_step_contract_raw(step_id: str, state_yaml_path: str) -> dict[str, Any] | None:
    """
    Load raw YAML dict for a step contract.

    Returns None if not found (missing contract → emit step without rules).
    Uses the same search dirs as parser._contract_search_dirs.
    """
    search_dirs = _parser._contract_search_dirs(state_yaml_path)
    for d in search_dirs:
        candidate = os.path.join(d, f"{step_id}.yaml")
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    print(f"WARNING: step contract not found for '{step_id}', skipping rules", file=sys.stderr)
    return None


def _resolve_phases(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return a flat list of fully-resolved phase dicts from a schema.

    Inline-expands `include: _<name>` entries. Phase-less schemas (top-level
    `steps:` with no `phases:`) synthesize a single phase named "main"; the
    rest of the engine treats them identically to legacy multi-phase schemas.
    """
    raw_phases = schema.get("phases")
    if not raw_phases:
        steps = schema.get("steps")
        if not steps:
            return []
        synthetic: dict[str, Any] = {
            "name": "main",
            "goal": schema.get("description", ""),
            "steps": steps,
        }
        for key in ("verify", "verify_when", "outputs", "rules"):
            if key in schema:
                synthetic[key] = schema[key]
        return [synthetic]

    resolved: list[dict[str, Any]] = []
    for phase_entry in raw_phases:
        if "include" in phase_entry:
            include_name = phase_entry["include"]  # e.g. "_complete-phase"
            included = _load_include_phase(include_name)
            resolved.append(included)
        else:
            resolved.append(phase_entry)
    return resolved


def _find_phase_def(phases: list[dict[str, Any]], phase_name: str) -> dict[str, Any]:
    """Return the phase dict for a given phase name, or raise."""
    for p in phases:
        if p.get("name") == phase_name:
            return p
    raise ValueError(f"Phase '{phase_name}' not found in resolved schema phases")


def _step_entry_for_id(phase_def: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    """
    Find the schema step entry matching step_id in a phase definition.

    Step entries may be:
    - Plain string: "design-and-draft-artifacts"
    - String with gate: "explore if discovery"  (strip " if <flag>")
    - Dict: {id: ..., rules_when: ..., extra_rules: ..., repeat_until: ...}

    Returns the dict form {id, rules_when, extra_rules, repeat_until}
    or an empty dict if the step is a plain string match (no injections).
    Returns None if no match found.
    """
    for entry in phase_def.get("steps", []):
        if isinstance(entry, dict):
            if entry.get("id") == step_id:
                return entry
        elif isinstance(entry, str):
            # Strip " if <flag>" suffix
            bare = entry.split(" if ")[0].strip()
            if bare == step_id:
                return {}  # plain string — no injections
    return None


def _evaluate_rules_when(rules_when: dict[str, Any], flags: dict[str, Any]) -> list[str]:
    """
    Evaluate rules_when mapping against resolved flags per rule-merge.md § Rules-When Evaluation.

    keys: "<flag>" → activate if flag truthy
          "not <flag>" → activate if flag falsy/absent

    Returns list of activated plain-string rules.
    """
    result: list[str] = []
    positive_flags: set[str] = set()
    for key, rules in rules_when.items():
        if key.startswith("not "):
            flag_name = key[4:].strip()
            flag_val = flags.get(flag_name, False)
            # Only activate if NOT already activated by positive match
            if not flag_val and flag_name not in positive_flags:
                result.extend(rules if isinstance(rules, list) else [rules])
        else:
            flag_val = flags.get(key, False)
            if flag_val:
                positive_flags.add(key)
                result.extend(rules if isinstance(rules, list) else [rules])
    return result


def _filter_step_rule(rule: str, repo_name: str) -> bool:
    """
    Return True if a plain-string rule should be kept for this repo.

    Rules with <!-- learned: ... repo: X --> metadata are scoped.
    """
    m = re.search(r"<!--\s*learned:.*?repo:\s*(\S+?)[\s>]", rule)
    if m:
        repo_field = m.group(1).rstrip(">").strip()
        return repo_field == "*" or repo_field == repo_name
    # Has learned metadata but no repo field → backward compat, keep
    if "<!-- learned:" in rule and "repo:" not in rule:
        return True
    # No metadata → permanent, keep
    return True


def _merge_rules(
    step_entry: dict[str, Any],
    contract_raw: dict[str, Any] | None,
    phase_def: dict[str, Any],
    schema: dict[str, Any],
    project: dict[str, Any],
    flags: dict[str, Any],
    repo_name: str,
) -> list[str]:
    """
    Apply 5-tier merge per rule-merge.md.

    Returns the merged ordered list of rule strings.
    """
    # ----------- Tier 1: Step entry injections -----------
    rules_when = step_entry.get("rules_when", {}) or {}
    extra_rules = step_entry.get("extra_rules", []) or []
    injected = _evaluate_rules_when(rules_when, flags)
    extra = list(extra_rules) if isinstance(extra_rules, list) else [extra_rules]

    # ----------- Tier 2: Step contract rules (filtered by repo scope) -----------
    contract_rules_raw: list[str] = []
    if contract_raw is not None:
        contract_rules_raw = contract_raw.get("rules", []) or []
    step_rules = [r for r in contract_rules_raw if _filter_step_rule(str(r), repo_name)]

    # ----------- Tier 3: Phase rules (plain strings) -----------
    phase_rules_raw = phase_def.get("rules", []) or []
    phase_rules = [str(r) for r in phase_rules_raw if isinstance(r, str)]

    # ----------- Tiers 4+5: Named rules (schema overrides project, deduped by id) -----------
    # Start with project rules, then override/add schema rules
    named_rules: dict[str, dict[str, Any]] = {}
    for entry in (project.get("rules") or []):
        if isinstance(entry, dict) and "id" in entry:
            named_rules[entry["id"]] = entry

    for entry in (schema.get("rules") or []):
        if isinstance(entry, dict) and "id" in entry:
            # Schema overrides project on same id
            named_rules[entry["id"]] = entry

    # Filter by when-condition against flags
    active_named: list[str] = []
    for entry in named_rules.values():
        when_flag = entry.get("when")
        if when_flag is None:
            # Always active
            active_named.append(str(entry.get("rule", "")))
        elif flags.get(when_flag, False):
            # Flag is truthy → active
            active_named.append(str(entry.get("rule", "")))
        # else: filtered out

    # ----------- Assemble in precedence order (highest first) -----------
    merged: list[str] = []
    merged.extend(injected)     # source 1a
    merged.extend(extra)        # source 1b
    merged.extend(step_rules)   # source 2
    merged.extend(phase_rules)  # source 3
    merged.extend(active_named) # sources 4+5

    return merged


def _build_step_block(
    step_id: str,
    phase_def: dict[str, Any],
    schema: dict[str, Any],
    project: dict[str, Any],
    flags: dict[str, Any],
    repo_name: str,
    state_yaml_path: str,
) -> dict[str, Any]:
    """
    Build the per-step block for plan.yaml.

    Emits: id, agent, goal, inputs, outputs, rules, repeat_until (when set).
    verify is attached by the caller (last step of phase only).
    """
    step_entry = _step_entry_for_id(phase_def, step_id) or {}
    contract_raw = _load_step_contract_raw(step_id, state_yaml_path)

    # Defaults for missing contracts
    agent = "inline"
    inputs: list[str] = []
    outputs: list[str] = []
    if contract_raw is not None:
        agent = contract_raw.get("agent", "inline")
        raw_inputs = contract_raw.get("inputs") or []
        raw_outputs = contract_raw.get("outputs") or []
        inputs = [str(x) for x in raw_inputs]
        outputs = [str(x) for x in raw_outputs]

    rules = _merge_rules(step_entry, contract_raw, phase_def, schema, project, flags, repo_name)
    goal = phase_def.get("goal", "")

    # Build block with explicit key order (alphabetical within step)
    block: dict[str, Any] = {
        "agent": agent,
        "goal": goal,
        "id": step_id,
        "inputs": inputs,
        "outputs": outputs,
        "rules": rules,
    }

    # repeat_until: from schema step entry OR contract, with step entry taking precedence
    repeat_until = step_entry.get("repeat_until") or (contract_raw.get("repeat_until") if contract_raw else None)
    if repeat_until:
        block["repeat_until"] = repeat_until

    return block


def _write_yaml_stable(obj: Any, path: Path) -> None:
    """
    Write obj as YAML with stable, diffable output.

    sort_keys=False preserves insertion order. Dict keys at the step level
    are pre-sorted by the caller (_build_step_block uses alphabetical insertion).
    """
    content = yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True)
    path.write_text(content, encoding="utf-8")


def generate_plan(state_yaml_path: str) -> None:
    """
    Read state.yaml, merge, write plan.yaml next to state.yaml.

    Public API. Returns None. Raises on unrecoverable errors.
    """
    state = _parser.load_state(state_yaml_path)
    schema_name = state.raw.get("schema", "")
    flags: dict[str, Any] = state.raw.get("flags") or {}
    slug = state.raw.get("slug", state.change_id)

    schema = _load_schema(schema_name)
    project = _load_project(state.repo_root)
    repo_name = (project.get("project") or {}).get("name", "") or slug

    # Resolve phases — expand include: _<name> directives inline
    resolved_phases = _resolve_phases(schema)

    phases_out: list[dict[str, Any]] = []
    for phase_name, phase_plan in state.workflow_plan.items():
        if isinstance(phase_plan, dict):
            active_step_ids = list(phase_plan.get("active", []))
        else:
            active_step_ids = []

        try:
            phase_def = _find_phase_def(resolved_phases, phase_name)
        except ValueError as e:
            print(f"WARNING: {e} — skipping phase", file=sys.stderr)
            continue

        steps_out: list[dict[str, Any]] = []
        for step_id in active_step_ids:
            block = _build_step_block(
                step_id=step_id,
                phase_def=phase_def,
                schema=schema,
                project=project,
                flags=flags,
                repo_name=repo_name,
                state_yaml_path=state_yaml_path,
            )
            steps_out.append(block)

        # Attach phase verify block to the last active step of the phase
        verify_block = phase_plan.get("verify") if isinstance(phase_plan, dict) else None
        # If not in workflow_plan, get from schema phase definition
        if verify_block is None:
            # Resolve verify_when flag overrides if present
            base_verify = phase_def.get("verify")
            verify_when = phase_def.get("verify_when", {})
            if base_verify is not None:
                # Apply verify_when overrides: if any flag matches, merge the override
                effective_verify = dict(base_verify)
                for flag_name, override in verify_when.items():
                    if flags.get(flag_name, False):
                        effective_verify.update(override)
                verify_block = effective_verify

        if verify_block and steps_out:
            steps_out[-1]["verify"] = verify_block

        phases_out.append({
            "goal": phase_def.get("goal", ""),
            "name": phase_name,
            "steps": steps_out,
        })

    plan = {
        "feature": slug,
        "phases": phases_out,
        "resolved_flags": flags,
        "schema": schema_name,
    }

    state_dir = Path(state_yaml_path).parent
    output_path = state_dir / "plan.yaml"
    _write_yaml_stable(plan, output_path)
    print(f"plan.yaml written to {output_path}", file=sys.stderr)


def main() -> None:
    """Entry point: python -m orchestrator_next.generate_plan <state_yaml_path>."""
    ap = argparse.ArgumentParser(
        description="Generate plan.yaml from state.yaml",
    )
    ap.add_argument("state_yaml_path", help="Path to state.yaml")
    args = ap.parse_args()
    generate_plan(args.state_yaml_path)


if __name__ == "__main__":
    main()
