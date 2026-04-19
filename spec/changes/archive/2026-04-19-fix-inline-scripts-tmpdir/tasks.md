# Tasks — Fix inline scripts to honor TMPDIR for sandbox compatibility

- [x] T-1: Rewrite every `mktemp` call in `scripts/inline/*.sh` to use `mktemp "${TMPDIR:-/tmp}/<name>.XXXXXX"` (3 sites: `capture-test-baseline.sh:41`, `preview-route.sh:24`, `preview-route.sh:25`)
  Verify: `grep -nE 'mktemp([^"]|$)' scripts/inline/*.sh` returns zero matches AND `grep -c 'mktemp "\${TMPDIR:-/tmp}/' scripts/inline/*.sh` sums to 3

- [x] T-2: Smoke-test both affected scripts under the default sandbox
  Verify: `bash scripts/inline/preview-route.sh | tail -1` parses as JSON with a top-level `route_preview` key AND `REPO_ROOT=$(pwd) bash scripts/inline/capture-test-baseline.sh | tail -1` parses as JSON with a top-level `baseline` key; neither produces a sandbox write-permission error on stderr
  depends: T-1

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- Format contract: contracts/artifact-formats.md § Task Format Contract -->
