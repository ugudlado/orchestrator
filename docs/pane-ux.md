# Pane UX: the home screen and the run view

Why the pane grew a second screen, what that borrows from the workflow UIs
people already know, and what the two screens look like at each width.

## Sources

| UI             | Read                                                                                                                                                                                                          | What it settled                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Temporal Web   | [Web UI](https://docs.temporal.io/web-ui), [redesign](https://temporal.io/blog/the-dark-magic-of-workflow-exploration)                                                                                        | Header metadata block; status by glyph before colour; relative time as a first-class format  |
| GitHub Actions | [run history](https://docs.github.com/en/actions/how-tos/monitor-workflows/viewing-workflow-run-history), [graph](https://docs.github.com/en/actions/how-tos/monitor-workflows/using-the-visualization-graph) | Definitions list beside runs list; status icon left of the name; press a job to get its logs |
| Dagster        | [webserver + UI](https://docs.dagster.io/guides/operate/webserver)                                                                                                                                            | Runs list filterable by status; run detail = timing pane above a log pane                    |
| Prefect        | [docs](https://docs.prefect.io/v3/)                                                                                                                                                                           | Flow runs as the default landing surface                                                     |
| Argo Workflows | [docs](https://argo-workflows.readthedocs.io/en/latest/)                                                                                                                                                      | Named only as "a UI to visualize and manage Workflows"; no usable detail                     |

Two fetches gave less than hoped: GitHub's run-history page documents
navigation rather than the row's fields, and Argo's index does not document
its UI at all. The conventions below rest on the three that did.

## Conventions adopted

Sources are keyed: **A** Actions [runs](https://docs.github.com/en/actions/how-tos/monitor-workflows/viewing-workflow-run-history)
/ [graph](https://docs.github.com/en/actions/how-tos/monitor-workflows/using-the-visualization-graph),
**T** Temporal [Web UI](https://docs.temporal.io/web-ui) /
[redesign](https://temporal.io/blog/the-dark-magic-of-workflow-exploration),
**D** [Dagster](https://docs.dagster.io/guides/operate/webserver).

| #   | Convention                                                                                                                                          | From |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 1   | Definitions and runs live on one screen. Actions puts them side by side; a pane is tall, not wide, so Home stacks them.                             | A    |
| 2   | The status glyph leads the row. A terminal cannot count on colour, so the glyph carries the meaning and colour only reinforces it.                  | A    |
| 3   | Ongoing runs pin to the top. Temporal treats "liveness" as a visual axis; the engine sorts ongoing first so the moving run is never below the fold. | T    |
| 4   | Relative time for the past, a clock for the present. Relative answers "is this recent"; an ongoing run shows elapsed instead.                       | T    |
| 5   | Duration and cost are right-aligned columns. With no Gantt to show the slow step, the columns have to make the expensive row obvious.               | D    |
| 6   | A run detail view is timing above logs. The run view already had this shape; Home only gives it a way back.                                         | D    |
| 7   | Pressing a row drills in. Every row is a Button: a recipe starts a run, a run opens its view, a step opens its log.                                 | A    |
| 8   | A breadcrumb says where you are and gets you back: `‹ Home · slug · recipe · status · elapsed`.                                                     | T    |
| 9   | Actions belong to the state. A finished run gets `[Start again]` (Temporal's "pre-filled values"), never `[Cancel]`.                                | T    |
| 10  | Progress is a fraction, not a bar. `4/10` costs five cells and survives every width.                                                                | —    |

## What live data changed

Rendering the real store found three things the unit tests had not:

- A slug can be a 36-character UUID, so the slug column is capped, not just
  padded — one outlier row must not widen every other one.
- A run left standing overnight clocks `689:36:55`, so the time column is nine
  cells. A clipped clock is worse than a clipped step name.
- Every cell is capped and padded to a fixed width, so the columns line up
  down the list rather than ragging with each row's content.

## Home at 50 columns

Both mockups below are the real `homeRowsOf` output, not hand-drawn.

```text
 Recipes
 feature        17 steps · 2 gates
 design         10 steps · 1 gate
 bugfix         6 steps

 Runs
 ▶ pane-2   design    design-re…      4:15  $1.8300
 ⏸ orc-118  feature   implement      12:04  $4.1000

 ✓ orc-117  feature   1h ago        18m05s  $6.0200
 ✗ orc-116  design    yesterday      4m10s  $0.4100
 ⊘ orc-115  bugfix    3d ago         1m12s  $0.0800

 [Start run]  [Close]
```

At this width the step name is the cell that gives. The clock and the cost are
what the row exists to compare, so they survive intact and the name is cut.

## Home at 100 columns

```text
 Recipes
 feature        17 steps · 2 gates  design-signoff, ship-signoff
 design         10 steps · 1 gate   design-signoff
 bugfix         6 steps

 Runs
 ▶ pane-2       design    design-review           4:15  $1.8300   6/10
 ⏸ orc-118      feature   implement              12:04  $4.1000   9/17

 ✓ orc-117      feature   1h ago                18m05s  $6.0200  17/17
 ✗ orc-116      design    yesterday              4m10s  $0.4100   7/10
 ⊘ orc-115      bugfix    3d ago                 1m12s  $0.0800    2/6

 [Start run]  [Close]
```

The past rows put `when` where the ongoing rows put `current step`: an
ongoing run is described by what it is doing, a finished one by when it ran.

## Run view at 50 columns

```text
 ‹ Home · pane-2 · design · active · 4:15
  Step            Att   Time    Cost
  ✓ explore         1   3m05s   $0.42
  ▶ design          1   1m10s   $0.31
  ◦ design-review   -       -       -
  Totals            2   4m15s   $0.73
 ── design
 now: Bash git status… (12s)
 #1 running · sonnet-5 · 1m10s · $0.31
 [Home]  [Cancel]
```

A past run draws the same table and log panel with `[Start again]` and
`[Home]` in place of the live actions. A run another session drives draws the
read-only view too, refreshed every 15s, under the note `driven elsewhere`.

## States

| State       | Home                                    | Run view                        |
| ----------- | --------------------------------------- | ------------------------------- |
| Nothing yet | Recipes only, `No runs yet.` under Runs | n/a                             |
| Loading     | last good rows stay up                  | last good table stays up        |
| Read failed | last good rows, no blanking             | last good table, no blanking    |
| Too narrow  | rows fall to one line each              | table falls to the compact list |

## Navigation

`screen` is `home` or `run`, with `selectedRun` naming the run the run view
shows. Starting a run from this session switches to `run`; `[Home]` returns.
Bare `/orchestrator` still toggles the pane, and `/orchestrator runs` prints
the same rows as text for a terminal too narrow to open one.

## Stale runs

A run's `run_status` can say `active`/`blocked`/`needs_you` forever: nothing
flips it back once the process driving it is gone, so an earlier session's
abandoned run stayed pinned above today's work on the strength of a status
field nobody was updating. `orchestrator status --json` now demotes a run
like that: `run_status` is untouched, but the engine adds `stale: true` and
`last_activity` (the newest of the state doc's own stamps, its step
history's, and its gates') whenever that activity is older than
`ORCHESTRATOR_STALE_AFTER_HOURS` (default 24). A stale row sorts with the
past section, by `last_activity` descending, rather than with the runs
actually in progress.

The home row draws it with the glyph `⋯`, and its middle column reads `stale`
in place of a current step, with the time column showing how long ago that
was (`relativeTimeOf(last_activity)`) rather than an elapsed clock. Opening a
stale run's view shows the note `no activity since <relative>` above the
action row, and offers `[Cancel]` / `[Home]` — cancel is safe here (it only
marks the run in its state doc), unlike `[Start again]`, which a genuinely
finished run gets instead.

`orchestrator status --json --all` is the escape hatch: every ongoing-status
run stays in the ongoing section (still carrying `stale: true` where it
applies), for a person who wants to see everything the engine still calls
"active" regardless of how long ago that was.
