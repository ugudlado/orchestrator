# Plan: Redis as the state manager — one CLI, repo-owned artifacts

Follow-up to `docs/plan-acp-simplify.md` (complete). Goal state:

- **State** (everything the orchestration needs to resume) lives in the
  RunStore — **Redis, mandatory**, local and cloud alike (a local
  `redis-server` is cheap; the default URL is `redis://localhost:6379`).
  No file-store fallback. Nothing under `<repo>/.orchestrator/sessions/`
  and no `<repo>/.orchestrator/<slug>/*_state.yaml` dirs.
- **Artifacts** (discovery.md, design.md, findings, tasks.yaml…) live in the
  **repo**, at paths the **workflow config decides** — the engine/CLI never
  prescribes an artifact layout; it only passes identity env
  (`CHANGE_ID`, `REPO_ROOT`, worktree/artifact-dir vars) to steps.
  Artifacts are how a run moves forward.
- **No state in the repo, ever.** The durable record of a finished run is
  whatever the workflow's own reporting step emits (workflow-report) —
  reporting is workflow config, not engine behavior. While a run/session
  is alive, its state lives in the store; when it's closed, the store key
  goes away and the report is what remains.
- A step that can't find the inputs it needs **validates that itself**
  (pack convention, not an engine gate) and fails with a missing-inputs
  reason, routing through the normal reset/on_failure machinery — it
  never invents scope.
- **One CLI surface.** `orchestrator <workflow> …` / `--resume` everywhere
  (local, cloud). The `orchestrator acp` stdio server is deleted.
- **One standardized user-input loop.** Any step can pause with
  `await_input` + optional `options`; the engine routes a chosen option
  deterministically (advance or reset_to), and passes freeform text back to
  the step otherwise.

Executor notes: branch off `feat/acp-server`. `pytest orchestrator_next/tests/ -q`
green after every phase. Minimal diffs, no new deps, delete means delete.

---

## Phase 1 — one CLI: flatten the ACP layer into a sessions module

The JSON-RPC/stdio framing exists only for external ACP clients we are
dropping. The CLI already calls handlers in-process.

1. Rename `acp_server.py` → `sessions.py`. Convert the `AcpServer.handle`
   method-dispatch into plain functions with real signatures:
   - `new_session(cwd, schema="") -> session_id`
   - `load_session(session_id) -> dict` (raises `UnknownSessionError`)
   - `prompt_session(session_id, text, on_update=None) -> dict`
   - `close_session(session_id) -> None`
   - `list_sessions() -> list[str]`
     Errors become exceptions (`SessionError(code, message)` replacing
     `AcpRpcError`; keep the numeric codes in the message for test
     continuity). `_notify` becomes a call to the `on_update` callback —
     DELETE `_send`, `_result`, `_error`, `_send_sink`/ContextVar, and
     `AcpServer.invoke`'s sink plumbing.
2. DELETE `main()` (stdin reader thread, JSON parse loop) and the
   `orchestrator acp` verb in `cli.py`. Remove `acp` from usage text.
   Note in `docs/distribution.md`: editor/Hermes integration, if ever
   needed again, is a thin stdio adapter over `sessions.py` — one file,
   re-addable without touching the engine.
3. `acp_client.py` → `session_cli.py`: `start_schema_main` / `resume_main`
   call the new functions directly; delete `_new_server`/`initialize`
   round-trip (no protocol handshake needed in-process).
4. Tests: rewrite `test_acp_server.py` assertions from JSON-RPC envelopes to
   function calls/exceptions; keep every behavior case (unknown session,
   empty prompt, schema routing/ask, restart-restore, list union). Rename
   test files `test_acp_*` → `test_session_*`.

## Phase 2 — Redis mandatory, state fully out of the repo

1. DELETE `FileRunStore` and `default_file_root()`. `open_store()`
   requires Redis: `redis_url()` defaults to `redis://localhost:6379`
   when `REDIS_URL`/`ORCHESTRATOR_ACP_REDIS_URL` are unset; connection
   failure raises with a start hint
   (`brew services start redis` / `docker run -d -p 6379:6379 redis`).
   No silent fallback of any kind. `orchestrator doctor` gains a Redis
   connectivity check (PING) with the same hint. Tests keep using
   `tests/acp_redis_fake.py` — no live server in CI; drop the
   file-backend parametrization from the session-persistence tests.
