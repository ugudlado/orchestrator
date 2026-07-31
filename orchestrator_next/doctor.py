"""
orchestrator doctor — structural health check command.

Runs structural and graph health checks and prints a PASS/WARN/FAIL table to stdout.
Exit codes: 0 (all pass or warnings only), 2 (at least one failure).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import namedtuple
from pathlib import Path

import yaml

CheckResult = namedtuple("CheckResult", "name status detail")


def _repo_root_from_env(orch_home: Path) -> Path:
    """Resolve consumer repo root for .orchestrator override checks and the
    onboarding checks (spec/project.yaml, git repo, ticketing).

    orch_home (config_root.parent) is only a safe fallback in a dev checkout
    where config/ lives at the repo root — it is NOT the consumer repo in a
    wheel install with the config bundled fallback (T1), where it resolves
    inside site-packages. Prefer the git toplevel of cwd, then cwd itself,
    before falling back to orch_home.
    """
    for key in ("ORCHESTRATOR_REPO_ROOT", "REPO_ROOT"):
        val = os.environ.get(key)
        if val:
            return Path(val).expanduser().resolve()
    import subprocess
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        top = ""
    if top:
        return Path(top).resolve()
    return orch_home.resolve()


def _normalize_workflow_step_ref(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("id", "")).strip()
    return str(item).strip() if item else ""


def _collect_symlinks(root: Path) -> list[Path]:
    links: list[Path] = []
    if not root.exists():
        return links
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            if path.is_symlink():
                links.append(path)
    return links


# ---------------------------------------------------------------------------
# Symlink validity
# ---------------------------------------------------------------------------

def check_symlinks(repo_root: Path, orch_home: Path) -> CheckResult:
    """WARN/FAIL when symlinks under repo_root or orch_home point at missing targets."""
    stale: list[str] = []
    for root in (repo_root, orch_home):
        for link in _collect_symlinks(root):
            if link.exists():
                continue
            try:
                target = os.readlink(link)
            except OSError as exc:
                target = f"<unreadable: {exc}>"
            stale.append(f"{link} -> {target}")
    if stale:
        return CheckResult("symlinks valid", "WARN", "; ".join(stale))
    return CheckResult("symlinks valid", "PASS", "all symlink targets exist")


# ---------------------------------------------------------------------------
# Onboarding checks (distribution improvements) — what a brand-new repo needs.
# ---------------------------------------------------------------------------

def check_config_source(source: str, config_root: Path) -> CheckResult:
    """Report which of the 3 config_root() tiers resolved: env, vendored, or
    bundled (see paths.config_root_with_source). Informational — never FAILs;
    check_config_root below still enforces the required layout."""
    return CheckResult("config source", "PASS", f"{source}: {config_root}")


def check_project_yaml(repo_root: Path) -> CheckResult:
    """WARN when spec/project.yaml is missing or fails to parse — the
    onboarding gap `orchestrator init` fixes."""
    path = repo_root / "spec" / "project.yaml"
    if not path.is_file():
        return CheckResult(
            "project.yaml", "WARN", f"missing at {path} — run `orchestrator init`"
        )
    try:
        yaml.safe_load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        return CheckResult("project.yaml", "WARN", f"{path} failed to parse: {exc}")
    return CheckResult("project.yaml", "PASS", f"valid: {path}")


def check_commit_verification(repo_root: Path) -> CheckResult:
    """WARN when the repo has no commit-time verification (pre-commit, husky,
    lefthook, biome). Workflow QA gates re-verify, but commit-time hooks catch
    breakage where it happens — agents' commits included."""
    markers = [
        ".pre-commit-config.yaml",
        ".husky",
        "lefthook.yml",
        ".lefthook.yml",
        "biome.json",
        "biome.jsonc",
    ]
    found = [m for m in markers if (repo_root / m).exists()]
    if found:
        return CheckResult("commit hooks", "PASS", f"commit-time verification: {', '.join(found)}")
    return CheckResult(
        "commit hooks", "WARN",
        "no commit-time verification found (pre-commit/husky/lefthook/biome) — "
        "add one so lint/test run at commit instead of only at the QA gate",
    )


