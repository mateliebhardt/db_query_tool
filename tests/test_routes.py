"""
Flask route tests — database is fully mocked, no real connection needed.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock
import app as flask_app

# ── Helpers ────────────────────────────────────────────────────────────────────

DB_COLS = ["id", "reference", "status"]
DB_ROWS = [
    {"id": 1, "reference": "Gate Valve GV-001",      "status": "active"},
    {"id": 2, "reference": "Ball Valve BV-002",       "status": "inactive"},
    {"id": 3, "reference": "Centrifugal Pump CP-001", "status": "active"},
]

def _make_mock_conn(rows=None):
    """Return a mock psycopg2 connection whose cursor yields `rows`."""
    rows = rows if rows is not None else DB_ROWS
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__  = MagicMock(return_value=False)
    cur.fetchall.return_value = rows

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    return conn


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


def _post(client, extra=None):
    data = {
        "start_date": "2026-01-01",
        "end_date":   "2026-04-30",
        "filter1": "", "filter2": "", "search": "",
    }
    if extra:
        data.update(extra)
    return client.post("/", data=data)


# ── GET /  ─────────────────────────────────────────────────────────────────────

class TestGetIndex:
    def test_renders_200(self, client):
        with patch("app.get_dropdown_options", return_value=[]):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_app_title(self, client):
        with patch("app.get_dropdown_options", return_value=[]):
            resp = client.get("/")
        assert b"Database Query Tool" in resp.data

    def test_contains_filter_labels(self, client):
        with patch("app.get_dropdown_options", return_value=[]):
            resp = client.get("/")
        assert flask_app.FILTER_1_LABEL.encode() in resp.data
        assert flask_app.FILTER_2_LABEL.encode() in resp.data
        assert flask_app.SEARCH_LABEL.encode()   in resp.data

    def test_dropdown_error_shows_message(self, client):
        with patch("app.get_dropdown_options", side_effect=Exception("DB down")):
            resp = client.get("/")
        assert b"Could not load filter options" in resp.data


# ── POST / (query) ─────────────────────────────────────────────────────────────

class TestPostIndex:
    def test_all_rows_returned_without_search(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client)
        assert b"Gate Valve" in resp.data
        assert b"Ball Valve" in resp.data
        assert b"Pump"       in resp.data

    def test_search_filters_to_matching_rows(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client, {"search": "pump"})
        assert b"Pump"       in resp.data
        assert b"Gate Valve" not in resp.data

    def test_fuzzy_search_tolerates_typo(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client, {"search": "vaIve"})
        assert b"Valve" in resp.data
        assert b"Pump"  not in resp.data

    def test_or_search_returns_union(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client, {"search": "pump valve"})
        assert b"Pump"  in resp.data
        assert b"Valve" in resp.data

    def test_no_results_shows_empty_message(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client, {"search": "xyz_nonexistent_999"})
        assert b"No results found" in resp.data

    def test_empty_db_result_shows_empty_message(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn(rows=[])):
            resp = _post(client)
        assert b"No results found" in resp.data

    def test_row_count_displayed(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client)
        assert b"3 rows returned" in resp.data

    def test_uk_date_format_in_results(self, client):
        with patch("app.get_dropdown_options", return_value=[]), \
             patch("app.get_connection", return_value=_make_mock_conn()):
            resp = _post(client, {"start_date": "2026-01-01", "end_date": "2026-04-30"})
        assert b"1 January 2026"  in resp.data
        assert b"30 April 2026"   in resp.data
