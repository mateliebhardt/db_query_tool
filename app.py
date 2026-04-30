import os
import io
import re
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
import pandas as pd
from rapidfuzz import fuzz
from flask import Flask, render_template, request, send_file, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)


@app.template_filter("uk_date")
def uk_date_filter(iso_str):
    """Format 'YYYY-MM-DD' → 'D Month YYYY' for display."""
    try:
        from datetime import datetime
        d = datetime.strptime(iso_str, "%Y-%m-%d")
        return f"{d.day} {d.strftime('%B')} {d.year}"
    except Exception:
        return iso_str


# ── Config ─────────────────────────────────────────────────────────────────────

TABLE_NAME     = "your_table"
DATE_COLUMN    = "created_at"

FILTER_1_COL   = "status"
FILTER_1_LABEL = "Status"

FILTER_2_COL   = "category"
FILTER_2_LABEL = "Category"

SEARCH_COL       = "reference"  # column to run the Python-side text search against
SEARCH_LABEL     = "Reference"
FUZZY_THRESHOLD  = 80           # 0–100: lower = more lenient, higher = stricter

# ── Database connection ────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def get_dropdown_options(column):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT {column} FROM {TABLE_NAME} "
                f"WHERE {column} IS NOT NULL ORDER BY {column}"
            )
            return [str(row[0]) for row in cur.fetchall()]


# ── Query builder (date + dropdowns only — search is done in Python) ───────────

BASE_QUERY = f"""
SELECT *
FROM {TABLE_NAME}
WHERE {DATE_COLUMN} >= %(start_date)s
  AND {DATE_COLUMN} <  %(end_date)s
"""

def build_query(filter1, filter2):
    q = BASE_QUERY
    params = {}
    if filter1:
        q += f"  AND {FILTER_1_COL} = %(filter1)s\n"
        params["filter1"] = filter1
    if filter2:
        q += f"  AND {FILTER_2_COL} = %(filter2)s\n"
        params["filter2"] = filter2
    q += f"ORDER BY {DATE_COLUMN} DESC"
    return q, params


# ── Python-side fuzzy search ───────────────────────────────────────────────────

_SEP_RE = re.compile(r"[-_/.\s]+")

def _strip_seps(text: str) -> str:
    """'GV-001' → 'gv001'  (collapse all separators for code matching)"""
    return _SEP_RE.sub("", text).lower()

def _fuzzy_hit(term: str, value: str) -> bool:
    """
    Two-strategy match optimised for engineering data:
      1. partial_ratio  — handles typos and case ('vaIve' → 'valve')
      2. separator-strip — handles code formatting ('GV001' ↔ 'GV-001')
    """
    t_low, v_low = term.lower(), value.lower()
    if fuzz.partial_ratio(t_low, v_low) >= FUZZY_THRESHOLD:
        return True
    t_stripped = _strip_seps(term)
    return bool(t_stripped) and t_stripped in _strip_seps(value)

def apply_search(columns, rows, search):
    if not search.strip() or SEARCH_COL not in columns:
        return rows
    col_idx = columns.index(SEARCH_COL)
    terms   = search.split()
    return [
        row for row in rows
        if any(_fuzzy_hit(term, str(row[col_idx])) for term in terms)
    ]


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def index():
    default_end   = date.today()
    default_start = default_end - timedelta(days=30)

    start_date = request.form.get("start_date", str(default_start))
    end_date   = request.form.get("end_date",   str(default_end))
    filter1    = request.form.get("filter1", "")
    filter2    = request.form.get("filter2", "")
    search     = request.form.get("search", "").strip()
    error      = None
    columns    = []
    rows       = []
    row_count  = 0

    try:
        options1 = get_dropdown_options(FILTER_1_COL)
        options2 = get_dropdown_options(FILTER_2_COL)
    except Exception as exc:
        options1, options2 = [], []
        error = f"Could not load filter options: {exc}"

    if request.method == "POST" and not error:
        try:
            query, extra_params = build_query(filter1, filter2)
            params = {"start_date": start_date, "end_date": end_date, **extra_params}

            with get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(query, params)
                    results = cur.fetchall()
                    if results:
                        columns = list(results[0].keys())
                        rows    = [list(r.values()) for r in results]

            rows      = apply_search(columns, rows, search)
            row_count = len(rows)
        except Exception as exc:
            error = str(exc)

    return render_template(
        "index.html",
        start_date=start_date,
        end_date=end_date,
        filter1=filter1,
        filter2=filter2,
        search=search,
        filter1_label=FILTER_1_LABEL,
        filter2_label=FILTER_2_LABEL,
        search_label=SEARCH_LABEL,
        options1=options1,
        options2=options2,
        columns=columns,
        rows=rows,
        row_count=row_count,
        error=error,
    )


@app.route("/download", methods=["POST"])
def download():
    start_date = request.form.get("start_date")
    end_date   = request.form.get("end_date")
    filter1    = request.form.get("filter1", "")
    filter2    = request.form.get("filter2", "")
    search     = request.form.get("search", "").strip()

    try:
        query, extra_params = build_query(filter1, filter2)
        params = {"start_date": start_date, "end_date": end_date, **extra_params}

        with get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if search and SEARCH_COL in df.columns:
            terms = search.split()
            mask  = df[SEARCH_COL].astype(str).apply(
                lambda v: any(_fuzzy_hit(t, v) for t in terms)
            )
            df = df[mask]
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")

    output.seek(0)
    filename = f"results_{start_date}_to_{end_date}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
