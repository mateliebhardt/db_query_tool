import os
import io
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
import pandas as pd
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

# ── Database connection ────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )

# ── Static query ───────────────────────────────────────────────────────────────
# Edit this query to match your schema.
# Use %(start_date)s and %(end_date)s as date-range placeholders.

QUERY = """
SELECT *
FROM your_table
WHERE created_at >= %(start_date)s
  AND created_at <  %(end_date)s
ORDER BY created_at DESC
"""

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def index():
    default_end = date.today()
    default_start = default_end - timedelta(days=30)

    start_date = request.form.get("start_date", str(default_start))
    end_date   = request.form.get("end_date",   str(default_end))
    error      = None
    columns    = []
    rows       = []
    row_count  = 0

    if request.method == "POST":
        try:
            with get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(QUERY, {"start_date": start_date, "end_date": end_date})
                    results = cur.fetchall()
                    if results:
                        columns = list(results[0].keys())
                        rows = [list(r.values()) for r in results]
                    row_count = len(rows)
        except Exception as exc:
            error = str(exc)

    return render_template(
        "index.html",
        start_date=start_date,
        end_date=end_date,
        columns=columns,
        rows=rows,
        row_count=row_count,
        error=error,
    )


@app.route("/download", methods=["POST"])
def download():
    start_date = request.form.get("start_date")
    end_date   = request.form.get("end_date")

    try:
        with get_connection() as conn:
            df = pd.read_sql_query(
                QUERY,
                conn,
                params={"start_date": start_date, "end_date": end_date},
            )
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
