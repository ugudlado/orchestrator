# Vast.ai automation — session lessons (2026-04-26)

End-to-end test of `gpu.sh vast create` against fresh Vast pods. Surfaced 13
bugs across script logic, Vast CLI quirks, and the Vast platform itself.
Spike validation (Cline tool-calling, bench at 65 tok/s) was completed earlier
on a manually-rented pod — that data still stands. This doc covers only the
automation hardening from the same session.

---

## Bug catalog

### Fixed in `vendors/vast.sh` / `lib/gpu-common.sh` / `setup-vllm.sh`

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Pod stuck in `loading` forever, `status_msg: manifest unknown` | Profile carried image tag `vastai/pytorch:2.7.0-py3.11-cuda12.8.1-devel` that doesn't exist on Docker Hub. The agent invented the tag from the spike's "verified working" doc text. | `qwen3.6-27b-4b-vast.env`: use `vastai/pytorch:cuda-12.8.1-auto`. Comment notes how to list real tags. |
| 2 | `wait_for_vast_running` aborted at 5 min while host was still pulling 12 GB image | Timeout too short for fresh hosts | Bumped to 15 min (180 × 5 s) |
| 3 | Cleanup trap never fired when wait functions failed | `wait_for_vast_running` and `wait_for_ssh` used `exit 1`, which doesn't trigger `ERR` traps | Both changed to `return 1`; trap now sees the failure |
| 4 | 5 min wasted polling instances that had already errored at the API level | We only checked `actual_status`, never `status_msg` | `wait_for_vast_running`: early-fail on grep of `manifest unknown\|not found\|error response from daemon\|no space left\|invalid image` |
| 5 | `vastai destroy instance` prompts `[y/N]` and hangs in non-interactive flows | Vast CLI default | All destroy calls now piped `yes \|` |
| 6 | `wait_for_ssh` aborted at 2.5 min while sshd was still binding | Timeout too short for fresh Vast pods (30-90 s after `running` typical) | Bumped to 5 min (60 × 5 s) |
| 7 | Multiple instances leaked after script crashes / `head` SIGPIPE / hooksmith blocks; each billed at $0.83-1.09/hr | Trap was on `ERR INT TERM` only; missed `EXIT`. Also `set -u` made the trap NameError on `$instance_id` after the local var went out of scope. | Switched to idempotent `EXIT` trap with `CREATE_DONE=0` guard. Trap string bakes the literal instance ID at definition time so it survives local scope exit. Five leaks were caused by this; cost ~$1 in burned instances. |
| 8 | New pods refused SSH with "Permission denied (publickey)" even though we had the right private key | `vastai` accounts and instances are separate. Adding a key to the account doesn't auto-attach to instances; new pods don't inherit unless the key is registered before create. | New `ensure_vast_ssh_key()` runs at the top of `vast_create`. Reads `~/.ssh/id_ed25519.pub` (override via `VAST_SSH_PUBKEY`), idempotently registers via `vastai create ssh-key` if not already present. |
| 9 | First few create attempts targeted A100 SXM4 *40GB* offers thinking they were 80GB | Vast UI offer cards don't always show VRAM clearly; CLI's `gpu_ram>=N` filter is broken (silently returns 0 results when 13 match — see search-tool note below) | Profile comment hardened: explicit `gpu_ram>=80000` filter shown, plus instructions to verify before using. New `gpu.sh vast search` subcommand filters in Python, bypassing the broken CLI filter. |
| 10 | Instance created but `intended_status: stopped` forever; never transitioned to `running` | `vastai create instance ... --ssh` provisions but does NOT start. This is undocumented in CLI help. | Added `vastai start instance "$instance_id"` immediately after create, before the wait loop. |
| 12 | scp `setup-vllm.sh` failed with "No such file or directory" on `/workspace/setup-vllm.sh` | The `cuda-12.8.1-auto` image doesn't ship a `/workspace` directory (older Vast templates did) | `vast_run_setup` now `ssh ... mkdir -p /workspace` before scp |
| 13 | Trap fired but threw `instance_id: unbound variable` and didn't destroy | Trap defined as `'... _vast_create_cleanup "$instance_id"'` (single quotes = lazy eval). After `vast_create` returned, the local `instance_id` was unset. Under `set -u` the substitution failed before reaching the destroy call. | Switched to double quotes baking the literal value at definition time: `"... _vast_create_cleanup $instance_id"`. Survives scope exit. |

### Bonus: new `gpu.sh vast search` subcommand

