#!/usr/bin/env bash
# Orchestrator pre-commit hook.
# Installed by install.sh as .git/hooks/pre-commit.
# Runs checks against staged files only — skips anything not about to be committed.
set -u

fail=0
red() { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

staged() {
  git diff --cached --name-only --diff-filter=ACMR -- "$@" 2>/dev/null
}

# --- Check 1: YAML validity ---
yaml_files="$(staged '*.yaml' '*.yml')"
if [ -n "$yaml_files" ]; then
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    if ! python3 -c "import sys, yaml; yaml.safe_load(open(sys.argv[1]))" "$f" 2>"${TMPDIR:-/tmp}/yaml_err.$$"; then
      red "✗ Invalid YAML: $f"
      sed 's/^/    /' "${TMPDIR:-/tmp}/yaml_err.$$"
      fail=1
    fi
    rm -f "${TMPDIR:-/tmp}/yaml_err.$$"
  done <<< "$yaml_files"
fi

# --- Check 2: shell script syntax ---
sh_files="$(staged '*.sh')"
if [ -n "$sh_files" ]; then
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    if ! bash -n "$f" 2>"${TMPDIR:-/tmp}/sh_err.$$"; then
      red "✗ Shell syntax error: $f"
      sed 's/^/    /' "${TMPDIR:-/tmp}/sh_err.$$"
      fail=1
    fi
    rm -f "${TMPDIR:-/tmp}/sh_err.$$"
  done <<< "$sh_files"
fi

# --- Check 4: obvious secrets ---
# Patterns that should never appear in staged content.
secret_patterns='(AKIA[0-9A-Z]{16}|sk-[a-zA-Z0-9]{32,}|ghp_[a-zA-Z0-9]{36}|xox[baprs]-[a-zA-Z0-9-]+|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
all_files="$(staged)"
if [ -n "$all_files" ]; then
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    # Skip binaries.
    case "$(file -b --mime "$f" 2>/dev/null)" in
      *charset=binary*) continue ;;
    esac
    if git diff --cached -U0 -- "$f" | grep -E "^\+" | grep -E "$secret_patterns" >/dev/null 2>&1; then
      red "✗ Possible secret in: $f"
      fail=1
    fi
  done <<< "$all_files"
fi

if [ "$fail" -ne 0 ]; then
  echo
  yellow "Pre-commit checks failed. Fix the issues above, or bypass with: git commit --no-verify"
  exit 1
fi

exit 0
