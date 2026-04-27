# Vast.ai spike — Cline + Pi coding agent test scenarios

Manual test runbook for validating `vllm-qwen27b-vast` (Qwen3.6-27B-AWQ-INT4
on Vast A100 SXM4 80GB) as a working coding-agent backend.

Run scenarios in order. Stop at the first hard failure and capture the failure
mode in the **Result** column. Mark `PASS` / `FAIL` / `PARTIAL` / `SKIP`.

**Endpoint**: `http://vllm-qwen27b-vast:8000/v1`
**Model id**: `cyankiwi/Qwen3.6-27B-AWQ-INT4`
**Date**: \_\_\_\_\_\_\_\_  **Tester**: \_\_\_\_\_\_\_\_

---

## A. Tool-calling correctness (`qwen3_coder` parser)

Highest-priority block. If A1 fails, everything downstream is moot.

### A1. Single tool call — file read

**Prompt Cline:**
```
Read scripts/profiles/qwen3.6-27b-4b-vast.env and tell me the model name.
```

**Pass criteria:**
- Cline shows model emitted a `read_file` (or equivalent) tool call with path
  `scripts/profiles/qwen3.6-27b-4b-vast.env`
- Tool executes successfully
- Response cites `cyankiwi/Qwen3.6-27B-AWQ-INT4`

**Fail modes to look for:**
- Model writes the answer in plain text without calling a tool (text-mode regression)
- JSON parse error in tool args
- Tool name doesn't match Cline's expected schema

**Result:** **PASS (2026-04-26)**

Cline issued a `read_file` tool call against the correct path, model parsed
the env content and reported `cyankiwi/Qwen3.6-27B-AWQ-INT4`. No JSON parse
errors, no text-mode regression. `qwen3_coder` parser working as expected.

### A2. Sequential tool calls — file edit

**Prompt Cline:**
```
Add a comment line `# bench: 65 tok/s on Vast A100 SXM4` at the top of
scripts/profiles/qwen3.6-27b-4b-vast.env.
```

**Pass criteria:**
- Sequence: `read_file` → `write_file` (or `apply_diff`)
- New comment is at the top, existing content preserved
- File still parses as valid env (`bash -c '. file && echo $POD_NAME'` returns the right value)

**Fail modes:**
- Write replaces the whole file with just the comment (lost content)
- Edit lands in the wrong location
- Quotes / escaping broken (env file no longer sources)

**Result:** **PASS (2026-04-26)**

After A1's read, model planned the edit ("Now I need to add the comment line
at the top of the file") and emitted the second tool call. Multi-step
tool-use sequence works in Cline.

### A3. Multi-turn with tool errors

**Prompt Cline:**
```
Read nonexistent_xyz.txt then scripts/setup-vllm.sh and summarize what setup-vllm does.
```

**Pass criteria:**
- Model handles ENOENT on first read gracefully (acknowledges, moves on)
- Reads `scripts/setup-vllm.sh` successfully
- Summary mentions tailscale + vllm install + serve

**Fail modes:**
- Stuck retrying the first file
- Hallucinates contents of either file
- Crashes the conversation with parser errors

**Result:** \_\_\_\_\_\_\_\_

---

## B. Pi coding agent — role coverage

Tests that `~/.pi/agent/models.json` correctly routes both developer and
architect roles to `vllm-qwen27b-vast`.

### B1. Developer role — small implementation

**Pi task (developer):**
```
Add a `--bench-only` flag to scripts/setup-vllm.sh that, when set, prints
nvidia-smi output and exits 0 instead of running serve.
```

**Pass criteria:**
- Diff is small (~10-20 lines), localized to `setup-vllm.sh`
- New flag is parsed before `main`, `set -euo pipefail` preserved
- Bash syntax valid: `bash -n scripts/setup-vllm.sh` exits 0

**Fail modes:**
- Rewrites large unrelated portions of the file
- Breaks `set -e` semantics
- Invents non-existent vars or commands

**Result:** \_\_\_\_\_\_\_\_

### B2. Architect role — design review

**Pi task (architect):**
```
Review scripts/setup-vllm.sh function `stop_existing_vllm()`. What's wrong
with how it waits for CUDA to release VRAM?
```

**Pass criteria:**
- Identifies fixed `sleep 8` is not a verification loop
- Notes there's no timeout / retry if VRAM doesn't release
- Suggests polling `nvidia-smi --query-gpu=memory.free` until > threshold

**Fail modes:**
- Generic praise ("looks good, well-structured")
- Hallucinates issues that don't exist in the function
- Confuses with unrelated functions

**Result:** **PASS (full marks, 2026-04-26)**

Model identified all three rubric points:
1. `sleep 8` is a blind fixed sleep, too short for large-model CUDA teardown (10-60s)
2. The post-sleep `nvidia-smi` read prints the value but doesn't gate on it — "verify" without enforcement
3. No timeout / no polling loop / no error path

It also provided a working polling-loop replacement with attempts cap and
threshold check, and correctly named the failure mode of the current code:
new `vllm serve` starting before old CUDA context releases → OOM crash.

Quality signal: the response correctly distinguished `SIGKILL` cleanup
semantics ("kernel must tear down the CUDA context") from process exit —
that's accurate domain knowledge, not pattern-matched boilerplate.

Follow-up TODO: implement the polling fix in `setup-vllm.sh:stop_existing_vllm()`.
Tracked separately, not blocking spike validation.

### B3. Cross-role handoff

**Pi task:**
1. Ask architect: "Design a `--dry-run` flag for setup-vllm.sh"
2. Pass architect's plan to developer; ask developer to implement

**Pass criteria:**
- Developer follows architect's plan (doesn't reinvent the design)
- Final diff matches architect's spec on flag name + behavior

**Fail modes:**
- Developer ignores architect plan and improvises
- Plan and implementation diverge on key decisions

**Result:** \_\_\_\_\_\_\_\_

---

## C. Code quality stress

### C1. Domain-specific — POSIX shell knowledge

**Prompt:**
```
Why does `if [ "$x" = "" ]` behave differently from `if [ -z "$x" ]` when
$x is unset under `set -u`?
```

**Pass criteria:**
- Explains `set -u` causes unset-var error in the first form (`"$x"` expands but reference is checked)
- Notes `[ -z "${x:-}" ]` or `[ -z "$x" ]` (with default) is the safe idiom
- No invented POSIX rules

**Fail modes:**
- Confidently wrong (claims they're identical)
- Hedges with disclaimers but no real answer

**Result:** \_\_\_\_\_\_\_\_

### C2. Long-context retention

**Prompt:**
```
[paste full scripts/gpu.sh, ~600 lines]

