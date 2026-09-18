"""The Mod pane's home screen (mod/pane.ts) — relative time, rows, tiers.

Same technique as test_mod_pane_table.py: Node >=22.6's
`--experimental-strip-types` imports the real `pane.ts`, so these assert the
shipped pure functions rather than a Python re-implementation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

MOD_DIR = Path(__file__).resolve().parents[1] / "mod"


def _node_supports_strip_types() -> bool:
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    major, minor = (int(p) for p in out.lstrip("v").split(".")[:2])
    return (major, minor) >= (22, 6)


requires_node = pytest.mark.skipif(
    not _node_supports_strip_types(),
    reason="needs Node >=22.6 for --experimental-strip-types",
)


def _call(expression: str, *args: object) -> object:
    """Evaluates `expression` (an arrow over the module `m` and JSON `a`)."""
    script = (
        'import("./pane.ts").then(m => {'
        "  const a = JSON.parse(process.argv[1]);"
        f"  const r = ({expression})(m, a);"
        "  process.stdout.write(JSON.stringify(r === undefined ? null : r));"
        "});"
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", script, "--", json.dumps(args)],
        cwd=MOD_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


#: A fixed "now" every relative-time case is measured against.
NOW = 1_758_000_000_000  # 2025-09-16T05:20:00Z, in ms
HOUR = 3_600_000
DAY = 24 * HOUR


def _at(ms_ago: int) -> str:
    """An ISO stamp `ms_ago` milliseconds before NOW."""
    import datetime as dt

    return (
        dt.datetime.fromtimestamp((NOW - ms_ago) / 1000, dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --- relative time ---------------------------------------------------------
@requires_node
@pytest.mark.parametrize(
    ("ago_ms", "expected"),
    [
        (5_000, "just now"),
        (3 * 60_000, "3m ago"),
        (59 * 60_000, "59m ago"),
        (2 * HOUR, "2h ago"),
        (23 * HOUR, "23h ago"),
        # A day and a half is "yesterday", not "1d ago": a day count reads as
        # a duration, a name reads as a day.
        (30 * HOUR, "yesterday"),
        (3 * DAY, "3d ago"),
        (6 * DAY, "6d ago"),
        (14 * DAY, "2w ago"),
    ],
)
def test_relative_time_words(ago_ms: int, expected: str) -> None:
    assert _call("(m, a) => m.relativeTimeOf(a[0], a[1])", _at(ago_ms), NOW) == expected


@requires_node
@pytest.mark.parametrize("stamp", [None, "", "not a date"])
def test_relative_time_of_an_unusable_stamp_is_a_dash(stamp: object) -> None:
    """Never `NaN ago`: a row with no stamp still has to draw."""
    assert _call("(m, a) => m.relativeTimeOf(a[0], a[1])", stamp, NOW) == "-"


# --- durations and clocks --------------------------------------------------
@requires_node
def test_run_duration_between_two_stamps() -> None:
    assert _call(
        "(m, a) => m.runDurationOf(a[0], a[1])", _at(2 * HOUR + 1_085_000), _at(2 * HOUR)
    ) == "18m05s"


@requires_node
@pytest.mark.parametrize(
    ("started", "ended"),
    [
        (None, None),
        ("2026-09-18T10:00:00Z", None),
        # An end before its start is nonsense; a wrong duration is worse than
        # none, so it answers `-` rather than a negative span.
        ("2026-09-18T10:00:00Z", "2026-09-18T09:00:00Z"),
    ],
)
def test_run_duration_of_an_unusable_pair_is_a_dash(started: object, ended: object) -> None:
    assert _call("(m, a) => m.runDurationOf(a[0], a[1])", started, ended) == "-"


@requires_node
def test_run_elapsed_counts_up_from_the_start() -> None:
    assert _call("(m, a) => m.runElapsedOf(a[0], a[1])", _at(255_000), NOW) == "4:15"


# --- glyphs and progress ---------------------------------------------------
@requires_node
@pytest.mark.parametrize(
    ("status", "glyph"),
    [
        ("active", "▶"),
        ("running", "▶"),
        ("blocked", "⏸"),
        ("needs_you", "⏸"),
        ("completed", "✓"),
        ("failed", "✗"),
        ("cancelled", "⊘"),
        ("something-new", "◦"),
    ],
)
def test_run_glyphs(status: str, glyph: str) -> None:
    assert _call("(m, a) => m.runGlyphOf(a[0])", status) == glyph


@requires_node
def test_progress_is_a_fraction_and_a_dash_when_the_plan_is_empty() -> None:
    assert _call(
        "(m, a) => m.progressTextOf(a[0])", {"nodes_done": 4, "nodes_total": 10}
    ) == "4/10"
    assert _call("(m, a) => m.progressTextOf(a[0])", {"nodes_total": 0}) == "-"


# --- recipe rows -----------------------------------------------------------
@requires_node
def test_recipe_line_counts_steps_and_gates() -> None:
    row = {"name": "feature", "pack": "workflows", "steps": 17,
           "gates": ["design-signoff", "ship-signoff"]}

    assert _call("(m, a) => m.recipeLineOf(a[0], a[1])", row, 50) == (
        "feature        17 steps · 2 gates"
    )


@requires_node
def test_recipe_line_names_the_gates_only_when_there_is_room() -> None:
    row = {"name": "design", "pack": "workflows", "steps": 10,
           "gates": ["design-signoff"]}
    wide = _call("(m, a) => m.recipeLineOf(a[0], a[1])", row, 100)

    assert "design-signoff" in str(wide)
    assert "design-signoff" not in str(
        _call("(m, a) => m.recipeLineOf(a[0], a[1])", row, 50)
    )


@requires_node
def test_recipe_line_singularises_one_step_and_one_gate() -> None:
    row = {"name": "solo", "pack": "p", "steps": 1, "gates": ["g"]}

    assert _call("(m, a) => m.recipeLineOf(a[0], a[1])", row, 50) == (
        "solo           1 step · 1 gate"
    )


@requires_node
def test_an_unreadable_recipe_says_so_rather_than_drawing_zero_steps() -> None:
    """A row reading "0 steps" looks empty; this one looks broken, which it is."""
    row = {"name": "bad", "pack": "p", "steps": 0, "gates": [],
           "error": "mapping values are not allowed"}
    line = str(_call("(m, a) => m.recipeLineOf(a[0], a[1])", row, 100))

    assert "unreadable" in line
    assert "0 steps" not in line


# --- run rows --------------------------------------------------------------
ONGOING = {
    "run_id": "r1", "slug": "pane-2", "run_status": "active", "recipe": "design",
    "current_step": "design-review", "started_at": None, "ended_at": None,
    "cost_usd": 1.83, "nodes_done": 6, "nodes_total": 10,
}
PAST = {
    "run_id": "r2", "slug": "orc-117", "run_status": "completed", "recipe": "feature",
    "current_step": "learn", "started_at": None, "ended_at": None,
    "cost_usd": 6.02, "nodes_done": 17, "nodes_total": 17,
}


@requires_node
def test_an_ongoing_row_shows_its_current_step_and_a_running_clock() -> None:
    """An ongoing run is described by what it is doing, not by when it ran."""
    row = {**ONGOING, "started_at": _at(255_000)}
    line = str(_call("(m, a) => m.runLineOf(a[0], a[1], a[2])", row, 90, NOW))

    assert line.startswith("▶ pane-2")
    assert "design-review" in line
    assert "4:15" in line
    assert "$1.8300" in line

    # At 50 columns the step name is the cell that gives: the clock and the
    # cost are what the row exists to compare, so they survive intact.
    narrow = str(_call("(m, a) => m.runLineOf(a[0], a[1], a[2])", row, 50, NOW))

    assert narrow.startswith("▶ pane-2")
    assert "design-re" in narrow
    assert "4:15" in narrow
    assert "$1.8300" in narrow


@requires_node
def test_a_past_row_shows_when_it_ran_and_how_long_it_took() -> None:
    """A finished run is described by when it ran, not by where it stopped."""
    # Ended two hours ago, having run for 18m05s before that.
    row = {**PAST, "started_at": _at(2 * HOUR + 1_085_000), "ended_at": _at(2 * HOUR)}
    line = str(_call("(m, a) => m.runLineOf(a[0], a[1], a[2])", row, 50, NOW))

    assert line.startswith("✓ orc-117")
    assert "2h ago" in line
    assert "18m05s" in line
    # The step it stopped at is the ongoing rows' middle, not a past row's.
    assert "learn" not in line


@requires_node
def test_a_partial_cost_is_marked_on_the_row() -> None:
    row = {**ONGOING, "started_at": _at(255_000), "cost_partial": True}

    assert "$1.8300?" in str(
        _call("(m, a) => m.runLineOf(a[0], a[1], a[2])", row, 64, NOW)
    )


@requires_node
def test_progress_rides_with_the_wide_tier_only() -> None:
    assert "6/10" in str(_call("(m, a) => m.runLineOf(a[0], a[1], a[2])", ONGOING, 100, NOW))
    assert "6/10" not in str(_call("(m, a) => m.runLineOf(a[0], a[1], a[2])", ONGOING, 50, NOW))


@requires_node
@pytest.mark.parametrize("width", [50, 64, 90, 100, 120])
def test_a_row_never_exceeds_the_pane_width(width: int) -> None:
    """A row wider than its pane wraps, which breaks every column below it."""
    row = {**ONGOING, "slug": "a-really-quite-long-slug-here",
           "recipe": "an-elaborately-named-recipe",
           "current_step": "a-step-with-a-very-long-identifier"}
    line = str(_call("(m, a) => m.runLineOf(a[0], a[1], a[2])", row, width, NOW))

    assert len(line) <= width


@requires_node
def test_ongoing_statuses() -> None:
    for status in ("active", "running", "blocked", "needs_you"):
        assert _call("(m, a) => m.isOngoingRun(a[0])", {"run_status": status}) is True
    for status in ("completed", "failed", "cancelled"):
        assert _call("(m, a) => m.isOngoingRun(a[0])", {"run_status": status}) is False


# --- the whole home screen -------------------------------------------------
@requires_node
def test_home_rows_are_two_labelled_sections_with_the_ongoing_runs_first() -> None:
    model = {
        "screen": "home",
        "recipes": [{"name": "feature", "pack": "p", "steps": 17, "gates": ["g"]}],
        "runs": [ONGOING, PAST],
        "selectedRun": None, "ownership": "live", "status": None,
        "phase": "running", "elapsedMs": 0, "gate": None, "retry": None,
        "ask": None, "logs": {"selected": None, "log": None, "offset": 0},
        "cwd": "", "note": "",
    }
    rows = _call("(m, a) => m.homeRowsOf(a[0], a[1], a[2])", model, 50, NOW)

    assert isinstance(rows, list)
    kinds = [row["kind"] for row in rows]
    texts = [row["text"] for row in rows]

    assert texts[0] == "Recipes"
    assert "Runs" in texts
    assert kinds[1] == "recipe"
    # Both run rows are pressable, and the ongoing one comes first.
    runs = [row for row in rows if row["kind"] == "run"]
    assert [row["key"] for row in runs] == [
        "orchestrator-run-pane-2", "orchestrator-run-orc-117",
    ]


@requires_node
def test_home_says_so_when_there_is_nothing_to_list() -> None:
    model = {
        "screen": "home", "recipes": [], "runs": [], "selectedRun": None,
        "ownership": "live", "status": None, "phase": "running", "elapsedMs": 0,
        "gate": None, "retry": None, "ask": None,
        "logs": {"selected": None, "log": None, "offset": 0}, "cwd": "", "note": "",
    }
    texts = [
        row["text"]
        for row in _call("(m, a) => m.homeRowsOf(a[0], a[1], a[2])", model, 50, NOW)
    ]

    assert "No recipes found." in texts
    assert "No runs yet." in texts


# --- keys ------------------------------------------------------------------
@requires_node
def test_row_keys_round_trip() -> None:
    """A press reports only its key, so the payload has to survive the trip."""
    assert _call("(m, a) => m.recipeNameOf(m.recipeKeyOf(a[0]))", "feature") == "feature"
    assert _call("(m, a) => m.runRefOf(m.runKeyOf(a[0]))", "orc-1") == "orc-1"
    # A key of the other kind, or none, is not claimed.
    assert _call("(m, a) => m.recipeNameOf(a[0])", "orchestrator-run-x") is None
    assert _call("(m, a) => m.runRefOf(a[0])", "orchestrator-approve") is None


# --- the action row --------------------------------------------------------
def _model(**overrides: object) -> dict:
    base = {
        "screen": "run", "recipes": [], "runs": [], "selectedRun": None,
        "ownership": "live", "status": {"run_id": "r", "slug": "s",
                                        "run_status": "active", "phase": "design",
                                        "nodes": []},
        "phase": "running", "elapsedMs": 0, "gate": None, "retry": None,
        "ask": None, "logs": {"selected": None, "log": None, "offset": 0},
        "cwd": "", "note": "",
    }
    base.update(overrides)
    return base


@requires_node
@pytest.mark.parametrize(
    ("overrides", "labels"),
    [
        ({"screen": "home"}, ["Start run", "Close"]),
        ({"ownership": "past"}, ["Start again", "Home"]),
        # A run another session drives offers nothing that would race it.
        ({"ownership": "elsewhere"}, ["Home"]),
        ({}, ["Cancel", "Home"]),
        ({"phase": "done"}, ["Start another", "Home"]),
        ({"gate": {"stepId": "g", "token": "t"}}, ["Approve", "Cancel"]),
    ],
)
def test_action_rows(overrides: dict, labels: list[str]) -> None:
    row = _call("(m, a) => m.actionRowOf(a[0]).map(b => b.label)", _model(**overrides))

    assert row == labels


@requires_node
def test_a_run_driven_elsewhere_says_so_in_the_footer() -> None:
    """A table that is not advancing must not read as a wedged run."""
    assert "driven elsewhere" in str(
        _call("(m, a) => m.footerTextOf(a[0])", _model(ownership="elsewhere"))
    )
    assert "finished" in str(
        _call("(m, a) => m.footerTextOf(a[0])", _model(ownership="past"))
    )


@requires_node
def test_the_breadcrumb_leads_with_the_way_back() -> None:
    model = _model(
        runs=[{"run_id": "r", "slug": "s", "recipe": "design", "run_status": "active"}],
        elapsedMs=255_000,
    )
    crumb = str(_call("(m, a) => m.breadcrumbOf(a[0])", model))

    assert crumb.startswith("‹ Home")
    assert "design" in crumb
    assert "4:15" in crumb
