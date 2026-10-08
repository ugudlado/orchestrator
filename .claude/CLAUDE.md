# Claude Code rules

Claude-specific; shared agent rules live in `AGENTS.md`.

- **Opus runs the session; Fable advises.** The main session runs Opus 5.5 with Fable as advisor (`advisorModel: "fable"` in `.claude/settings.json`). Opus plans, orchestrates, integrates, reviews the final diff, and commits; it consults the advisor before committing to an approach, when an error recurs, and before declaring done. Delegate edits and test runs to `model: "sonnet"` subagents and read-only search/doc lookups to `model: "haiku"` subagents. Subagents inherit the advisor, and every advisor call re-reads that transcript uncached, so subagents consult it only when stuck. (2026-09-02, loop-design session burning Fable tokens with Fable as main model; 2026-10-08, switched to Opus main + Fable advisor.)
- The harness's file memory (`memory/` + MEMORY.md) is for session-bootstrap pointers only. Full memory content belongs in agentmemory.
