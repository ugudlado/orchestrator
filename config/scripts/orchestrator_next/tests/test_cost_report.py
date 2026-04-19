"""
Tests for orchestrator_next.cost_report — aggregate_repo(scope="complexity")
and _by_complexity() function.

T-5 (HL-291): RED tests — verify complexity arm of cost reporting.

Test scenarios:
- aggregate_repo(scope="complexity") returns dict with expected structure
- All five buckets (XS, S, M, L, XL) appear when seeded
- Features without feature_complexity row fall into 'unknown' bucket
- Features with NULL complexity fall into 'unknown' bucket
- Bucket ordering: XS, S, M, L, XL, unknown
- Empty buckets are omitted from output
- render_markdown_repo outputs columns: complexity | features | total_cost | median_cost | p90_cost
- aggregate_repo raises ValueError for unknown scope (non-regression)
"""
from __future__ import annotations

import os
import sys

import pytest
import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = "/home/user/myrepo"
REPO_BASENAME = "myrepo"


@pytest.fixture()
def conn():
    """In-memory DuckDB with step_events and feature_complexity tables."""
    c = duckdb.connect(":memory:")
    c.execute("""
        CREATE TABLE step_events (
            repo_root  VARCHAR NOT NULL,
            change_id  VARCHAR NOT NULL,
            phase      VARCHAR NOT NULL,
            step_id    VARCHAR NOT NULL,
            attempt    INTEGER NOT NULL,
            agent_name VARCHAR NOT NULL,
            status     VARCHAR NOT NULL,
            schema_name VARCHAR,
            started_at  TIMESTAMP,
            ended_at    TIMESTAMP,
            duration_ms BIGINT,
            gen_ai_request_model  VARCHAR,
            gen_ai_usage_input_tokens  BIGINT,
            gen_ai_usage_output_tokens BIGINT,
            gen_ai_usage_cache_read_input_tokens BIGINT,
            gen_ai_usage_cost_usd  DOUBLE,
            tool_calls_json  VARCHAR,
            artifacts_json   VARCHAR,
            escalation_json  VARCHAR,
            upserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (repo_root, change_id, phase, step_id, attempt, status)
        )
    """)
    c.execute("""
        CREATE TABLE feature_complexity (
            repo_root    VARCHAR NOT NULL,
            change_id    VARCHAR NOT NULL,
            complexity   VARCHAR,
            schema_name  VARCHAR,
            started_at   TIMESTAMP,
            completed_at TIMESTAMP,
            upserted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (repo_root, change_id)
        )
    """)
    yield c
    c.close()


def _make_event_inserter():
    """Return a stateful insert function with its own counter (avoids PK collisions)."""
    counter: dict[tuple, int] = {}

    def _insert(conn, change_id: str, cost: float, repo_root: str = REPO_ROOT):
        key = (repo_root, change_id)
        counter[key] = counter.get(key, 0) + 1
        step_id = f"step-{counter[key]}"
        conn.execute("""
            INSERT INTO step_events
              (repo_root, change_id, phase, step_id, attempt, agent_name, status,
               gen_ai_usage_cost_usd, started_at)
            VALUES (?, ?, 'implement', ?, 1, 'developer', 'completed', ?, '2026-01-01')
        """, [repo_root, change_id, step_id, cost])

    return _insert


# Default inserter for simple single-event tests (no PK collision risk for distinct change_ids)
def _insert_step_event(conn, change_id: str, cost: float, repo_root: str = REPO_ROOT):
    """Insert a single step_event row for testing (unique change_id per test expected)."""
    conn.execute("""
        INSERT INTO step_events
          (repo_root, change_id, phase, step_id, attempt, agent_name, status,
           gen_ai_usage_cost_usd, started_at)
        VALUES (?, ?, 'implement', 'step-1', 1, 'developer', 'completed', ?, '2026-01-01')
    """, [repo_root, change_id, cost])


def _insert_complexity(conn, change_id: str, complexity, repo_root: str = REPO_ROOT):
    """Insert a feature_complexity row for testing."""
    conn.execute("""
        INSERT INTO feature_complexity (repo_root, change_id, complexity)
        VALUES (?, ?, ?)
    """, [repo_root, change_id, complexity])


@pytest.fixture()
def seeded_conn(conn):
    """DB seeded with 5 bucketed features + 1 unknown (no complexity row) + 1 null complexity."""
    insert = _make_event_inserter()

    # Five complexity buckets, 2 step_events each to test median/p90
    for cid, cx, cost1, cost2 in [
        ("feat-xs-1", "XS", 0.10, 0.12),
        ("feat-s-1",  "S",  0.20, 0.22),
        ("feat-m-1",  "M",  0.30, 0.32),
        ("feat-l-1",  "L",  0.40, 0.42),
        ("feat-xl-1", "XL", 0.50, 0.52),
    ]:
        insert(conn, cid, cost1)
        insert(conn, cid, cost2)
        _insert_complexity(conn, cid, cx)

    # One feature with no complexity row -> unknown
    insert(conn, "feat-no-row", 0.60)

    # One feature with NULL complexity -> unknown
    insert(conn, "feat-null-cx", 0.70)
    _insert_complexity(conn, "feat-null-cx", None)

    return conn


# ---------------------------------------------------------------------------
# T-5 (HL-291): aggregate_repo(scope="complexity") tests
# ---------------------------------------------------------------------------

