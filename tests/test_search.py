"""
Unit tests for the Python-side fuzzy search logic.
No database connection required.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import app as sut

# ── Shared fixtures ────────────────────────────────────────────────────────────

COLUMNS = ["id", "reference", "status"]
ROWS = [
    [1, "Gate Valve GV-001",            "active"],
    [2, "Ball Valve BV-002",             "inactive"],
    [3, "Centrifugal Pump CP-001",       "active"],
    [4, "Pressure Gauge PG-001",         "active"],
    [5, "Safety Relief Valve SRV-001",   "active"],
    [6, "Control Valve CV_002",          "active"],
    [7, "Flow Indicator FI.003",         "active"],
]


# ── _strip_seps ────────────────────────────────────────────────────────────────

class TestStripSeps:
    def test_hyphen(self):
        assert sut._strip_seps("GV-001") == "gv001"

    def test_underscore(self):
        assert sut._strip_seps("CV_002") == "cv002"

    def test_dot(self):
        assert sut._strip_seps("FI.003") == "fi003"

    def test_slash(self):
        assert sut._strip_seps("P/001") == "p001"

    def test_spaces(self):
        assert sut._strip_seps("Gate Valve") == "gatevalve"

    def test_mixed(self):
        assert sut._strip_seps("GV-001/A") == "gv001a"


# ── _fuzzy_hit ─────────────────────────────────────────────────────────────────

class TestFuzzyHit:

    # Basic matching
    def test_exact_match(self):
        assert sut._fuzzy_hit("valve", "valve")

    def test_partial_match_in_longer_string(self):
        assert sut._fuzzy_hit("valve", "Gate Valve GV-001")

    def test_no_match(self):
        assert not sut._fuzzy_hit("pump",  "Gate Valve GV-001")
        assert not sut._fuzzy_hit("xyz99", "Gate Valve GV-001")

    # Case insensitivity
    def test_lowercase_term_uppercase_value(self):
        assert sut._fuzzy_hit("valve", "GATE VALVE GV-001")

    def test_uppercase_term_lowercase_value(self):
        assert sut._fuzzy_hit("VALVE", "gate valve gv-001")

    def test_mixed_case(self):
        assert sut._fuzzy_hit("Valve", "gate valve GV-001")

    # Typo tolerance (the user's stated requirement)
    def test_capital_I_for_lowercase_l(self):
        # "vaIve" → one char off from "valve" → partial_ratio = 80
        assert sut._fuzzy_hit("vaIve", "valve")

    def test_capital_I_for_l_in_longer_string(self):
        assert sut._fuzzy_hit("vaIve", "Gate Valve GV-001")

    def test_single_char_typo(self):
        # "valze" → one substitution
        assert sut._fuzzy_hit("valze", "valve")

    # Separator-agnostic code matching (engineering data)
    def test_code_without_hyphen_matches_hyphenated(self):
        assert sut._fuzzy_hit("GV001",  "Gate Valve GV-001")

    def test_hyphenated_matches_code_without_separator(self):
        assert sut._fuzzy_hit("GV-001", "Gate Valve GV001")

    def test_underscore_variant(self):
        assert sut._fuzzy_hit("CV002",  "Control Valve CV_002")

    def test_dot_variant(self):
        assert sut._fuzzy_hit("FI003",  "Flow Indicator FI.003")

    def test_slash_variant(self):
        assert sut._fuzzy_hit("P001",   "Pump P/001")

    def test_cross_separator_styles(self):
        # User types GV-001, data stored as GV_001
        assert sut._fuzzy_hit("GV-001", "Gate Valve GV_001")


# ── apply_search ───────────────────────────────────────────────────────────────

class TestApplySearch:

    def test_empty_search_returns_all(self):
        assert sut.apply_search(COLUMNS, ROWS, "") == ROWS

    def test_whitespace_only_returns_all(self):
        assert sut.apply_search(COLUMNS, ROWS, "   ") == ROWS

    def test_missing_search_column_returns_all(self):
        assert sut.apply_search(["id", "name"], ROWS, "valve") == ROWS

    def test_single_term(self):
        result = sut.apply_search(COLUMNS, ROWS, "pump")
        assert len(result) == 1
        assert result[0][1] == "Centrifugal Pump CP-001"

    def test_or_logic_two_terms(self):
        # "pump" OR "gauge" — should return both
        result = sut.apply_search(COLUMNS, ROWS, "pump gauge")
        assert len(result) == 2
        refs = {r[1] for r in result}
        assert "Centrifugal Pump CP-001"  in refs
        assert "Pressure Gauge PG-001"    in refs

    def test_or_logic_returns_union_not_intersection(self):
        # If AND logic was used, "valve pump" would return 0 rows (no row has both)
        result = sut.apply_search(COLUMNS, ROWS, "valve pump")
        assert len(result) > 1

    def test_case_insensitive_results_identical(self):
        lower = sut.apply_search(COLUMNS, ROWS, "valve")
        upper = sut.apply_search(COLUMNS, ROWS, "VALVE")
        assert lower == upper

    def test_typo_still_finds_result(self):
        result = sut.apply_search(COLUMNS, ROWS, "vaIve")
        assert len(result) > 0

    def test_part_code_without_separator(self):
        # GV001 must find GV-001; fuzzy may also return other -001 codes (acceptable)
        result = sut.apply_search(COLUMNS, ROWS, "GV001")
        refs = [r[1] for r in result]
        assert any("GV-001" in r for r in refs)

    def test_part_code_cross_separator(self):
        # CV-002 must find CV_002; fuzzy may also match similarly-scored codes
        result = sut.apply_search(COLUMNS, ROWS, "CV-002")
        refs = [r[1] for r in result]
        assert any("CV_002" in r for r in refs)

    def test_no_match_returns_empty(self):
        result = sut.apply_search(COLUMNS, ROWS, "xyz_nonexistent_999")
        assert result == []

    def test_preserves_row_order(self):
        result = sut.apply_search(COLUMNS, ROWS, "valve")
        ids = [r[0] for r in result]
        assert ids == sorted(ids)


# ── build_query ────────────────────────────────────────────────────────────────

class TestBuildQuery:

    def test_no_filters_has_no_extra_params(self):
        _, params = sut.build_query("", "")
        assert "filter1" not in params
        assert "filter2" not in params

    def test_filter1_added(self):
        q, params = sut.build_query("active", "")
        assert "filter1" in params
        assert params["filter1"] == "active"
        assert sut.FILTER_1_COL in q

    def test_filter2_added(self):
        q, params = sut.build_query("", "valve")
        assert "filter2" in params
        assert sut.FILTER_2_COL in q

    def test_both_filters(self):
        q, params = sut.build_query("active", "valve")
        assert "filter1" in params and "filter2" in params

    def test_query_contains_order_by(self):
        q, _ = sut.build_query("", "")
        assert "ORDER BY" in q.upper()

    def test_query_contains_date_placeholders(self):
        q, _ = sut.build_query("", "")
        assert "%(start_date)s" in q
        assert "%(end_date)s"   in q


# ── uk_date_filter ─────────────────────────────────────────────────────────────

class TestUkDateFilter:

    def test_standard_date(self):
        assert sut.uk_date_filter("2026-04-30") == "30 April 2026"

    def test_no_leading_zero_on_day(self):
        assert sut.uk_date_filter("2026-03-05") == "5 March 2026"

    def test_first_of_month(self):
        assert sut.uk_date_filter("2026-12-01") == "1 December 2026"

    def test_invalid_string_returns_original(self):
        assert sut.uk_date_filter("not-a-date") == "not-a-date"

    def test_empty_string_returns_empty(self):
        assert sut.uk_date_filter("") == ""

    def test_all_months(self):
        months = [
            ("01", "January"), ("02", "February"), ("03", "March"),
            ("04", "April"),   ("05", "May"),       ("06", "June"),
            ("07", "July"),    ("08", "August"),    ("09", "September"),
            ("10", "October"), ("11", "November"),  ("12", "December"),
        ]
        for num, name in months:
            assert sut.uk_date_filter(f"2026-{num}-15") == f"15 {name} 2026"
