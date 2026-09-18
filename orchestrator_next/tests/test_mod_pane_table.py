"""The Mod pane's metrics table (mod/pane.ts) — columns, formats, totals.

Same technique as test_mod_parse_json.py: Node >=22.6's
`--experimental-strip-types` imports the real `pane.ts`, so these assert the
shipped pure functions rather than a Python re-implementation. Only the
row/format helpers are exercised — they take plain data and answer strings,
so no element table and no terminal is needed.
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


# --- fixture ---------------------------------------------------------------
# A three-node run as `orchestrator status --json` reports it after the
# enrichment: one finished node, one running, one not yet started, with the
# running node's cost unpriced so the partial marker has something to mark.
STATUS = {
    "run_id": "0f2c91aa-77",
    "slug": "pane-table",
    "run_status": "active",
    "phase": "build",
    "nodes": [
        {
            "id": "discovery", "phase": "plan", "kind": "judgment",
            "status": "completed", "attempts": 1, "model": "claude-sonnet-5",
            "verdict": "pass", "seconds": 12.0, "input_tokens": 1200,
            "output_tokens": 340, "cache_read_tokens": 12500,
            "cache_write_tokens": 800, "cost_usd": 0.1731, "cost_partial": False,
        },
        {
            "id": "implement", "phase": "build", "kind": "judgment",
            "status": "running", "attempts": 2, "model": "claude-fable-5-1",
            "verdict": "", "seconds": 185.0, "input_tokens": 1_300_000,
            "output_tokens": 9400, "cache_read_tokens": 250,
            "cache_write_tokens": 0, "cost_usd": 0.42, "cost_partial": True,
        },
        {
            "id": "review", "phase": "build", "kind": "judgment",
            "status": "pending", "attempts": 0, "model": "",
            "verdict": "", "seconds": 0.0, "input_tokens": 0,
            "output_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "cost_usd": 0.0, "cost_partial": False,
        },
    ],
    "totals": {
        "seconds": 197.0, "input_tokens": 1_301_200, "output_tokens": 9740,
        "cache_read_tokens": 12750, "cache_write_tokens": 800,
        "cost_usd": 0.5931, "cost_partial": True,
    },
}


def _rows(columns: int | None) -> list[dict]:
    return _call(
        "(m, a) => m.tableRowsOf(a[0].nodes, a[0].totals, a[1])", STATUS, columns
    )


def _lines(columns: int | None) -> list[str]:
    return _call(
        "(m, a) => m.tableRowsOf(a[0].nodes, a[0].totals, a[1]).map(m.rowTextOf)",
        STATUS, columns,
    )


# --- width tiers -------------------------------------------------------
# Keyed to the docked PANE's own body width (`bodyColumns`), not the
# terminal's: a real docked pane runs far narrower than a terminal (a live
# session measured ~50 columns), so the old terminal-width tiers (150/110)
# never fired and the table always fell back to the compact list.
@requires_node
def test_wide_tier_draws_every_column() -> None:
    keys = _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", 120)
    assert keys == [
        "step", "model", "attempts", "verdict", "seconds",
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "cost_usd",
    ]
    assert _call("(m, a) => m.showsCostBar(a[0])", 120) is True


@requires_node
def test_roomy_tier_drops_cache_columns() -> None:
    keys = _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", 90)
    assert keys == [
        "step", "model", "attempts", "verdict", "seconds",
        "input_tokens", "output_tokens", "cost_usd",
    ]
    # The bar rides with the widest tier only.
    assert _call("(m, a) => m.showsCostBar(a[0])", 90) is False


@requires_node
def test_compact_tier_keeps_model_and_out_only() -> None:
    keys = _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", 64)
    assert keys == ["step", "model", "attempts", "seconds", "output_tokens", "cost_usd"]


@requires_node
def test_narrowest_table_tier_is_step_att_time_cost() -> None:
    keys = _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", 40)
    assert keys == ["step", "attempts", "seconds", "cost_usd"]


@requires_node
def test_below_narrow_floor_has_no_table_so_the_pane_falls_back_to_the_list() -> None:
    assert _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", 39) == []
    assert _rows(39) == []


@requires_node
def test_tier_boundaries_are_inclusive() -> None:
    assert len(_call("(m, a) => m.columnsFor(a[0])", 120)) == 10
    assert len(_call("(m, a) => m.columnsFor(a[0])", 119)) == 8
    assert len(_call("(m, a) => m.columnsFor(a[0])", 90)) == 8
    assert len(_call("(m, a) => m.columnsFor(a[0])", 89)) == 6
    assert len(_call("(m, a) => m.columnsFor(a[0])", 64)) == 6
    assert len(_call("(m, a) => m.columnsFor(a[0])", 63)) == 4
    assert len(_call("(m, a) => m.columnsFor(a[0])", 40)) == 4
    assert len(_call("(m, a) => m.columnsFor(a[0])", 39)) == 0


@requires_node
def test_unknown_width_assumes_the_default_body_width() -> None:
    """Before any drawing reports a width, assume a real docked pane's size."""
    default_width = _call("(m, a) => m.DEFAULT_BODY_COLUMNS")
    assert default_width == 48
    assert (
        _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", None)
        == _call("(m, a) => m.columnsFor(a[0]).map(c => c.key)", default_width)
    )