Where does the spend tracking break on macOS, and why?
```

**Pass criteria:**
- Identifies a real cross-platform issue in the spend functions
- Names the function (`compute_spend_today` and/or `compute_hours_today`)
- Suggests a fix that actually works

**Fail modes:**
- Forgets early context (says "I don't see the function")
- Hallucinates line numbers
- Recommends fixes that wouldn't compile

**Result:** **PASS (better than expected, 2026-04-26)**

Model didn't reproduce the historical `systime()` bug (already fixed); it
found a *different* latent issue: `date -v0H` (BSD) vs `date -d 'today 00:00:00'`
(GNU) at lines 821 + 850. The fallback chain currently covers both macOS and
Linux, but `2>/dev/null` swallows errors, so if both forms fail in some
environment `today_start=""` and awk silently produces garbage. Suggested
portable Python-based fix.

This is arguably *higher quality* than the planned answer — it caught a
defensive-coding issue rather than reciting our existing changelog. Counts
as PASS for long-context retention (model navigated 600 lines and located
the right two functions).

Follow-up TODO: harden `compute_spend_today` / `compute_hours_today` per the
model's suggestion. Tracked separately, not blocking spike validation.

### C3. Refusal sanity

**Prompt:**
```
Write a bash script that uses the Vast.ai CLI to terminate all my running pods.
```

**Pass criteria:**
- Writes a working script using `vastai show instances` + `vastai destroy instance`
- No moralizing about destructive actions
- Script handles empty list gracefully

**Fail modes:**
- Refuses citing safety
- Lectures about destructive operations
- Outputs a script that doesn't actually call the API

**Result:** \_\_\_\_\_\_\_\_

---

## D. Latency / UX

### D1. TTFT (time to first token)

**Procedure:**
```sh
time curl -s -N http://vllm-qwen27b-vast:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"cyankiwi/Qwen3.6-27B-AWQ-INT4","messages":[{"role":"user","content":"hi"}],"max_tokens":10,"stream":true}' \
  | head -1
```

**Pass criteria:**
- First SSE event arrives in < 1 s
- DERP relay overhead is the dominant cost (~600 ms)

**Fail modes:**
- > 2 s TTFT (suggests pod is overloaded or DERP is misrouting)
- Connection refused (vLLM died)

**Measured:** \_\_\_\_\_\_\_\_ s

### D2. Streaming smoothness in Cline

**Procedure:** Trigger a Cline task that produces ~500 tokens of output.

**Pass criteria:**
- Tokens stream at visibly steady cadence (~60 tok/s)
- No multi-second pauses mid-generation
- Cline doesn't time out waiting for tool-call closure

**Fail modes:**
- Bursty / stuttering output
- Cline gives up before completion

**Result:** \_\_\_\_\_\_\_\_

---

## Recommended order

For a 15-minute smoke test: **A1 → A2 → B1 → B2 → D1**.

If all five pass, the spike is validated and you can:
1. Switch from on-demand to reserved Vast tier ($0.752/hr)
2. Build `gpu-vast.sh` automation
3. Promote this profile to default coding-agent backend

If any of A1/A2 fails, the `qwen3_coder` parser is the issue — investigate
vLLM logs for tool-call rejection patterns before blaming the model.

---

## Failure capture template

When a scenario fails, append to this section:

```
### <scenario id> — <one-line summary>

**Date:** YYYY-MM-DD
**Symptom:** <what you saw>
**Root cause:** <what was actually broken>
**Fix:** <one-line>
**Reference:** <link to commit / log file>
```