Vast CLI's `--raw` filter syntax silently drops some constraints — `gpu_ram>=80000`
returned 0 results when 13 matches existed in the unfiltered output. Added a
`vast_search()` function that calls `vastai search offers` with only the safe
filters (`num_gpus`, `verified`, `rentable`) and applies VRAM + GPU-name
matching in Python locally. Configurable via `VAST_GPU_NAME` (default
`A100_SXM4`) and `VAST_MIN_VRAM_MB` (default 80000).

Usage:
```sh
gpu.sh vast search                                  # defaults
VAST_GPU_NAME=H100 VAST_MIN_VRAM_MB=80000 gpu.sh vast search
```

### External — Vast platform issue (not fixed)

| # | Symptom |
|---|---|
| 11 | `vastai logs <id>` shows repeating "Error: remote port forwarding failed for listen port <N>" on the proxy. SSH connections hang or close. Affected at least three different SSH endpoints (`ssh3`, `ssh6`, `ssh8`) on three different physical hosts during the same session. Bypassable in theory via direct public-IP TCP exposure (`-e VAST_TCP_PORT_22=22` at create time), but that requires a vast.sh refactor and wasn't pursued tonight. |

This was the dominant failure mode after the script's own bugs were fixed.
The script was reaching scp + nohup phase correctly twice; Vast's proxy just
wouldn't forward connections. Logged for retry on a different day or via
direct-port-mapping refactor.

---

## Time + cost

- 7 instance create attempts
- ~$2 in burned compute
- ~3 hours of iteration
- 12 in-script bugs squashed
- 1 external blocker documented
- 1 new subcommand added
- 0 instance leaks at end of session (after manual cleanup of last one)

---

## Open follow-ups (next session)

1. **Retry end-to-end create** once Vast's SSH proxy recovers. Use `gpu.sh vast search` to pick a fresh 80GB SXM4 offer, then `gpu.sh vast create`. Should now complete cleanly all the way to "Setup launched".

2. **Direct-port refactor** (medium effort, defer until needed) — to bypass Vast's SSH proxy entirely:
   - At create: pass `--env '-e VAST_TCP_PORT_22=22 -e VAST_TCP_PORT_8000=8000'`
   - After running: parse `ports` field from `vastai show instance --raw` to get external port mapping
   - In `wait_for_ssh`: connect to `public_ipaddr:<external_22>` instead of `ssh_host:ssh_port`
   - Pros: avoids bug 11 entirely. Cons: not all hosts allow public-IP exposure; more parsing.

3. **Already-tracked TODOs from spike validation** (separate from this automation work):
   - Polling fix in `setup-vllm.sh:stop_existing_vllm` (B2 architect finding from VAST_SPIKE_TEST_SCENARIOS.md)
   - Portable `today_start` in `compute_spend_today`/`compute_hours_today` in `lib/gpu-common.sh` (C2 long-context retention finding)

4. **Stage 3 of refactor** (still owed): flip default vendor in `gpu.sh` from runpod to vast, update help text, update `H100_A100_FIXES.md` examples.

5. **Fix the broken `setup-vllm.sh:99` pip install** if a future agent run uses a fresh tagless image — already added `--timeout 120 --retries 5` in this session as a defensive measure.

---

## What worked despite all this

- `vendors/runpod.sh` was untouched and `gpu.sh runpod create` continues to work identically to pre-refactor `gpu.sh create`. Stage 1 of the dispatcher refactor was clean.
- `qwen3_coder` parser + Cline tool-calling validated cleanly on the manual pod (A1, A2 PASS).
- 65 tok/s bench reproducible.
- `gpu.sh vast search` works and surfaces the right offers despite Vast CLI's broken filter.
- Trap finally fires on `EXIT` once bug 13 was fixed — the leaks stopped at that point in the session.

---

## Reading order for tomorrow

1. Skim this doc top-to-bottom (~5 min)
2. Run `gpu.sh vast search` to find a current 80GB SXM4 offer
3. Update `VAST_OFFER_ID` in `qwen3.6-27b-4b-vast.env`
4. Run `PROFILE=qwen3.6-27b-4b-vast scripts/gpu.sh vast create` in foreground (no `head` truncation; tee to file if you want to monitor in another pane). Expect 5-8 min for image pull + setup-vllm kickoff.
5. If it lands cleanly: bench, mark task #4 done.
6. If bug 11 (proxy port forwarding) recurs: confirm via `vastai logs <id>`, document, defer to direct-port refactor.
