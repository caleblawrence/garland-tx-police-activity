#!/usr/bin/env python
"""Ingest Garland's monthly Crime Watch archive.

The weekly URL is a fixed document id that always serves the latest week, so it
cannot reach backwards. The monthly archive can: 53 reports as of writing,
February 2022 onward, at https://www.garlandtx.gov/406/Crime-Watch-Reports

These land in `monthly_incidents`, deliberately apart from `incidents`. The two
feeds overlap — December 2025 exists as both four weekly reports and one
monthly one — and at different grains there is no honest way to tell a
duplicate from two real incidents that share a block, a day and an offence.
Keeping them separate means neither can corrupt the other, and the weekly map
stays exactly what it was.

    uv run python -m garland_tx_data_analysis.archive_ingest --list
    uv run python -m garland_tx_data_analysis.archive_ingest --month 2026-04
    uv run python -m garland_tx_data_analysis.archive_ingest --all
    uv run python -m garland_tx_data_analysis.archive_ingest --export

Idempotent: a month already in `monthly_reports` is skipped unless --force.
"""

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import psycopg
import requests
from dotenv import load_dotenv

from garland_tx_data_analysis.categories import CATEGORIES, categorise
from garland_tx_data_analysis.tools import (
    BROWSER_USER_AGENT,
    DOWNLOAD_TIMEOUT_SECONDS,
    WORK_DIR,
    _extract_text,
    _find_report_month,
    _parse_report,
    connect,
    ensure_schema,
)

ARCHIVE_PAGE = "https://www.garlandtx.gov/406/Crime-Watch-Reports"
ARCHIVE_ITEM = "https://www.garlandtx.gov/Archive.aspx?ADID={archive_id}"
EXPORT_PATH = f"{WORK_DIR}/archive_incidents.json"

MONTH_IN_LABEL = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)",
    re.IGNORECASE,
)


def _get(url: str) -> requests.Response:
    response = requests.get(
        url,
        headers={"User-Agent": BROWSER_USER_AGENT},
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response


def list_archive() -> list[dict]:
    """Every monthly report the archive page links to, newest first.

    Only the archive id is taken from the page. The month comes from the PDF
    itself at ingest time, because the labels are not trustworthy — one 2024
    entry reads "April Crime Watch Reports (PDF)" with no year.
    """
    page = _get(ARCHIVE_PAGE).text
    found: dict[int, str] = {}
    for href, inner in re.findall(
        r'<a[^>]*href="(Archive\.aspx\?ADID=\d+)"[^>]*>(.*?)</a>', page, re.S | re.I
    ):
        label = html.unescape(re.sub("<[^>]*>", "", inner)).strip()
        if not MONTH_IN_LABEL.search(label):
            continue
        archive_id = int(href.split("=")[1])
        found.setdefault(archive_id, label)
    return [
        {"archive_id": i, "label": label}
        for i, label in sorted(found.items(), reverse=True)
    ]


LABEL_MONTH_YEAR = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+(\d{4})",
    re.IGNORECASE,
)
MONTH_NUMBERS = {
    m: i
    for i, m in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}


def month_hint(label: str) -> Optional[str]:
    """The month an archive label claims, or None if it does not say.

    Only ever a hint. It decides whether a PDF is worth downloading; the month
    actually stored always comes from the document. One 2024 entry reads
    "April Crime Watch Reports (PDF)" with no year, and this returns None for
    it rather than guessing — that PDF gets downloaded and asked.
    """
    m = LABEL_MONTH_YEAR.search(label)
    if not m:
        return None
    return f"{int(m.group(2)):04d}-{MONTH_NUMBERS[m.group(1).lower()]:02d}"


