"""The Mod pane's per-step log panel (mod/pane.ts) — selection and formatting.

Same technique as test_mod_pane_table.py: Node >=22.6's
`--experimental-strip-types` imports the real `pane.ts`, so these assert the
shipped pure functions rather than a Python re-implementation. Only the
formatting and selection helpers are exercised — they take plain data and
answer strings, so no element table and no terminal is needed.

What is NOT covered here, because it needs a real terminal: the focus ring
landing on a step row, Enter pressing it, and the wheel reaching `ui.scroll`.
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


# --- fixtures --------------------------------------------------------------
# Nodes as `status --json` reports them: one finished, one running, one not
# started. Selection follows the running one until a person picks another.
NODES = [
    {"id": "explore", "phase": "main", "kind": "judgment",
     "status": "completed", "attempts": 1},
    {"id": "implement", "phase": "main", "kind": "judgment",
     "status": "running", "attempts": 2},
    {"id": "review", "phase": "main", "kind": "judgment",
     "status": "pending", "attempts": 0},
]

# Two attempts of one judgment step, newest first, shaped exactly as
# record.py `_build_history_entry` writes them into `step_history`.
ATTEMPTS = [
    {
        "step_id": "implement", "phase": "main", "status": "completed",
        "agent": "developer", "attempt": 2,
        "started_at": "2026-06-02T16:19:12Z", "ended_at": "2026-06-02T16:22:17Z",
        "usage": {"model": "claude-sonnet-5", "duration_ms": 185_000,
                  "cost_usd": 0.1731},
        "evidence": {"outputs": {"verdict": "pass",
                                 "design_result": "design.md"}},
        "artifacts": ["/repo/design.md"],
    },
    {
        "step_id": "implement", "phase": "main", "status": "abandoned",
        "agent": "developer", "attempt": 1,
        "started_at": "2026-06-02T16:10:00Z", "ended_at": "2026-06-02T16:10:42Z",
        "usage": {"model": "claude-fable-5-1", "duration_ms": 42_000,
                  "cost_usd": 0.0, "cost_partial": True},
        "evidence": {"summary": "tool crash"},
    },
]

# An inline script step: no model, no usage, a summary and plain outputs.
SCRIPT_ATTEMPT = {
    "step_id": "create-worktree", "phase": "main", "status": "completed",
    "agent": None, "attempt": 1,
    "started_at": "2026-06-02T16:16:04Z", "ended_at": "2026-06-02T16:16:04Z",
    "usage": {},
    "evidence": {"outputs": {"created": True, "branch": "feature/orc-74"},
                 "summary": "inline script completed"},
}


# --- selection -------------------------------------------------------------
@requires_node
def test_selection_follows_the_running_step() -> None:
    """Nothing picked: the panel shows whatever is running right now."""
    assert _call("(m, a) => m.selectedStepOf(a[0], a[1])", NODES, None) == "implement"


@requires_node
def test_a_persons_selection_wins_over_the_running_step() -> None:
    assert _call("(m, a) => m.selectedStepOf(a[0], a[1])", NODES, "explore") == "explore"


@requires_node
def test_selection_of_an_unknown_step_falls_back_to_running() -> None:
    """A step that left the plan (a re-seeded run) must not blank the panel."""
    assert _call("(m, a) => m.selectedStepOf(a[0], a[1])", NODES, "gone") == "implement"


@requires_node
def test_with_nothing_running_the_last_touched_step_is_shown() -> None:
    done = [
        {"id": "explore", "status": "completed", "phase": "main",
         "kind": "judgment", "attempts": 1},
        {"id": "implement", "status": "completed", "phase": "main",
         "kind": "judgment", "attempts": 1},
        {"id": "review", "status": "pending", "phase": "main",
         "kind": "judgment", "attempts": 0},
    ]
    assert _call("(m, a) => m.selectedStepOf(a[0], a[1])", done, None) == "implement"


@requires_node
def test_step_row_keys_round_trip() -> None:
    key = _call("(m, a) => m.stepKeyOf(a[0])", "implement")
    assert key == "orchestrator-step-implement"
    assert _call("(m, a) => m.stepIdOf(a[0])", key) == "implement"
    # An action Button's key is not a step's, so a press is never misrouted.
    assert _call("(m, a) => m.stepIdOf(a[0])", "orchestrator-approve") is None


# --- the attempt line ------------------------------------------------------
@requires_node
def test_attempt_line_carries_status_model_time_cost_and_verdict() -> None:
    line = _call("(m, a) => m.attemptLineOf(a[0])", ATTEMPTS[0])
    assert line == "#2 completed · sonnet-5 · 3m05s · $0.1731 · pass"


@requires_node
def test_an_unpriced_attempt_marks_its_cost_rather_than_reading_free() -> None:
    line = _call("(m, a) => m.attemptLineOf(a[0])", ATTEMPTS[1])
    assert line == "#1 abandoned · fable-5-1 · 42s · $0.0000? · tool crash"


@requires_node
def test_a_script_attempt_drops_the_segments_it_never_recorded() -> None:
    """No model and no usage: the line must not pad with `-` cells."""
    line = _call("(m, a) => m.attemptLineOf(a[0])", SCRIPT_ATTEMPT)
    assert line == "#1 completed · inline script completed"


@requires_node
def test_duration_falls_back_to_the_two_timestamps() -> None:
    """An entry written before record.py derived duration_ms still shows time."""
    entry = {**ATTEMPTS[0], "usage": {"model": "claude-sonnet-5"}}
    line = _call("(m, a) => m.attemptLineOf(a[0])", entry)
    assert "3m05s" in line


# --- outputs and artifacts -------------------------------------------------
@requires_node
def test_outputs_skip_the_values_that_are_artifact_paths() -> None:
    """An artifact gets its own `→` line; printing its path twice is noise."""
    entry = {**ATTEMPTS[0],
             "evidence": {"outputs": {"verdict": "pass",
                                      "design_result": "/repo/design.md"}}}
    assert _call("(m, a) => m.outputLinesOf(a[0])", entry) == ["verdict: pass"]


@requires_node
def test_a_nested_output_is_drawn_as_compact_json() -> None:
    entry = {"evidence": {"outputs": {"state_patch": {"branch": "feature/x"}}}}
    assert _call("(m, a) => m.outputLinesOf(a[0])", entry) == [
        'state_patch: {"branch":"feature/x"}'
    ]


@requires_node
def test_artifact_lines_are_relative_to_the_runs_directory() -> None:
    lines = _call("(m, a) => m.artifactLinesOf(a[0], a[1])", ATTEMPTS[0], "/repo")
    assert lines == ["→ design.md"]


@requires_node
def test_an_artifact_outside_the_run_keeps_its_full_path() -> None:
    lines = _call("(m, a) => m.artifactLinesOf(a[0], a[1])", ATTEMPTS[0], "/other")
    assert lines == ["→ /repo/design.md"]


# --- the answer tail -------------------------------------------------------
@requires_node
def test_answer_tail_keeps_the_last_lines_and_drops_the_json_block() -> None:
    """The fenced block is the machine's copy of the outputs listed above."""
    answer = (
        "I started by reading the spec.\n"
        "\n"
        "Then I changed the parser.\n"
        "Tests pass.\n"
        "```json\n{\"verdict\": \"pass\"}\n```\n"
    )
    assert _call("(m, a) => m.answerTailOf(a[0])", answer) == [
        "I started by reading the spec.",
        "Then I changed the parser.",
        "Tests pass.",
    ]


