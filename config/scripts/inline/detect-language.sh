#!/usr/bin/env bash
# detect-language.sh — Detect project language(s), package manager, and web/
# backend framework presence from root indicator files.
#
# Pure file-presence detection — never guesses. If no indicator file is
# found, exits non-zero so the driver surfaces it to the user (the contract
# forbids a silent default; an inline script cannot hold a dialogue).
#
# Idempotent: read-only, derives outputs every run.
#
# Emits a single JSON object on stdout (captured by `orchestrator next`):
#   {languages: [...], package_manager: str, web_project: bool, backend_project: bool}
#
# Env (from dispatch):
#   ORCHESTRATOR_REPO_ROOT  — absolute path to the project root
#   REPO_ROOT               — fallback

set -euo pipefail

REPO="${ORCHESTRATOR_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}}"

if [ -z "$REPO" ]; then
  echo "[detect-language] error: ORCHESTRATOR_REPO_ROOT is not set and git rev-parse failed" >&2
  exit 1
fi

python3 - "$REPO" <<'PY'
import json, os, sys

repo = sys.argv[1]


def has(*names):
    return any(os.path.exists(os.path.join(repo, n)) for n in names)


def pkg_json_text():
    p = os.path.join(repo, "package.json")
    if not os.path.exists(p):
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


languages = []
package_manager = None
web_project = False
backend_project = False

# --- Node / TypeScript ---------------------------------------------------
if has("package.json"):
    txt = pkg_json_text()
    is_ts = has("tsconfig.json") or '"typescript"' in txt
    languages.append("node-ts" if is_ts else "node")

    WEB = ("next", "vite", "react", "astro", "nuxt", "svelte", "angular",
           "remix", "solid")
    BACKEND = ("express", "fastify", "hapi", "koa", "nestjs")
    web_project = any(f'"{w}"' in txt for w in WEB)
    backend_project = any(f'"{b}"' in txt for b in BACKEND)

    if has("pnpm-lock.yaml"):
        package_manager = "pnpm"
    elif has("yarn.lock"):
        package_manager = "yarn"
    elif has("package-lock.json"):
        package_manager = "npm"
    else:
        package_manager = "pnpm"  # contract default for Node

# --- Python --------------------------------------------------------------
if has("pyproject.toml", "setup.py", "requirements.txt"):
    languages.append("python")
    pyproject = ""
    pp = os.path.join(repo, "pyproject.toml")
    if os.path.exists(pp):
        try:
            with open(pp, encoding="utf-8") as f:
                pyproject = f.read()
        except OSError:
            pyproject = ""
    if has("uv.lock") or "[tool.uv]" in pyproject:
        py_pm = "uv"
    elif "[tool.poetry]" in pyproject:
        py_pm = "poetry"
    else:
        py_pm = "pip"
    # Node PM (if any) takes the package_manager slot; else Python's.
    if package_manager is None:
        package_manager = py_pm

# --- Rust / Go -----------------------------------------------------------
if has("Cargo.toml"):
    languages.append("rust")
    package_manager = package_manager or "cargo"
if has("go.mod"):
    languages.append("go")
    package_manager = package_manager or "go"

if not languages:
    sys.stderr.write(
        "[detect-language] no language indicator files found at "
        f"{repo} (looked for package.json, pyproject.toml/setup.py/"
        "requirements.txt, Cargo.toml, go.mod). Ask the user what kind "
        "of project this is.\n"
    )
    sys.exit(2)

print("[bootstrap] Detected: %s | PM: %s | Web: %s"
      % (", ".join(languages), package_manager,
         "yes" if web_project else "no"), file=sys.stderr)

print(json.dumps({
    "languages": languages,
    "package_manager": package_manager,
    "web_project": web_project,
    "backend_project": backend_project,
}))
PY