def _stored_months(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT report_month, stored_total FROM monthly_reports")
        return dict(cur.fetchall())


def ingest_one(
    archive_id: int, force: bool = False, allow_shortfall: bool = False
) -> dict:
    """Fetch, parse and store one monthly report.

    Refuses a month whose numbered districts do not add up, for the same reason
    `store_incidents` refuses a week: rows were dropped and nobody knows which.
    The unnumbered `DISTRICT` block is reported rather than refused — its rows
    cannot be attributed to a district and are dropped by design, which is the
    one place monthly reports differ from weekly ones.
    """
    pdf_bytes = _get(ARCHIVE_ITEM.format(archive_id=archive_id)).content
    if not pdf_bytes.startswith(b"%PDF-"):
        return {"archive_id": archive_id, "status": "not-a-pdf"}

    scratch = Path(WORK_DIR) / f"archive-{archive_id}.pdf"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(pdf_bytes)

    lines = _extract_text(str(scratch)).split("\n")
    month = _find_report_month(lines)
    if not month:
        return {"archive_id": archive_id, "status": "no-month-header"}

    incidents, sections = _parse_report(lines)
    numbered_gaps = [
        s
        for s in sections
        if s["district"] is not None and s["declared_total"] != s["parsed_total"]
    ]
    if numbered_gaps and not allow_shortfall:
        return {
            "archive_id": archive_id,
            "month": month,
            "status": "refused",
            "detail": [
                f"district {s['district']}: declared {s['declared_total']}, "
                f"parsed {s['parsed_total']}"
                for s in numbered_gaps
            ],
        }

    shortfall = sum(
        s["declared_total"] - s["parsed_total"] for s in numbered_gaps
    )
    declared = sum(s["declared_total"] for s in sections)
    unattributed = sum(
        s["declared_total"] for s in sections if s["district"] is None
    )

    with connect() as conn:
        ensure_schema(conn)
        already = _stored_months(conn)
        if month in already and not force:
            return {
                "archive_id": archive_id,
                "month": month,
                "status": "already-stored",
                "stored": already[month],
            }
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM monthly_incidents WHERE report_month = %s", (month,)
            )
            cur.executemany(
                """
                INSERT INTO monthly_incidents
                       (report_month, district, occurred_on, incident, location)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (
                        month,
                        row["district"],
                        row["date"],
                        row["incident"],
                        row["location"] or None,
                    )
                    for row in incidents
                ],
            )
            cur.execute(
                """
                INSERT INTO monthly_reports (report_month, archive_id,
                       declared_total, stored_total, unattributed_rows,
                       shortfall_rows)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (report_month) DO UPDATE
                   SET archive_id = EXCLUDED.archive_id,
                       declared_total = EXCLUDED.declared_total,
                       stored_total = EXCLUDED.stored_total,
                       unattributed_rows = EXCLUDED.unattributed_rows,
                       shortfall_rows = EXCLUDED.shortfall_rows,
                       ingested_at = now()
                """,
                (month, archive_id, declared, len(incidents), unattributed, shortfall),
            )
    scratch.unlink(missing_ok=True)
    return {
        "archive_id": archive_id,
        "month": month,
        "status": "stored",
        "stored": len(incidents),
        "declared": declared,
        "unattributed": unattributed,
        "shortfall": shortfall,
    }


def export(path: str = EXPORT_PATH) -> dict:
    """Write the archive as JSON for the site build, labels applied where known.

    Reuses `incident_labels`, so an offence code named for the weekly map reads
    the same on the archive page. Codes the weekly feed has never seen keep
    their verbose form rather than being invented here — one place decides what
    a code is called, and it is not this script.
    """
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.report_month, m.district, m.occurred_on, m.incident,
                       m.location, l.short_description
                  FROM monthly_incidents m
                  LEFT JOIN incident_labels l USING (incident)
                 ORDER BY m.occurred_on, m.district, m.incident
                """
            )
            rows = [
                {
                    "month": r[0],
                    "district": r[1],
                    "date": r[2].isoformat(),
                    "incident": r[3],
                    "location": r[4] or "",
                    "short_description": r[5] or r[3],
                    "labelled": r[5] is not None,
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT report_month, declared_total, stored_total,
                       unattributed_rows, shortfall_rows
                  FROM monthly_reports ORDER BY report_month
                """
            )
            months = [
                {
                    "month": r[0],
                    "declared_total": r[1],
                    "stored_total": r[2],
                    "unattributed_rows": r[3],
                    "shortfall_rows": r[4],
                    "complete": r[4] == 0,
                }
                for r in cur.fetchall()
            ]

    # The category travels with the row so the page never has to know how an
    # offence code maps to one. There is exactly one place that decides, and it
    # is categories.py.
    for row in rows:
        row["category"] = categorise(row["incident"])

    payload = {
        "months": months,
        "categories": CATEGORIES,
        "incidents": rows,
        "unlabelled_codes": sorted(
            {r["incident"] for r in rows if not r["labelled"]}
        ),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=1)
    by_category: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    return {
        "path": os.path.abspath(path),
        "months": len(months),
        "incidents": len(rows),
        "unlabelled_codes": len(payload["unlabelled_codes"]),
        "by_category": by_category,
    }


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show what the archive offers")
    parser.add_argument("--month", help="ingest one month, as YYYY-MM")
    parser.add_argument("--all", action="store_true", help="ingest every month not yet stored")
    parser.add_argument("--force", action="store_true", help="re-ingest a month already stored")
    parser.add_argument(
        "--allow-shortfall",
        action="store_true",
        help=(
            "store a month whose numbered districts come up short. The gap is "
            "recorded and shown on the page rather than hidden — for a dozen "
            "months the PDF's text layer genuinely holds fewer rows than the "
            "report declares, in both pypdf extraction modes"
        ),
    )
    parser.add_argument("--export", action="store_true", help="write the JSON the site build reads")
    args = parser.parse_args(argv)

    if args.list:
        items = list_archive()
        print(f"{len(items)} monthly reports listed")
        with connect() as conn:
            ensure_schema(conn)
            stored = _stored_months(conn)
        for item in items:
            print(f"  ADID={item['archive_id']:<5} {item['label']}")
        print(f"\nalready stored: {len(stored)} month(s)")
        return

    if args.month or args.all:
        items = list_archive()
        with connect() as conn:
            ensure_schema(conn)
            stored = _stored_months(conn)

        # Oldest first, so an interrupted run leaves a contiguous prefix rather
        # than a scatter of months.
        for item in reversed(items):
            hint = month_hint(item["label"])

            # Skip on the hint alone where it is decisive, so a re-run does not
            # re-download 53 PDFs. A label with no year has no hint, and gets
            # downloaded and asked.
            if args.month and hint and hint != args.month:
                continue
            if not args.month and hint and hint in stored and not args.force:
                print(f"  {hint}  already stored ({stored[hint]} incidents)")
                continue

            result = ingest_one(
                item["archive_id"],
                force=args.force,
                allow_shortfall=args.allow_shortfall,
            )
            month = result.get("month")
            if args.month and month != args.month:
                continue

            status = result["status"]
            if status == "stored":
                short = result.get("shortfall") or 0
                note = f"  SHORT {short} in numbered districts" if short else ""
                print(
                    f"  {month}  stored {result['stored']:>4} of "
                    f"{result['declared']:>4} declared "
                    f"({result['unattributed']} unattributable){note}"
                )
            elif status == "refused":
                print(f"  {month}  REFUSED — {'; '.join(result['detail'])}")
            else:
                print(f"  {month or item['label']}  {status}")

            if args.month and month == args.month:
                break

    if args.export:
        print(json.dumps(export(), indent=2))


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
