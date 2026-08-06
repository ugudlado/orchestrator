# Plan: ACP/session simplification + local↔cloud hardening

Executor notes: work on branch `feat/acp-server`. Run
`pytest orchestrator_next/tests/ -q` after every phase — all green before
moving on. Minimal diffs; no new dependencies. When a phase says DELETE,
delete — do not keep a deprecated shim.

Files: `orchestrator_next/acp_server.py`, `acp_client.py`, `run_loop.py`,
`record.py`, `reset_step.py`, `cli.py`, `paths.py`, tests under
`orchestrator_next/tests/`.

---

## Phase 1 — pure cuts (no behavior redesign)

1. **Unconditional flush** — `acp_server.py:_send`: replace the
   "flush only on result/error" branch with `sys.stdout.flush()` always.
   Update the docstring.

2. **Delete `_route_schema` prefix heuristics** — `acp_server.py:571-579`:
   keep first-word match and the `schema:` / `workflow:` prefixes only;
   delete the `"run the"` / `"use the"` scanning loop and the 4-token walk.
   Adjust any test asserting those phrases (they should now route to
   ask-schema).

3. **Delete `_schemas_cache`** — `acp_server.py:534-553`:
   `_available_schemas` calls `list_workflows` directly each time (it is a
   glob; cheap). Keep the `["research"]` fallback on exception. Remove cache
   dict and key logic.

4. **Kill `route_to` alias** — `record.py:261`: read `outputs.reset_to`
   only. Remove `route_to` from code, tests, and any SKILL.md mention
   (grep `.orchestrator/` and `skills/` for `route_to`).

5. **Single state read in paused branch** — `run_loop.py:780-802`
   (`run_loop`): the LOOP_PAUSED handling parses the state file twice
   (`raw`, `raw2`). Merge into one `yaml.safe_load` inside one try/except;
   derive `ticket`, `ask`, and `schema` from that single dict.

6. **`reset_step.py` write path** — delete the read-back "corruption guard"
   (lines 98-108). Replace with atomic write: dump to
   `path.with_suffix(".tmp")`, then `os.replace(tmp, path)`.

7. **Replace `_unlock_failed_for_retry` with `apply_dag_reset`** —
   `acp_server.py:200-227`: locate the failed step exactly as today
   (last `status: failed` in step_history, else `next_step`), then call
   `reset_step.apply_dag_reset(raw, phase, failed_step, keep_history_for=failed_step)`,
   set `raw["status"]="active"`, write. Delete the bespoke
   `readiness.mark_node_status` walker. Keep the function name and return
   value (step_id or None) so `run_workflow` is untouched.

Verify: full pytest, plus manually
`python -m pytest orchestrator_next/tests/test_acp_server.py orchestrator_next/tests/test_acp_session_redis.py -q`.

---

## Phase 2 — session-ness comes from workflow config, not the engine

Problem: `SESSION_SCHEMAS = frozenset({"research"})` hard-codes a pack
workflow name in the engine; `acp_client.is_session_schema` couples the CLI
to it.

1. Add optional top-level key `mode: session` to workflow YAML
   (`.orchestrator/workflows/workflows/research.yaml` gets `mode: session`).
2. New helper in `paths.py` (or wherever `list_workflows` lives):
   `workflow_mode(name, repo_root) -> str` returning `"session"` or
   `"ticket"` (default) by reading the resolved workflow YAML's `mode` key.
3. `acp_client.is_session_schema(token)` resolves via that helper instead of
   the frozenset. `acp_server` uses it wherever `SESSION_SCHEMAS` was used.
4. DELETE `SESSION_SCHEMAS`.
5. Tests: one new test — a temp pack with `mode: session` routes through the
   session path; a workflow without it does not. Update imports in existing
   tests.

---

## Phase 3 — one RunStore, two backends (this erases the temp-file dance)

Problem: ticket runs persist state.yaml on disk; sessions smuggle state.yaml
_content_ through Redis via temp files (`_LIVE_STATE_KEY`,
`_snapshot_state_to_session`, rematerialize in `run_workflow`).

