#!/usr/bin/env python
"""What weeks the database actually holds, and which ones it is missing.

A gap in this data looks exactly like a quiet week, and the difference matters
before anything summarises a month or claims a trend. February through April
2026 have no incidents not because Garland had none, but because nobody ran
the pipeline.

`store_incidents` records each week as it writes it. This seeds `report_weeks`
from the history that predates that, and reports coverage for a date range.

    uv run python -m garland_tx_data_analysis.report_coverage
    uv run python -m garland_tx_data_analysis.report_coverage 2026-01-01 2026-08-31
"""

import json
import sys
from datetime import date

from dotenv import load_dotenv

from garland_tx_data_analysis.tools import connect, coverage, ensure_schema


def seed_from_incidents() -> int:
    """Record every week already in `incidents`. Idempotent.

    Rows with no report period are deliberately left out: 211 of them predate
    the field, and placing them on a calendar would invent the coverage this
    table exists to report honestly.
    """
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO report_weeks
                       (report_period, period_start, period_end, incidents_stored)
                SELECT report_period,
                       to_date(split_part(report_period, ' - ', 1), 'MM/DD/YYYY'),
                       to_date(split_part(report_period, ' - ', 2), 'MM/DD/YYYY'),
                       count(*)
                  FROM incidents
                 WHERE report_period IS NOT NULL
                 GROUP BY report_period
                ON CONFLICT (report_period) DO UPDATE
                   SET incidents_stored = EXCLUDED.incidents_stored
                """
            )
            return cur.rowcount


def main(argv: list[str]) -> None:
    seeded = seed_from_incidents()
    print(f"report_weeks: {seeded} week(s) recorded from stored incidents.\n")

    if len(argv) >= 2:
        start, end = date.fromisoformat(argv[0]), date.fromisoformat(argv[1])
    else:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT min(occurred_on), max(occurred_on) FROM incidents")
            start, end = cur.fetchone()
        if start is None:
            print("No incidents stored.")
            return

    result = coverage(start, end)
    print(result["coverage_statement"])
    print()
    for week in result["present"]:
        print(f"  have    {week['report_period']}  {week['incidents_stored']:>4} incidents")
    for week in result["missing"]:
        print(f"  MISSING {week}")
    print(f"\n{json.dumps({k: result[k] for k in ('weeks_present','weeks_missing','complete','unattributed_rows')})}")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
