#!/usr/bin/env bats

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
HELPER="$REPO_ROOT/config/scripts/inline/commit-worktree-learn-updates.sh"

setup() {
  SANDBOX="$BATS_TMPDIR/commit-wt-$$-$RANDOM"
  mkdir -p "$SANDBOX/repo"
  git -C "$SANDBOX/repo" init -q
  git -C "$SANDBOX/repo" config user.email "test@example.com"
  git -C "$SANDBOX/repo" config user.name "Test"
  mkdir -p "$SANDBOX/repo/config/steps"
  echo "x" > "$SANDBOX/repo/config/steps/foo.txt"
  git -C "$SANDBOX/repo" add -A
  git -C "$SANDBOX/repo" commit -m "init" -q
}

teardown() {
  rm -rf "$SANDBOX"
}

@test "clean repo -> no-op" {
  run bash "$HELPER" "$SANDBOX/repo" "orc-999" "feature/orc-999"
  [ "$status" -eq 0 ]
  [ -z "$(git -C "$SANDBOX/repo" status --porcelain)" ]
}

@test "dirty allowed path -> commits and leaves clean" {
  echo "y" >> "$SANDBOX/repo/config/steps/foo.txt"
  run bash "$HELPER" "$SANDBOX/repo" "orc-999" ""
  [ "$status" -eq 0 ]
  [ -z "$(git -C "$SANDBOX/repo" status --porcelain)" ]
  msg="$(git -C "$SANDBOX/repo" log -1 --format=%s)"
  [ "$msg" = "chore(orc-999): learn-cycle rule updates" ]
}

@test "merge path: dirty disallowed path -> refuses to merge (exit 7)" {
  mkdir -p "$SANDBOX/repo/spec/changes/orc-999"
  echo "artifact" > "$SANDBOX/repo/spec/changes/orc-999/tmp.txt"
  run bash "$HELPER" "$SANDBOX/repo" "orc-999" "" --require-clean
  [ "$status" -eq 7 ]
}

@test "standalone path: unrelated WIP tolerated -> commits learn files, exit 0" {
  # Learn target (committed) plus unrelated WIP (left dirty, not swept in).
  echo "y" >> "$SANDBOX/repo/config/steps/foo.txt"
  echo "wip" > "$SANDBOX/repo/engine.py"
  run bash "$HELPER" "$SANDBOX/repo" "orc-999" ""
  [ "$status" -eq 0 ]
  # Learn file committed...
  msg="$(git -C "$SANDBOX/repo" log -1 --format=%s)"
  [ "$msg" = "chore(orc-999): learn-cycle rule updates" ]
  # ...unrelated WIP NOT swept into the commit (still untracked).
  [ -n "$(git -C "$SANDBOX/repo" status --porcelain engine.py)" ]
}