2. DELETE `session_workspace()` and `_delete_session_artifacts` — the
   engine no longer creates or removes `.orchestrator/sessions/<id>/`.
   Session runs seed with `worktree_path=""`; artifact locations are
   entirely the workflow's decision (steps already receive `CHANGE_ID`,
   `REPO_ROOT`, and the artifact-dir env vars — that env contract is ALL
   the engine provides). Update the research pack's steps to choose their
   own output dir in the pack config; do not encode any path in
   `orchestrator_next/`.
3. Remove `.orchestrator/sessions` handling from `.gitignore` if present;
   ensure `~/.orchestrator/state/` needs no repo ignore.

## Phase 3 — ALL runs live in the RunStore (ticket convergence)

Today ticket runs persist `<repo>/.orchestrator/<slug>/*_state.yaml`;
sessions use the store. Converge: **run_id → state text in RunStore** for
every run; the state file on disk is only ever a per-invocation temp
materialization.

1. New helpers in `run_store.py` (or a small `run_state.py`):
   - `materialize(store, run_id) -> Path` — load text, write temp file,
     rebind `repo_root`/`worktree_path`/`cwd` to this machine (reuse the
     phase-4 rebind logic from the previous plan).
   - `persist(store, run_id, state_path) -> None` — read file, save.
2. `run_cmd` (`run_loop.py`): replace `_seed_state`/`_resolve_active_state`
   file-glob logic with store calls:
   - New run: mint run_id, seed state text, `store.save`, print `run_id=`.
   - Resume: `store.load(first_positional)` (exact, then lowercase) →
     materialize → drive_loop → persist in a `finally`. Lock around the
     whole drive (`store.lock/unlock`) — same busy error as sessions.
   - DELETE `_resolve_active_state` AND `_resolve_archived_state` — a
     resume id not found among live keys is looked up in the Redis
     archive namespace (see 4): found → report "completed (archived)"
     (the `complete` schema may still run teardown off it), else unknown.
3. `drive_loop` keeps operating on a state_yaml_path — no signature change.
   After any step that relocates the path (archive), persist from the new
   path.
4. Archive in Redis, never in the repo: state.yaml is NEVER written into
   the repo. On completion / `orchestrator complete <run_id>` / session
   close, the run's key is **archived, not deleted**: add
   `store.archive(run_id)` — Redis `RENAME` to `orc:acp:archive:<run_id>`
   - `PERSIST` (archived state carries no TTL; cleanup is an explicit
     human action later —
     `redis-cli --scan --pattern 'orc:acp:archive:*' | xargs redis-cli del`
     — document it in `docs/cloud-environment.md`, do NOT build a prune
     command yet). `list_ids()` grows `archived: bool = False`; live
     listings (`session/list`, resume resolution) use live keys only,
     `report --all` reads both. Rework `archive-completed-change` in the
     pack to stop moving state.yaml (it may still relocate artifacts if
     the workflow wants). The workflow's reporting step stays the
     human-readable record; the archived key is the machine-readable one.
5. Retention: live keys keep the refreshed TTL (a run idle past
   `SESSION_TTL` was abandoned); archived keys persist until manual
   cleanup. If a deployment wants queryable run history beyond that,
   that's a store backend choice, not engine logic:
   select the backend by URL scheme (`ORCHESTRATOR_STATE_URL`:
   `redis://…` → RedisRunStore (default), `postgres://…` → a later
   PostgresRunStore (~40 lines: one table `run_id, state_text,
updated_at`, no TTL)). Only implement the URL-scheme dispatch now; the
   Postgres backend is a follow-up file behind the same Protocol.
6. Headless/cloud autocommit: `autocommit_state` currently commits the
   state file into git. With state in the store, restrict autocommit to
   repo artifacts the workflow produced. Update `DRIVE.md` and
   `docs/cloud-environment.md`: cloud driver needs the state URL, resumes
   by run_id, artifacts arrive via git as before.
7. Migration: none needed for real users (runs are short-lived). Delete
   dead glob/seed tests; port seeding tests to store-backed equivalents.
8. `report --all` / `doctor`: enumerate runs via `store.list_ids()` instead
   of scanning `.orchestrator/*/`.

## Phase 4 — steps validate their own inputs (pack convention, zero engine code)

Artifacts move the workflow forward; a resumed run on a machine without
them must fail loudly at the right step. This is NOT an engine gate —
the CLI knows nothing about artifact paths. Steps validate themselves.

1. Engine: **no changes.** The machinery already exists — a step records
   `status: failed` with `outputs.reason` (and optionally
   `outputs.reset_to`), and routing takes over.
