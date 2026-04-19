# Fix inline scripts mktemp TMPDIR (ISSUE-20)

## Idea
Every `mktemp` call in `scripts/inline/*.sh` should use `${TMPDIR:-/tmp}/<name>-XXXXXX` so it honors the sandbox-permitted temp directory. `/var/folders/...` is blocked by Claude Code's sandbox on macOS; `$TMPDIR` is whitelisted.

## Why Now
`scripts/inline/preview-route.sh` fails silently on every workflow run under the default sandbox because it calls unadorned `mktemp`. It's marked non-blocking so runs proceed, but the preview-route step always returns `estimate_unavailable`. Two-line fix; pattern likely exists in other inline scripts.

## Prototype
```diff
- TMPOUT=$(mktemp)
- TMPERR=$(mktemp)
+ TMPOUT=$(mktemp "${TMPDIR:-/tmp}/preview-route-XXXXXX")
+ TMPERR=$(mktemp "${TMPDIR:-/tmp}/preview-route-err-XXXXXX")
```

## Priority
- User value: 3/10 (cosmetic — already non-blocking)
- Strategic fit: 5/10
- Technical leverage: 8/10 (trivial fix, unblocks pre-flight cost estimates)
- Effort: XS
- **Score: 5.3**

## Source
spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-20