@requires_node
def test_a_docked_render_never_exceeds_its_own_width() -> None:
    """A 60-column pane's table never draws a line wider than 60 cells.

    Live finding: a docked pane is far narrower than the terminal, and a
    long step id at the "roomy" tier (`Model`, `Verdict`, `In` all present)
    can add up to more than 60 cells before this — the row wrapped
    mid-cell on a real terminal instead of clipping the Step id.
    """
    wide_status = dict(STATUS)
    wide_status["nodes"] = [
        {**STATUS["nodes"][0], "id": "a-very-long-step-identifier-that-would-overflow"},
        STATUS["nodes"][1],
        STATUS["nodes"][2],
    ]
    lines = _call(
        "(m, a) => m.tableRowsOf(a[0].nodes, a[0].totals, a[1]).map(m.rowTextOf)",
        wide_status, 60,
    )
    assert lines != []
    for line in lines:
        assert len(line) <= 60, line


@requires_node
def test_ellipsize_marks_a_cut_string_and_leaves_a_short_one_alone() -> None:
    assert _call("(m, a) => m.ellipsize(a[0], a[1])", "implement", 20) == "implement"
    assert _call("(m, a) => m.ellipsize(a[0], a[1])", "implement", 5) == "impl…"
    assert _call("(m, a) => m.ellipsize(a[0], a[1])", "implement", 1) == "…"
    assert _call("(m, a) => m.ellipsize(a[0], a[1])", "implement", 0) == ""


# --- formatting ------------------------------------------------------------
@requires_node
def test_token_formatting() -> None:
    cases = [0, 1, 840, 999, 1000, 1200, 12500, 999_999, 1_300_000, 12_400_000]
    assert _call("(m, a) => a[0].map(m.tokenTextOf)", cases) == [
        "-", "1", "840", "999", "1.0k", "1.2k", "12.5k", "1000k", "1.3M", "12.4M",
    ]


@requires_node
def test_seconds_formatting() -> None:
    cases = [0, 12, 59, 60, 185, 3600, 3870]
    assert _call("(m, a) => a[0].map(m.secondsTextOf)", cases) == [
        "-", "12s", "59s", "1m00s", "3m05s", "1h00m", "1h04m",
    ]


@requires_node
def test_cost_formatting_marks_a_partial_with_a_question_mark() -> None:
    assert _call("(m, a) => m.costTextOf(a[0], a[1])", 0.1731, False) == "$0.1731"
    assert _call("(m, a) => m.costTextOf(a[0], a[1])", 0.42, True) == "$0.4200?"
    assert _call("(m, a) => m.costTextOf(a[0], a[1])", 0, False) == "-"
    # A zero that could not be priced is not the same as a free step.
    assert _call("(m, a) => m.costTextOf(a[0], a[1])", 0, True) == "$0.0000?"


@requires_node
def test_model_is_drawn_without_the_vendor_prefix() -> None:
    assert _call("(m, a) => m.shortModelOf(a[0])", "claude-sonnet-5") == "sonnet-5"
    assert _call("(m, a) => m.shortModelOf(a[0])", "") == ""


@requires_node
def test_cost_bar_scales_to_the_priciest_row_and_never_empties_a_real_cost() -> None:
    width = _call("(m, a) => m.COST_BAR_CELLS")
    assert width == 8
    full = _call("(m, a) => m.costBarOf(a[0], a[1])", 0.42, 0.42)
    assert full == "█" * 8
    # A twentieth of the max still draws something rather than a blank cell.
    tiny = _call("(m, a) => m.costBarOf(a[0], a[1])", 0.02, 0.42)
    assert tiny.strip() != ""
    assert len(tiny) == 8
    # Nothing billed, or nothing to scale against, draws blank — not a full bar.
    assert _call("(m, a) => m.costBarOf(a[0], a[1])", 0, 0.42).strip() == ""
    assert _call("(m, a) => m.costBarOf(a[0], a[1])", 0.5, 0).strip() == ""


# --- rows ------------------------------------------------------------------
@requires_node
def test_rows_are_header_then_nodes_then_totals() -> None:
    rows = _rows(150)
    assert [row["kind"] for row in rows] == [
        "header", "node", "node", "node", "totals"
    ]
    assert [row["key"] for row in rows] == [
        "header", "discovery", "implement", "review", "totals"
    ]


@requires_node
def test_totals_row_carries_the_run_totals_and_the_partial_marker() -> None:
    totals = _rows(150)[-1]
    cells = [cell.strip() for cell in totals["cells"]]
    assert cells[0] == "Totals"
    # Model, attempts and verdict do not sum to anything.
    assert cells[1:4] == ["", "", ""]
    assert cells[4] == "3m17s"
    assert cells[5] == "1.3M"
    assert cells[-1] == "$0.5931?"