1. New module `orchestrator_next/run_store.py` (~60 lines total):

   ```python
   class RunStore(Protocol):
       def load(self, run_id: str) -> str | None: ...   # state.yaml text
       def save(self, run_id: str, text: str) -> None: ...
       def delete(self, run_id: str) -> None: ...
       def list_ids(self) -> list[str]: ...
   ```

   - `FileRunStore(root: Path)` — one file per run under
     `<root>/<run_id>.yaml`. Default root:
     `<repo>/.orchestrator/sessions/_state/`.
   - `RedisRunStore(client)` — key `orc:acp:session:<id>`, value = payload
     JSON exactly as `_save_session` writes today.
   - Factory `open_store() -> RunStore`: Redis when `redis_url()` is set and
     importable, else file store. `require_redis` semantics move here: a
     session run works locally with NO Redis (file backend); Redis is only
     required when `REDIS_URL` is set but unusable → raise.

2. Rewrite `_save_session` / `_load_session` / `_delete_session_store` /
   `_persisted_session_ids` as thin calls into the store. The payload shape
   (`{cwd, schema, workflow}`) stays identical so `test_acp_session_redis.py`
   still passes against the fake.
3. Remove the `require_redis()` guards in `session/new` / `session/load` /
   `session/prompt` (the factory handles it). Keep `RedisRequiredError` for
   the misconfigured-Redis case only.
4. Do NOT refactor ticket-run persistence in this phase — file-backed ticket
   state stays as-is. The store unifies _session_ persistence; converging
   ticket runs onto RunStore is a later branch.
5. Tests: parametrize the session-persistence tests over both backends
   (file store via tmp_path; Redis via the existing `acp_redis_fake`).

---

## Phase 4 — cloud-safety patches

1. **Session lock (no concurrent resumes)** — in `session/prompt`, before
   running the workflow: acquire `orc:acp:lock:<session_id>` with
   `SET NX EX 900` (Redis backend) or `flock` on the state file (file
   backend); add `lock(run_id)`/`unlock(run_id)` to RunStore. If held,
   return RPC error `-32012 "session busy — another process is running it"`.
   Release in a `finally`. Test: fake-redis test where a second prompt while
   locked errors with -32012.
2. **Rebind machine paths on load** — `_load_session`: the stored `cwd` is a
   hint. If it doesn't exist on this machine, fall back to `os.getcwd()`.
   Same for `repo_root`/`worktree_path` inside the rematerialized state
   text: after writing the temp state file, patch those two keys to the
   resuming machine's repo_root/workspace before `drive_loop` runs. Test:
   store a session whose cwd is `/nonexistent/...`, load on tmp repo, prompt
   succeeds.
3. **TTL** — `RedisRunStore.save` uses `client.set(key, val, ex=TTL)`;
   `TTL = int(os.environ.get("ORCHESTRATOR_ACP_SESSION_TTL", 14*86400))`.
   Every save refreshes it. File backend: no TTL (local disk is the user's).
4. **Outage ≠ unknown session** — `_load_session` currently swallows all
   exceptions → "unknown session". Catch only `json.JSONDecodeError` /
   missing-key as None; let connection errors surface as RPC -32010 with the
   real message. Test: fake client whose `get` raises `ConnectionError` →
   session/load returns -32010, not -32002.

---

## Phase 5 — CLI resume grammar (small)

Keep `orchestrator --resume <session_id> ["text"]` as the only session
resume spelling, but make `orchestrator research <session_id> "text"`
(first positional parses as a UUID of an existing session) print a one-line
redirect hint instead of starting a new run with the UUID as topic.
Cheapest form: in `start_schema_main`, if the prompt is a bare UUID that
`store.load` finds, print `use: orchestrator --resume <id>` and exit 7.

---

## Out of scope (do not do)

- Converging ticket-run persistence onto RunStore.
- Artifact replication to Redis/object store — document in
  `docs/cloud-environment.md` that session artifacts are per-machine
  ephemeral; the result text streams back through ACP.
- Any new dependency, any async, any ORM.

## Done criteria

- `pytest orchestrator_next/tests/ -q` green.
- `grep -rn "SESSION_SCHEMAS\|route_to\|_schemas_cache" orchestrator_next/` → no hits.
- Local smoke without Redis: `unset REDIS_URL; orchestrator research "test topic"`
  starts (file backend), `orchestrator --resume <id>` resumes.
- With Redis up: same two commands against Redis; key has a TTL
  (`redis-cli TTL orc:acp:session:<id>` > 0).
