"""One-time import of the old TinyDB history into Postgres.

The pre-Postgres pipeline kept its append-only history in `incidents.db`, a
TinyDB JSON file. This moves those rows into the `incidents` table, preserving
`incident_id` so anything that referenced one still lines up.

Safe to run more than once: rows are inserted by their original primary key and
a second run conflicts on it and does nothing. The JSON file is only read —
keep it as the backup of the pre-migration state.

    uv run migrate_history
"""

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

from garland_tx_data_analysis.tools import (  # noqa: E402
    _parse_date,
    connect,
    ensure_schema,
)

TINYDB_PATH = "incidents.db"


def _legacy_rows(path: str) -> list[dict]:
    """Read TinyDB's storage format: {"_default": {"1": {...}, "2": {...}}}."""
    with open(path) as f:
        payload = json.load(f)
    tables = [t for t in payload.values() if isinstance(t, dict)]
    return [row for table in tables for row in table.values()]


def run() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else TINYDB_PATH
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found — nothing to migrate.")

    rows = _legacy_rows(path)
    if not rows:
        raise SystemExit(f"{path} holds no rows.")

    records = [
        (
            row.get("incident_id"),
            # 344 of the legacy rows predate the report_period field. NULL says
            # "not recorded" rather than inventing a week they might not be in.
            row.get("report_period") or None,
            row.get("district"),
            _parse_date(row.get("date")),
            row.get("incident"),
            row.get("location"),
            row.get("short_description"),
        )
        for row in rows
    ]
    missing_id = [r for r in records if r[0] is None]
    if missing_id:
        raise SystemExit(
            f"{len(missing_id)} legacy rows have no incident_id; refusing to "
            "import without a stable key."
        )

    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM incidents")
            before = cur.fetchone()[0]

            cur.executemany(
                """
                INSERT INTO incidents (incident_id, report_period, district,
                                       occurred_on, incident, location,
                                       short_description)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (incident_id) DO NOTHING
                """,
                records,
            )

            # The identity sequence still starts at 1; move it past the ids we
            # just forced in, or the agent's next insert collides with them.
            cur.execute(
                """
                SELECT setval(pg_get_serial_sequence('incidents', 'incident_id'),
                              (SELECT max(incident_id) FROM incidents))
                """
            )

            cur.execute("SELECT count(*) FROM incidents")
            after = cur.fetchone()[0]
            cur.execute(
                "SELECT min(occurred_on), max(occurred_on), "
                "count(*) FILTER (WHERE report_period IS NULL) FROM incidents"
            )
            first, last, no_period = cur.fetchone()

    print(f"Read {len(rows)} rows from {path}.")
    print(f"Inserted {after - before} (database went {before} -> {after}).")
    print(f"Dates span {first} to {last}; {no_period} rows carry no report period.")
    if after - before == 0 and before > 0:
        print("Nothing new — these rows were already imported.")


if __name__ == "__main__":
    run()