def check_git_repo(repo_root: Path) -> CheckResult:
    """WARN when repo_root is not inside a git repository."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("git repo", "WARN", f"git not runnable: {exc}")
    if result.returncode != 0:
        return CheckResult("git repo", "WARN", f"not a git repo: {repo_root}")
    return CheckResult("git repo", "PASS", result.stdout.strip())


def check_ticketing_backend(repo_root: Path) -> CheckResult:
    """WARN when the ticketing backend named in project.yaml is not reachable.

    Skips cleanly (PASS) when project.yaml is absent or names no backend —
    that's check_project_yaml's job to flag, not this one's.
    """
    path = repo_root / "spec" / "project.yaml"
    if not path.is_file():
        return CheckResult("ticketing backend", "PASS", "no project.yaml — skipped")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:  # noqa: BLE001
        return CheckResult("ticketing backend", "PASS", "project.yaml unparsable — skipped")
    backend = str(data.get("ticketing") or "").strip()
    if not backend:
        return CheckResult("ticketing backend", "PASS", "ticketing unset — skipped")
    if backend == "backlog":
        missing = [k for k in ("BACKLOG_URL", "BACKLOG_TOKEN") if not os.environ.get(k)]
        project = os.environ.get("BACKLOG_PROJECT") or os.environ.get("BACKLOG_PROJECT_ID") or data.get("project_id")
        if not project:
            missing.append("BACKLOG_PROJECT(_ID)")
        if missing:
            return CheckResult(
                "ticketing backend", "WARN", f"backlog: missing {', '.join(missing)}"
            )
        return CheckResult("ticketing backend", "PASS", "backlog: env configured")
    if backend == "linear":
        return CheckResult("ticketing backend", "PASS", "linear: reachability checked via MCP, not doctor")
    return CheckResult("ticketing backend", "WARN", f"unknown ticketing backend: {backend}")


# ---------------------------------------------------------------------------
# Config root layout
# ---------------------------------------------------------------------------

def check_config_root(config_root: Path) -> CheckResult:
    """FAIL when the resolved config root is missing or lacks the required layout.

    The config root (ORCHESTRATOR_CONFIG, else <repo>/.orchestrator/config —
    see paths.config_root; explicit, no cwd fallback) must exist and hold
    workflows/ and steps/. This is the prerequisite for every config-content
    check below: if the root is wrong, those checks have nothing to validate.

    D4: models.yaml presence is intentionally NOT required here — a
    workflow-only config root has no models.yaml of its own (routing can come
    from ~/.orchestrator/models.yaml or an env-file layer instead). See
    check_models_layer_present for the loosened WARN-only check.
    """
    if not config_root.is_dir():
        return CheckResult(
            "config root", "FAIL", f"config root does not exist: {config_root}"
        )
    missing = [
        name
        for name in ("workflows", "steps")
        if not (config_root / name).is_dir()
    ]
    if missing:
        return CheckResult(
            "config root",
            "FAIL",
            f"{config_root} missing: {', '.join(missing)}",
        )
    return CheckResult("config root", "PASS", f"valid config root: {config_root}")


def check_models_layer_present(config_root: Path) -> CheckResult:
    """D4: WARN only if NO layer (user home, env file, config root) yields a
    `models:` block — report which layers were checked. Loosened from the old
    hard requirement that config_root/models.yaml itself exist, which broke
    on a workflow-only config root (Phase R) even when the user's
    ~/.orchestrator/models.yaml fully covers routing.
    """
    from orchestrator_next.model_routes import _layer_chain, _models_map  # noqa: SLF001

    routes_yaml = str(config_root / "models.yaml")
    checked: list[str] = []
    for label, path in _layer_chain(routes_yaml):
        checked.append(f"{label}={path or '<unset>'}")
        if _models_map(path):
            return CheckResult(
                "models layer present", "PASS", f"models: found via {label} ({path})"
            )
    return CheckResult(
        "models layer present", "WARN",
        f"no layer defines a models: block — checked: {', '.join(checked)}",
    )


# ---------------------------------------------------------------------------
# Config-folder validation (the 4 portability rules), anchored on config_root().
# These honor ORCHESTRATOR_CONFIG; they do NOT read .orchestrator/ overrides.
# ---------------------------------------------------------------------------

def _iter_config_contracts(config_root: Path) -> dict[str, Path]:
    """Step id -> contract path under <config_root>/steps/ (directory form)."""
    found: dict[str, Path] = {}
    steps_root = config_root / "steps"
    if not steps_root.is_dir():
        return found
    for child in sorted(steps_root.iterdir()):
        if child.is_dir() and (child / "contract.yaml").is_file():
            found[child.name] = child / "contract.yaml"
    return found


def check_workflow_steps_resolve(config_root: Path) -> CheckResult:
    """RULE 1: every step referenced by a workflow has a contract under steps/."""
    contracts = set(_iter_config_contracts(config_root))
    wf_root = config_root / "workflows"
    failures: list[str] = []
    if wf_root.is_dir():
        for wf_path in sorted(wf_root.glob("*.yaml")):
            try:
                data = yaml.safe_load(wf_path.read_text()) or {}
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{wf_path.name}: {exc}")
                continue
            for item in data.get("steps") or []:
                step_id = _normalize_workflow_step_ref(item)
                if step_id and step_id not in contracts:
                    failures.append(f"{wf_path.stem}.yaml → {step_id} (no contract)")
    if failures:
        return CheckResult("workflow steps resolve", "FAIL", "; ".join(failures))
    return CheckResult("workflow steps resolve", "PASS", "all workflow steps have contracts")


def check_step_dispatch_kind(config_root: Path) -> CheckResult:
    """RULE 2: every step contract is dispatchable — declares agent: (spawn)
    or run: (script). Script steps point run: at a script.sh that execs their
    own payload (e.g. a python file)."""
    failures: list[str] = []
    for step_id, path in _iter_config_contracts(config_root).items():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{step_id}: {exc}")
            continue
        if not (data.get("model") or data.get("run")):
            failures.append(f"{step_id}: no model:/run:")
    if failures:
        return CheckResult("step dispatch kind", "FAIL", "; ".join(failures))
    return CheckResult("step dispatch kind", "PASS", "all steps are agent- or script-driven")


def check_subprocesses_available(config_root: Path) -> CheckResult:
    """RULE 3 (WARN): every distinct subprocess: in models.yaml is on PATH.

    WARN, not FAIL: a config is valid even if a backend (e.g. cursor) is not
    installed on this machine — that's machine state, not config integrity.
    """
    import shutil

    models_yaml = config_root / "models.yaml"
    if not models_yaml.is_file():
        return CheckResult("subprocesses available", "WARN", "models.yaml not found")
    try:
        data = yaml.safe_load(models_yaml.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        return CheckResult("subprocesses available", "WARN", f"models.yaml parse: {exc}")
    subs: set[str] = set()
    for entry in (data.get("models") or {}).values():
        candidates = entry if isinstance(entry, list) else [entry]
        for cand in candidates:
            if isinstance(cand, dict) and cand.get("subprocess"):
                subs.add(str(cand["subprocess"]))
    missing = sorted(s for s in subs if not shutil.which(s))
    if missing:
        return CheckResult(
            "subprocesses available", "WARN", f"not on PATH: {', '.join(missing)}"
        )
    return CheckResult(
        "subprocesses available", "PASS", f"all {len(subs)} subprocess backends on PATH"
    )


def check_prompt_optimizer() -> CheckResult:
    """WARN when the optional prompt-optimizer integration is not runnable."""
    import shutil

    configured = os.environ.get("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR", "").strip()
    if not configured:
        return CheckResult(
            "prompt optimizer",
            "WARN",
            "ORCHESTRATOR_PROMPT_OPTIMIZER_DIR is not set",
        )

    optimizer_dir = Path(configured).expanduser()
    if not optimizer_dir.is_dir():
        return CheckResult(
            "prompt optimizer",
            "WARN",
            f"ORCHESTRATOR_PROMPT_OPTIMIZER_DIR is not a directory: {optimizer_dir}",
        )

    uv = shutil.which("uv")
    if not uv:
        return CheckResult("prompt optimizer", "WARN", "uv is not on PATH")

    return CheckResult(
        "prompt optimizer",
        "PASS",
        f"configured at {optimizer_dir.resolve()} (uv: {uv})",
    )


def check_contract_aliases_resolve(config_root: Path) -> CheckResult:
    """D4: every model: alias referenced by an installed step contract must
    resolve to at least one available route (subprocess with a binary on
    PATH) somewhere in the layer chain. This is what makes updating step configs
    and agent config independently safe in practice — a contract that invents an
    alias no layer defines, or a machine missing the binary for an alias a
    contract requires, is caught here instead of at dispatch time (exit 4).
    """
    import shutil

    from orchestrator_next.model_routes import resolve_route, resolve_tool_template

    routes_yaml = str(config_root / "models.yaml")
    aliases: set[str] = set()
    for step_id, path in _iter_config_contracts(config_root).items():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        model = data.get("model")
        if model:
            aliases.add(str(model))

    if not aliases:
        return CheckResult("contract aliases resolve", "PASS", "0 agent-step aliases to check")

    unresolved: list[str] = []
    for alias in sorted(aliases):
        route = resolve_route(alias, routes_yaml)
        subprocess_name = route.get("subprocess") or ""
        if not subprocess_name:
            unresolved.append(f"{alias} (no route in any layer)")
            continue
        binary, _template = resolve_tool_template(subprocess_name, routes_yaml)
        if not shutil.which(binary):
            unresolved.append(
                f"{alias} -> {subprocess_name} ({binary} not on PATH) — reroute via "
                f"~/.orchestrator/models.yaml, e.g. "
                f"models: {{{alias}: {{model_id: <id>, subprocess: claude}}}}"
            )

    if unresolved:
        return CheckResult(
            "contract aliases resolve", "WARN",
            f"{len(unresolved)} alias(es) unresolved: {', '.join(unresolved)}",
        )
    return CheckResult(
        "contract aliases resolve", "PASS", f"all {len(aliases)} contract aliases resolve"
    )


def check_no_silent_fallback(config_root: Path) -> CheckResult:
    """D3 guard rail: WARN whenever any alias is currently resolving to a
    non-first candidate in its fallback chain — a tier is silently running
    on a degraded route and someone should notice."""
    from orchestrator_next.model_routes import resolve_all_with_source

    routes_yaml = str(config_root / "models.yaml")
    resolved = resolve_all_with_source(routes_yaml)
    on_fallback = sorted(
        f"{alias}→candidate#{entry['active_index']} ({entry['subprocess']})"
        for alias, entry in resolved.items()
        if entry.get("is_fallback")
    )
    if on_fallback:
        return CheckResult(
            "no silent fallback", "WARN",
            f"aliases on a fallback candidate: {', '.join(on_fallback)}",
        )
    return CheckResult("no silent fallback", "PASS", "no alias is on a fallback candidate")


def check_model_route_sources(config_root: Path) -> CheckResult:
    """Report per-tier model route provenance (PASS — informational, not integrity)."""
    from orchestrator_next.model_routes import resolve_all_with_source

    routes_yaml = str(config_root / "models.yaml")
    resolved = resolve_all_with_source(routes_yaml)
    if not resolved:
        return CheckResult("model route sources", "PASS", "0 tiers resolved")

    parts = []
    for tier in sorted(resolved):
        src = resolved[tier].get("subprocess_source") or resolved[tier].get("model_id_source") or "?"
        parts.append(f"{tier}←{src}")
    detail = f"{len(resolved)} tiers resolved: {', '.join(parts)}"
    return CheckResult("model route sources", "PASS", detail)


# ---------------------------------------------------------------------------
# run_all + formatting
# ---------------------------------------------------------------------------

def _format_table(results: list) -> str:
    """Render a fixed-width 3-column table: name, status, detail."""
    name_w = max(len(r.name) for r in results)
    lines = []
    for r in results:
        detail = r.detail
        if len(detail) > 120:
            detail = detail[:117] + "..."
        lines.append(f"{r.name:<{name_w}}  {r.status:<4}  {detail}")
    return "\n".join(lines)


def run_all() -> int:
    """Run all checks and return exit code 0 (pass/warn) or 2 (any failure)."""
    from orchestrator_next.paths import ConfigRootError, config_root_with_source

    try:
        config_root, config_source = config_root_with_source()
    except ConfigRootError as exc:
        print(_format_table([CheckResult("config root", "FAIL", str(exc))]))
        return 2
    orch_home = config_root.parent
    repo_root = _repo_root_from_env(orch_home)
    results = [
        # Onboarding checks (distribution improvements) — setup diagnosis first.
        check_config_source(config_source, config_root),
        check_project_yaml(repo_root),
        check_git_repo(repo_root),
        check_commit_verification(repo_root),
        check_ticketing_backend(repo_root),
        # Config-folder validation (the 4 portability rules) — anchored on config_root().
        check_config_root(config_root),
        check_models_layer_present(config_root),     # D4: loosened models.yaml presence (WARN)
        check_workflow_steps_resolve(config_root),  # rule 1
        check_step_dispatch_kind(config_root),      # rule 2
        check_subprocesses_available(config_root),  # rule 3 (WARN)
        check_model_route_sources(config_root),
        check_no_silent_fallback(config_root),      # D3 guard rail (WARN)
        check_contract_aliases_resolve(config_root),  # D4: contract/agent-config safety net (WARN)
        check_prompt_optimizer(),
        check_symlinks(repo_root, orch_home),
    ]
    print(_format_table(results))
    if any(r.status == "FAIL" for r in results):
        print("Check config/steps/<id>/contract.yaml and models.yaml layers.")
        return 2
    return 0


def _doctor_main(argv: list) -> int:
    """Entry point for `orchestrator doctor`. Runs all checks.

    The config root resolves via paths.config_root (ORCHESTRATOR_CONFIG →
    <repo>/.orchestrator/config; explicit, no cwd fallback). A wrong, unset,
    or missing config root surfaces as the `config root` FAIL check, not a
    crash.
    """
    ap = argparse.ArgumentParser(prog="orchestrator doctor")
    ap.parse_args(argv)  # --help handled here; no flags in this iteration
    return run_all()


if __name__ == "__main__":
    raise SystemExit(_doctor_main(sys.argv[1:]))