@requires_node
def test_answer_tail_is_capped_at_six_lines() -> None:
    answer = "\n".join(f"line {n}" for n in range(1, 21))
    tail = _call("(m, a) => m.answerTailOf(a[0])", answer)
    assert tail == [f"line {n}" for n in range(15, 21)]


# --- the live progress line ------------------------------------------------
@requires_node
def test_live_line_names_the_tool_its_argument_and_how_long_it_has_run() -> None:
    live = {"tool": "Bash", "argument": "git status", "atMs": 1_000}
    line = _call("(m, a) => m.liveLineOf(a[0], a[1])", live, 13_000)
    assert line == "now: Bash git status (12s)"


@requires_node
def test_a_long_tool_argument_is_cut_to_sixty_characters() -> None:
    argument = _call("(m, a) => m.liveArgumentOf(a[0])", "x" * 200)
    assert len(argument) == 60
    assert argument.endswith("…")


@requires_node
def test_a_tool_arguments_newlines_collapse_to_one_line() -> None:
    """A heredoc in a Bash command must not break the panel's line count."""
    assert _call("(m, a) => m.liveArgumentOf(a[0])", "git commit\n -m 'x'") == (
        "git commit -m 'x'"
    )


# --- the whole panel -------------------------------------------------------
@requires_node
def test_panel_draws_live_then_attempts_then_outputs_then_answer() -> None:
    log = {
        "attempts": ATTEMPTS,
        "answer": "Did the thing.\nAll green.",
        "live": [{"tool": "Bash", "argument": "pytest -q", "atMs": 1_000}],
    }
    lines = _call(
        "(m, a) => m.logLinesOf(a[0], a[1], a[2], a[3], a[4])",
        "implement", log, 80, 4_000, "/repo",
    )
    assert lines == [
        "── implement",
        "now: Bash pytest -q (3s)",
        "#2 completed · sonnet-5 · 3m05s · $0.1731 · pass",
        "#1 abandoned · fable-5-1 · 42s · $0.0000? · tool crash",
        "verdict: pass",
        "design_result: design.md",
        "→ design.md",
        "Did the thing.",
        "All green.",
    ]