@requires_node
def test_every_column_is_padded_to_one_width_so_numbers_line_up() -> None:
    rows = _rows(150)
    for index in range(len(rows[0]["cells"])):
        widths = {len(row["cells"][index]) for row in rows}
        assert len(widths) == 1, f"column {index} ragged: {widths}"


@requires_node
def test_numeric_cells_are_right_aligned_and_the_step_is_left() -> None:
    rows = _rows(150)
    cost = [row["cells"][-1] for row in rows]
    assert all(not cell.endswith(" ") or cell.strip() == "" for cell in cost)
    assert cost[0].endswith("Cost")           # header right-aligned too
    assert rows[1]["cells"][0].startswith("✓ discovery")   # step left-aligned


@requires_node
def test_node_rows_carry_their_status_for_the_highlight_and_the_dim() -> None:
    rows = _rows(150)
    assert [row["status"] for row in rows] == [
        "", "completed", "running", "pending", ""
    ]
    assert _call("(m, a) => m.isRunningStatus(a[0])", "running") is True
    assert _call("(m, a) => m.isPendingStatus(a[0])", "pending") is True
    assert _call("(m, a) => m.isPendingStatus(a[0])", "completed") is False


@requires_node
def test_only_node_rows_get_a_bar_and_only_in_the_wide_tier() -> None:
    wide = _rows(150)
    assert wide[0]["bar"] == "" and wide[-1]["bar"] == ""
    assert wide[2]["bar"].strip() == "█" * 8      # the priciest node
    assert all(row["bar"] == "" for row in _rows(90))


@requires_node
def test_rendered_lines_are_plain_text_with_no_trailing_space() -> None:
    for line in _lines(150):
        assert line == line.rstrip()
        assert "\n" not in line


@requires_node
def test_a_pre_enrichment_status_still_draws_zeros_rather_than_undefined() -> None:
    """An older engine answers nodes with no metrics; the table must not break."""
    bare = {
        "nodes": [{"id": "plan", "phase": "p", "kind": "judgment",
                   "status": "completed", "attempts": 1}],
        "totals": {},
    }
    lines = _call(
        "(m, a) => m.tableRowsOf(a[0].nodes, a[0].totals, a[1]).map(m.rowTextOf)",
        bare, 150,
    )
    assert "undefined" not in "".join(lines)
    assert "NaN" not in "".join(lines)
    assert any("plan" in line for line in lines)


# --- the parked-retry line -------------------------------------------------
# `record.py` writes `needs_you_reason` as "<step_id> abandoned: <detail>"
# and `dispatch.py` falls back to a bare "<step_id> abandoned", so the pane
# must not prefix the step id a second time.
def _retry_text(step_id: str, reason: str) -> str:
    return _call("(m, a) => m.retryTextOf({stepId: a[0], reason: a[1]})",
                 step_id, reason)


@requires_node
def test_a_reason_that_already_names_the_step_is_printed_as_is() -> None:
    """The live bug: "learn abandoned: learn abandoned: …"."""
    reason = "learn abandoned: the subagent ended without a fenced json block"
    assert _retry_text("learn", reason) == reason


@requires_node
def test_a_bare_engine_fallback_reason_is_not_doubled() -> None:
    assert _retry_text("learn", "learn abandoned") == "learn abandoned"


@requires_node
def test_a_rejected_verdict_reason_is_also_left_alone() -> None:
    """`record.py` writes "rejected" for a fail_on verdict, not "abandoned"."""
    reason = "review rejected: verdict=needs_work"
    assert _retry_text("review", reason) == reason


@requires_node
def test_a_reason_that_does_not_name_the_step_is_prefixed() -> None:
    assert (
        _retry_text("learn", "spawn refused: no agent id")
        == "learn abandoned: spawn refused: no agent id"
    )


@requires_node
def test_an_empty_reason_still_says_which_step() -> None:
    assert _retry_text("learn", "") == "learn abandoned"
    assert _retry_text("learn", "   ") == "learn abandoned"


@requires_node
def test_a_step_whose_name_prefixes_another_is_not_confused() -> None:
    """"learn" must not swallow a reason about "learn-more"."""
    assert (
        _retry_text("learn", "learn-more abandoned: nope")
        == "learn abandoned: learn-more abandoned: nope"
    )


@requires_node
def test_the_prompt_line_uses_it_so_the_pane_and_the_helper_cannot_diverge() -> None:
    reason = "learn abandoned: no fenced json block"
    prompt = _call(
        "(m, a) => m.promptTextOf({status: null, phase: 'running', elapsedMs: 0,"
        " gate: null, ask: null, note: '',"
        " retry: {stepId: a[0], reason: a[1]}})",
        "learn", reason,
    )
    assert prompt == reason
