# Parallel-Safety: Writing Workflows for `ORCHESTRATOR_MAX_PARALLEL > 1`

This is a guideline, not an engine rule. The engine cannot enforce it — this
document explains why, what the engine does instead, and what's left for
whoever designs the DAG or the task list.

## What the engine already guarantees

Two experiments (8 workers, 8 disjoint files, one worktree) found the actual
failure modes when steps run in parallel, and neither one is "two steps
touched the same source file":

| Shared singleton | Failure without a fix | Fix |
| --- | --- | --- |
| `.git/index` (one per worktree) | `git add`/`commit` don't queue under contention — 7 of 8 concurrent commits failed with `Unable to create .git/index.lock` | `worktree_lock.py`: an fcntl lock held only for the duration of the git call |
| `tasks.yaml` (one per change) | classic read-modify-write race — 7 of 8 concurrent status updates were lost | `record.apply_task_updates()`: the engine, not the agent, owns the read-merge-write, under the same lock |
| `state.yaml` / the state DB | two dispatchers could claim the same ready node | `dispatch_batch()` claims a whole batch in one atomic compare-and-swap; SQLite/Postgres backends use a `version` column, file backend uses a byte-content token |

If you're designing a DAG where independent steps may be dispatched together
(no `depends_on` edge between them, `ORCHESTRATOR_MAX_PARALLEL > 1`), all of
this is handled for you already. You do not need to add your own locking
around git commands or around `tasks.yaml` — using the engine's contract (run
git through `worktree_lock.git_in_worktree`, report `task_updates:` in
COMPLETION instead of writing `tasks.yaml` directly) is enough.

## What the engine cannot guarantee

Two steps with no dependency edge between them, each editing a *different
region of the same physical file* — e.g. both adding an import line to
`__init__.py`, or both appending a route to the same router file.

This is not a singleton problem, so none of the fixes above touch it — and
it's not fixable at dispatch time for a more basic reason than "no static
diff is possible." The engine doesn't mediate file access at all. What it
hands a step is a prompt (the step's `SKILL.md`, filled in with context) sent
to an ACP agent over stdio; what it gets back is a COMPLETION block. The
agent reads and writes files directly, through its own tool calls, in a
process the engine doesn't sit inside of. `files:` is a field the task or
step *declares* — a promise written down before either edit exists — not a
permission the engine grants or a scope it mediates. There's no layer where
the engine could say "this step's tool call is out of scope, deny it," short
of writing a filesystem-level sandbox per step and reconciling it with
whatever the agent's own tools already assume about the working tree — a
different and much larger project than dispatching prompts to agents. So the
engine has no hook to enforce `files:` against even if the disjointness were
statically checkable, and it isn't statically checkable either: two edits to
`__init__.py` don't announce whether they're compatible until they exist,
and by then it's too late to arbitrate — whichever tool call lands last on
disk wins, silently. That's a design problem: it gets solved when the plan
is drawn up, not at dispatch time.

## The rule

**Any two tasks/steps with no `depends_on` path between them (in either
direction, including transitively) must have disjoint `files:` lists.**

"No path between them" is exactly the set that a parallel dispatcher is free
to batch together. If two tasks can be scheduled in the same batch, their
files must not overlap — full stop. If they can't be scheduled together
(one is an ancestor or descendant of the other, however many hops away),
sharing a file is fine, because it can never race.

This extends the existing design step rule ("identify shared-file conflict
risk up front") by making it mechanical: the check is a pure function of two
fields (`files`, `depends_on`) that already exist in `tasks.yaml` and in a
workflow's node list, so it can be — and now is — checked by tooling instead
of eyeballed.

### When two tasks genuinely need to touch the same file

Pick one, in order of preference:

1. **Add a `depends_on` edge**, even an artificial one with no real data
   dependency. Costs a little wall-clock; the correctness win is unconditional
   and free of design effort. This is the right default when in doubt.
2. **Merge the two tasks into one** that owns the whole file. Right when the
   two edits are small and related enough that one task doing both is not a
   scope violation.
3. **Split the file first.** A preceding task extracts or creates the shared
   surface (e.g. adds the export, the type, the route table) in its own
   commit; the two downstream tasks then each own a distinct file and can run
   concurrently for real. Right when the two edits are substantial enough
   that merging them would blur what each task is verifying.

Do not reach for a fourth option — a lock file, a merge step at the join, a
"whoever finishes last wins" convention — inside the workflow itself. That
reintroduces engine-shaped complexity into content the engine deliberately
doesn't manage, and it's exactly the kind of shared-singleton hazard the two
experiments above exist to warn you off.

## Tooling support (advisory, not a gate)

`architect/validate-tasks-yaml.sh` now computes this automatically: it builds
the `depends_on` closure, finds every unordered pair of tasks, and prints a
`Warning (parallel-safety): ...` to stderr for any pair whose `files` overlap.
It does **not** fail the build — exit code is unaffected — because whether an
overlap is a real problem is a call only the designer can make (e.g. two
idempotent appends to the same log file may be intentionally fine). Treat the
warning as "look at this before shipping the plan," not as a lint error to
silence.

The same check generalizes to any DAG with `files`-like metadata and
`depends_on`-like edges — it isn't specific to `tasks.yaml`. If a pack ever
fans `tasks.yaml` entries out as individual engine-dispatched steps (rather
than one `implement` step working through them serially, which is how this
pack does it today), the identical rule and the identical check apply to that
DAG.

## Where this sits relative to code-review

`code-review` already re-reads `tasks.yaml` and can reopen a task via
`reviews[]`. If a reopened task is going to be re-implemented while a sibling
task from the same batch is still in flight, the same disjointness rule
applies to the reopened task's `files:` against every task-in-flight it has
no `depends_on` path to — not just against the plan as it looked when it was
first drawn up.