@requires_node
def test_panel_says_it_is_reading_before_the_first_read_settles() -> None:
    lines = _call(
        "(m, a) => m.logLinesOf(a[0], a[1], a[2], a[3])", "implement", None, 80, 0
    )
    assert lines == ["── implement", "reading…"]


@requires_node
def test_a_step_with_no_attempts_says_so_rather_than_drawing_blank() -> None:
    lines = _call(
        "(m, a) => m.logLinesOf(a[0], a[1], a[2], a[3])",
        "review", {"attempts": [], "live": []}, 80, 0,
    )
    assert lines == ["── review", "no attempts yet"]


@requires_node
def test_with_no_step_selected_the_panel_draws_nothing() -> None:
    assert _call("(m, a) => m.logLinesOf(a[0], a[1], a[2], a[3])", None, None, 80, 0) == []


@requires_node
def test_every_panel_line_is_cut_to_the_panes_width() -> None:
    """The narrow tiers must not wrap a line and break the row count."""
    log = {"attempts": ATTEMPTS, "live": [], "answer": "x" * 200}
    lines = _call(
        "(m, a) => m.logLinesOf(a[0], a[1], a[2], a[3], a[4])",
        "implement", log, 40, 0, "/repo",
    )
    assert lines
    assert all(len(line) <= 40 for line in lines)


# --- the panel's own window ------------------------------------------------
@requires_node
def test_a_short_log_is_drawn_whole() -> None:
    lines = ["a", "b", "c"]
    assert _call("(m, a) => m.logWindowOf(a[0], a[1])", lines, 0) == lines


@requires_node
def test_a_long_log_shows_twelve_lines_from_the_offset() -> None:
    lines = [str(n) for n in range(20)]
    window = _call("(m, a) => m.logWindowOf(a[0], a[1])", lines, 3)
    assert window == [str(n) for n in range(3, 15)]


@requires_node
def test_an_offset_past_the_end_lands_on_the_last_page() -> None:
    """The selected step can change under a scrolled window; never draw blank."""
    lines = [str(n) for n in range(20)]
    window = _call("(m, a) => m.logWindowOf(a[0], a[1])", lines, 99)
    assert window == [str(n) for n in range(8, 20)]