2. Pack convention: every step that consumes upstream artifacts gets an
   explicit "## Inputs" section in its SKILL.md (agent steps) or a guard
   at the top of script.sh (script steps): check the files you need first;
   if missing, fail immediately with
   `outputs.reason: "missing inputs: <paths>"` and `reset_to` pointing at
   the step that produces them — never invent scope from the codebase.
   (`load-ticket-context` already follows this pattern with its
   `[TICKET FETCH FAILED]` guard; replicate the idea, not engine code.)
3. Apply to the steps that consume upstream artifacts (design →
   discovery.md, implement → design.md/tasks.yaml, synthesize-findings →
   intake artifact), in the pack only.
4. Tests: pack-level — a mini-workflow test where the consuming step's
   script fails with the missing-inputs reason and the run routes per
   `reset_to`/on_failure. No engine test changes.

## Phase 5 — standardized await_input with options

Today `human-review` hand-rolls interpretation in SKILL prose. Standardize
so ANY step (agent or script) can pause with routable choices.

1. Payload contract (documented in the COMPLETION contract text in
   `run_loop.py`):
   ```yaml
   COMPLETION:
     step_id: human-review
     status: await_input
     outputs:
       ask: "Review passed. Ship it, or send back?"
       options:
         - label: approve # then: advance (default)
         - label: rework implementation
           reset_to: implement
         - label: rework design
           reset_to: design
   ```
   `record` validates: each option needs `label`; `reset_to` (optional)
   must be a node id at-or-before the current step (reuse the
   `_resolve_routing` validation); invalid options → record error 3.
   Persist `{ask, options}` on the state (`awaiting` block) as part of the
   await_input entry.
2. Engine-side resume routing, in `drive_loop`/`prompt_session` when a run
   is awaiting input and user text arrives:
   - Normalize text (strip, lower). Exact match on a label, or the label's
     first word, or a 1-based option number → deterministic action:
     - option without `reset_to` → mark the awaiting step **completed**
       (`outputs.reason = "user selected: <label>"`) and continue the loop;
     - option with `reset_to` → `apply_dag_reset` to that target
       (reason = user selection), continue the loop from there.
   - No match → current behavior: re-run the awaiting step with
     `User direction: <text>` and let the step interpret. This is the
     escape hatch; steps stay in charge of freeform language.
3. CLI surface: on pause, print the ask and a numbered option list;
   resume hint shows `orchestrator --resume <id> "approve"` (sessions) or
   `orchestrator <schema> <run_id> "approve"` (runs). `on_update` streams
   the same text for any future UI.
4. Simplify `human-review/SKILL.md`: emit the options block; delete the
   prose tables that taught the agent to interpret "ship/LGTM/merge" —
   deterministic labels handle the common case, freeform falls through to
   the agent as before.
5. Tests: option → advance; option with reset_to → dag reset applied;
   number selection; unmatched text → step re-runs with User direction;
   invalid option target rejected at record time.

---

## Out of scope

- Implementing the PostgresRunStore backend (only the URL-scheme dispatch
  lands now; the backend is a later ~40-line file behind the Protocol).
- Any engine-side artifact path convention — artifact layout, archiving,
  and reporting are workflow-pack decisions.
- Artifact replication/object store — artifacts are git-versioned repo
  files by design; cloud gets them via git.
- Auto-spawning a `redis-server` process from the CLI — the error hint
  tells the user how to start one; process management is theirs.
- Re-adding an ACP/stdio adapter.
- Web/TUI for the options loop — CLI text only.

## Done criteria

- `pytest orchestrator_next/tests/ -q` green.
- `grep -rn "acp" orchestrator_next/*.py` → only comments/docs mentioning
  the removed mode (no live code paths); `orchestrator acp` prints usage
  error.
- No repo writes outside workflow-chosen artifact paths: run a workflow
  end-to-end, then `git status` shows only artifacts the pack's steps
  wrote; `<repo>/.orchestrator/` contains packs only — no state files, no
  sessions dir.
- Redis down → every run/resume fails fast with the start hint;
  `orchestrator doctor` reports the failing PING.
- With local Redis: full run + `--resume` work;
  `redis-cli --scan --pattern 'orc:acp:session:*'` shows live runs only,
  and a completed run appears under `orc:acp:archive:<run_id>` with
  `TTL` = -1 (persisted). `--resume` of an archived run id says
  "completed (archived)" instead of unknown-session.
  `grep -rn "FileRunStore" orchestrator_next/` → no hits.
- Missing-inputs: delete `design.md` mid-run, resume → the consuming
  step itself fails with `missing inputs` and routes via its
  `reset_to`/on_failure (pack behavior, no engine gate).
- Options: `--resume <id> "2"` picks option 2 deterministically.
