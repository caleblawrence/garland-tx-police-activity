#!/usr/bin/env python
"""Garland's figures as the FBI holds them, for context the PDFs cannot give.

The city's own reports are an incident list covering eight selected categories.
This is the other half of the picture: what Garland PD reported to the FBI
under UCR/NIBRS, monthly, since January 2019 — with two things the PDFs have
no way to express.

  - Clearances. Nothing in the incident reports says whether anything was
    solved. This does.
  - Comparison. Garland against Texas and the United States, per 100,000, so a
    number has something to be large or small next to.

It also reaches back to 2019, where the monthly archive starts in February
2022, and currently runs one month ahead of it.

These figures will NOT match the archive, and are stored apart from it for the
same reason the weekly and monthly feeds are kept apart. UCR counts offences
within incidents on a national standard; the city's report is a curated list of
selected incidents. Different definitions, different timing. Presenting one as
a check on the other would be wrong.

Needs a free key from https://api.data.gov/signup/ in FBI_CDE_API_KEY.

    uv run python -m garland_tx_data_analysis.fbi_ucr --fetch
    uv run python -m garland_tx_data_analysis.fbi_ucr --export
"""

import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

from garland_tx_data_analysis.tools import WORK_DIR, connect, ensure_schema

# Garland Police Department. NIBRS-reporting since 2020-01-01.
ORI = "TX0571100"
AGENCY = "Garland Police Department"
BASE = "https://api.usa.gov/crime/fbi/cde/summarized/agency/{ori}/{offense}"
EXPORT_PATH = f"{WORK_DIR}/ucr_monthly.json"

# The public CDE vocabulary, confirmed by probing: these ten and no more.
# Weapons-law and drug offences are rejected as invalid offense ids on every
# endpoint, so the categories the city's report omits are not recoverable here
# either — worth knowing before treating this as the "complete" picture.
OFFENSES = {
    "V": "Violent crime (total)",
    "P": "Property crime (total)",
    "homicide": "Homicide",
    "rape": "Rape",
    "robbery": "Robbery",
    "aggravated-assault": "Aggravated assault",
    "burglary": "Burglary",
    "larceny": "Larceny",
    "motor-vehicle-theft": "Motor vehicle theft",
    "arson": "Arson",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ucr_monthly (
    report_month text    NOT NULL,   -- 'YYYY-MM'
    offense      text    NOT NULL,   -- the CDE offense id
    offenses     integer,
    clearances   integer,
    garland_rate numeric,
    texas_rate   numeric,
    us_rate      numeric,
    population   integer,
    PRIMARY KEY (report_month, offense)
);
CREATE INDEX IF NOT EXISTS ucr_monthly_month_idx ON ucr_monthly (report_month);
"""


def _key() -> str:
    key = os.getenv("FBI_CDE_API_KEY")
    if not key:
        raise SystemExit(
            "FBI_CDE_API_KEY is not set. Get a free key at "
            "https://api.data.gov/signup/ and put it in .env."
        )
    return key


def fetch(offense: str, start: str = "01-2019", end: str = "12-2026") -> list[dict]:
    """One offence category's monthly series, as rows ready to store."""
    response = requests.get(
        BASE.format(ori=ORI, offense=offense),
        params={"from": start, "to": end, "API_KEY": _key()},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()["offenses"]

    actuals = payload.get("actuals", {})
    rates = payload.get("rates", {})
    offences = actuals.get(f"{AGENCY} Offenses", {})
    clearances = actuals.get(f"{AGENCY} Clearances", {})

    rows = []
    for month_key, count in offences.items():
        if count is None:
            continue  # a month the FBI has no submission for, not a zero
        month, year = month_key.split("-")
        rows.append(
            {
                "report_month": f"{year}-{month}",
                "offense": offense,
                "offenses": count,
                "clearances": clearances.get(month_key),
                "garland_rate": rates.get(f"{AGENCY} Offenses", {}).get(month_key),
                "texas_rate": rates.get("Texas Offenses", {}).get(month_key),
                "us_rate": rates.get("United States Offenses", {}).get(month_key),
            }
        )
    return rows


def store(rows: list[dict]) -> int:
    if not rows:
        return 0
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.executemany(
                """
                INSERT INTO ucr_monthly (report_month, offense, offenses,
                       clearances, garland_rate, texas_rate, us_rate)
                VALUES (%(report_month)s, %(offense)s, %(offenses)s,
                        %(clearances)s, %(garland_rate)s, %(texas_rate)s,
                        %(us_rate)s)
                ON CONFLICT (report_month, offense) DO UPDATE
                   SET offenses = EXCLUDED.offenses,
                       clearances = EXCLUDED.clearances,
                       garland_rate = EXCLUDED.garland_rate,
                       texas_rate = EXCLUDED.texas_rate,
                       us_rate = EXCLUDED.us_rate
                """,
                rows,
            )
    return len(rows)


def export(path: str = EXPORT_PATH) -> dict:
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                SELECT report_month, offense, offenses, clearances,
                       garland_rate, texas_rate, us_rate
                  FROM ucr_monthly ORDER BY report_month, offense
                """
            )
            rows = [
                {
                    "month": r[0],
                    "offense": r[1],
                    "offenses": r[2],
                    "clearances": r[3],
                    "garland_rate": float(r[4]) if r[4] is not None else None,
                    "texas_rate": float(r[5]) if r[5] is not None else None,
                    "us_rate": float(r[6]) if r[6] is not None else None,
                }
                for r in cur.fetchall()
            ]

    payload = {"agency": AGENCY, "ori": ORI, "labels": OFFENSES, "rows": rows}
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)
    months = sorted({r["month"] for r in rows})
    return {
        "path": os.path.abspath(path),
        "rows": len(rows),
        "months": len(months),
        "span": f"{months[0]} .. {months[-1]}" if months else None,
    }


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="pull every category and store")
    parser.add_argument("--export", action="store_true", help="write the JSON the site reads")
    args = parser.parse_args(argv)

    if args.fetch:
        total = 0
        for offense, label in OFFENSES.items():
            rows = fetch(offense)
            stored = store(rows)
            total += stored
            latest = max((r["report_month"] for r in rows), default="—")
            print(f"  {label:26s} {stored:4d} months  through {latest}")
        print(f"\nstored {total} rows")

    if args.export:
        print(json.dumps(export(), indent=2))


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