class TestAggrepoByComplexity:

    def test_aggregate_repo_complexity_returns_dict(self, seeded_conn):
        """aggregate_repo(scope='complexity') returns a dict (FR-6)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        assert isinstance(result, dict)

    def test_aggregate_repo_complexity_has_rows_key(self, seeded_conn):
        """aggregate_repo(scope='complexity') result has 'rows' key (FR-6)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        assert "rows" in result

    def test_aggregate_repo_complexity_has_scope_key(self, seeded_conn):
        """aggregate_repo(scope='complexity') result has scope='complexity' (FR-6)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        assert result.get("scope") == "complexity"

    def test_all_five_buckets_present(self, seeded_conn):
        """All five complexity buckets appear in result when seeded (FR-6)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        complexities = {r["complexity"] for r in result["rows"]}
        assert "XS" in complexities
        assert "S" in complexities
        assert "M" in complexities
        assert "L" in complexities
        assert "XL" in complexities

    def test_unknown_bucket_present(self, seeded_conn):
        """Features without a feature_complexity row appear in 'unknown' bucket (FR-7, AC-3)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        complexities = {r["complexity"] for r in result["rows"]}
        assert "unknown" in complexities

    def test_null_complexity_in_unknown_bucket(self, seeded_conn):
        """Features with NULL complexity in feature_complexity also map to 'unknown' (FR-7)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        # feat-no-row and feat-null-cx should both be in unknown
        unknown_row = next(r for r in result["rows"] if r["complexity"] == "unknown")
        assert unknown_row["features"] == 2

    def test_bucket_ordering_xs_s_m_l_xl_unknown(self, seeded_conn):
        """Buckets are ordered XS, S, M, L, XL, unknown (FR-8, AC-2)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        ordered = [r["complexity"] for r in result["rows"]]
        expected_order = ["XS", "S", "M", "L", "XL", "unknown"]
        # All present buckets should appear in expected order
        present = [b for b in expected_order if b in ordered]
        assert ordered == present

    def test_row_has_required_columns(self, seeded_conn):
        """Each row has complexity, features, total_cost, median_cost, p90_cost (FR-6, AC-2)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        for row in result["rows"]:
            assert "complexity" in row
            assert "features" in row
            assert "total_cost" in row
            assert "median_cost" in row
            assert "p90_cost" in row

    def test_feature_count_per_bucket(self, seeded_conn):
        """Each labeled bucket has exactly 1 feature (FR-6)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        for row in result["rows"]:
            if row["complexity"] != "unknown":
                assert row["features"] == 1, f"Expected 1 feature in {row['complexity']}, got {row['features']}"

    def test_empty_buckets_omitted(self, conn):
        """Buckets with no features are omitted from the result (FR-8)."""
        # Only seed XS and M
        _insert_step_event(conn, "feat-xs", 0.10)
        _insert_complexity(conn, "feat-xs", "XS")
        _insert_step_event(conn, "feat-m", 0.30)
        _insert_complexity(conn, "feat-m", "M")

        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(conn, REPO_BASENAME, scope="complexity")
        complexities = [r["complexity"] for r in result["rows"]]
        assert "XS" in complexities
        assert "M" in complexities
        # S, L, XL, unknown should not appear (no data)
        assert "S" not in complexities
        assert "L" not in complexities
        assert "XL" not in complexities
        assert "unknown" not in complexities

    def test_aggregate_repo_empty_dataset(self, conn):
        """aggregate_repo(scope='complexity') returns empty rows list on no data (FR-8)."""
        from orchestrator_next.cost_report import aggregate_repo
        result = aggregate_repo(conn, REPO_BASENAME, scope="complexity")
        assert result["rows"] == []

    def test_uses_repo_basename_not_full_path(self, conn):
        """aggregate_repo uses basename matching, not full repo_root path (NFR-3)."""
        # Insert with full path repo_root
        _insert_step_event(conn, "feat-test", 0.15)
        _insert_complexity(conn, "feat-test", "S")

        from orchestrator_next.cost_report import aggregate_repo
        # Query with basename only — must find the row
        result = aggregate_repo(conn, REPO_BASENAME, scope="complexity")
        assert len(result["rows"]) > 0


# ---------------------------------------------------------------------------
# T-5 (HL-291): render_markdown_repo with scope="complexity"
# ---------------------------------------------------------------------------

class TestRenderMarkdownRepoComplexity:

    def test_render_complexity_produces_markdown(self, seeded_conn):
        """render_markdown_repo with complexity scope produces non-empty markdown (FR-8)."""
        from orchestrator_next.cost_report import aggregate_repo, render_markdown_repo
        data = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        output = render_markdown_repo(data, scope="complexity")
        assert isinstance(output, str)
        assert len(output) > 0

    def test_render_complexity_has_required_columns(self, seeded_conn):
        """render_markdown_repo complexity output includes all five column headers (AC-2)."""
        from orchestrator_next.cost_report import aggregate_repo, render_markdown_repo
        data = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        output = render_markdown_repo(data, scope="complexity")
        assert "complexity" in output.lower()
        assert "features" in output.lower() or "feature" in output.lower()
        assert "total_cost" in output.lower() or "total" in output.lower()
        assert "median_cost" in output.lower() or "median" in output.lower()
        assert "p90_cost" in output.lower() or "p90" in output.lower()

    def test_render_complexity_contains_bucket_labels(self, seeded_conn):
        """render_markdown_repo complexity output contains bucket labels (AC-2)."""
        from orchestrator_next.cost_report import aggregate_repo, render_markdown_repo
        data = aggregate_repo(seeded_conn, REPO_BASENAME, scope="complexity")
        output = render_markdown_repo(data, scope="complexity")
        assert "XS" in output
        assert "S" in output
        assert "M" in output
        assert "L" in output
        assert "XL" in output
        assert "unknown" in output
