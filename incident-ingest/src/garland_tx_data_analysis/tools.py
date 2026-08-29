"""Tools the deep agent drives the weekly-report pipeline with.

Each one is a plain LangChain tool: deterministic work stays in Python, and
every decision the agent could get wrong is left to the agent.

The important one is `parse_incidents`. The PDF closes every district block
with its own `District Total: N` line, so the parser can be checked against
the source rather than trusted. The tool reports that reconciliation instead
of asserting success, which is what gives the extraction auditor something
real to audit.
"""

import json
import os
import re
from collections import Counter
from datetime import date, datetime
from typing import Optional

import psycopg
import requests
from langchain_core.tools import tool
from psycopg.rows import tuple_row
from pypdf import PdfReader

# garlandtx.gov sits behind a filter that answers `404` with an empty body to
# clients sending the default `python-requests/x.y` User-Agent. The download
# then "failed" while the rest of the pipeline happily re-parsed whatever stale
# PDF was already on disk, so a run could publish months-old data and still
# report success. Send a browser UA and verify we actually got a PDF.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DOWNLOAD_TIMEOUT_SECONDS = 60


@tool
def download_weekly_report(url: str, save_path: str) -> str:
    """Download the weekly police-incidents PDF and save it locally.

    Verifies the response really is a PDF and raises if it is not, so the run
    stops instead of silently reusing an older file.

    Args:
        url: The URL of the PDF to download.
        save_path: Local path to write the PDF to.
    """
    response = requests.get(
        url,
        headers={"User-Agent": BROWSER_USER_AGENT},
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    content = response.content
    if not content.startswith(b"%PDF-"):
        # A filter/error page returned with a 200 is worse than an HTTP error:
        # it would parse to zero incidents and look like a quiet week.
        raise ValueError(
            f"{url} returned {len(content)} bytes that are not a PDF "
            f"(content-type={response.headers.get('content-type')!r}). "
            "Refusing to overwrite the local file."
        )

    with open(save_path, "wb") as f:
        f.write(content)
    return f"Downloaded {len(content)} bytes to {os.path.abspath(save_path)}."


def _extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _find_report_period(lines: list[str]) -> Optional[str]:
    """Pull the `Reported Between 05/03/2026 & 05/09/2026` header off page 1.

    Surfacing this makes a stale download obvious in the run output instead of
    something you only notice weeks later by diffing the map.
    """
    for line in lines:
        m = re.search(
            r"Reported Between\s+(\d{1,2}/\d{1,2}/\d{4})\s*&\s*(\d{1,2}/\d{1,2}/\d{4})",
            line,
        )
        if m:
            return f"{m.group(1)} - {m.group(2)}"
    return None


NUMBERED_HEADER_RE = re.compile(r"DISTRICT\s+(\d+)")
BARE_HEADER_RE = re.compile(r"^DISTRICT$")
DISTRICT_TOTAL_RE = re.compile(r"District Total:\s*(\d+)")
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

# A row opens with the beat and case numbers the report runs together, e.g.
# ` 148 02062026R036876 THEFT-...`. Matching that shape is what lets us tell a
# row whose offence name wrapped from an ordinary line of page furniture.
ROW_START_RE = re.compile(r"^\s*\d+\s+\d+R\d+\b")

# Page furniture: the report repeats a footer, a print date and the column
# header on every page. A wrapped row never continues across these, so they
# abandon a part-built row rather than being pasted into it — the print date in
# particular would otherwise supply a date and manufacture a phantom incident.
PAGE_FURNITURE_RE = re.compile(
    r"^\s*(Page \d+ of \d+|Prepared by:|ADDRESSOFFENSECASE#BEAT|Murder \(incl)"
    r"|^\s*\d{1,2}/\d{1,2}/\d{4}\s*$"
)

# A wrapped row spans a handful of physical lines at most. The cap stops a
# malformed page from swallowing the rest of a district into one pending row.
MAX_WRAPPED_LINES = 4


def _parse_report(lines: list[str]) -> tuple[list[dict], list[dict]]:
    """Walk the lines once, returning (incidents, sections).

    Incidents are assigned to the most recent numbered `DISTRICT NN` header.

    A section is one district block: the district it was headed by, the
    `District Total: N` the PDF declares for it, and how many rows we actually
    parsed out of it. The two disagreeing is the signal that the parse lost
    something — most notably for the report's first block, whose header
    arrives as a bare `DISTRICT` with no number. Rows under an unnumbered
    header have no district to belong to and are dropped, exactly as before;
    the difference is that now the drop is counted and reported rather than
    silent.

    Most rows arrive whole, with the offence name and date run together:

        ` 148 13002026R036918 ORGANIZED RETAIL THEFT...07/30/2026 11XX W ...`

    An offence name long enough to wrap is emitted across several physical
    lines instead, and the date lands on none of them:

        ` 148 02062026R036876 THEFT-MATERIAL ALUMINUM/ BRONZE/ COPPER/ L/T `
        `$20,000`
        `07/30/2026 10XX W CENTERVILLE RD`

    So a line that opens a row but carries no date starts a pending row, and
    following lines are appended until one supplies the date. Page furniture
    and the next row abandon a pending row rather than being pasted into it:
    losing a row is caught by the district-total reconciliation, while a
    fabricated one would be published as fact.
    """
    incidents: list[dict] = []
    sections: list[dict] = []

    current_district: Optional[str] = None
    section_count = 0
    # The parts of a row whose offence name wrapped, still waiting for a date.
    pending: list[str] = []

    def record(body: str) -> None:
        """Count a row against its district block, and keep it if attributable."""
        nonlocal section_count
        section_count += 1
        if current_district is None:
            return
        date_match = DATE_RE.search(body)
        incident_type, _, location = body.partition(date_match.group())
        incidents.append(
            {
                "district": current_district,
                "date": date_match.group(),
                "incident": incident_type.strip(),
                "location": location.strip(),
            }
        )

    for line in lines:
        header_match = NUMBERED_HEADER_RE.search(line)
        if header_match:
            pending = []
            current_district = header_match.group(1)
            section_count = 0
            continue

        if BARE_HEADER_RE.match(line.strip()):
            # A bare `DISTRICT` with no number starts a section we cannot
            # attribute. Reset rather than letting its rows leak into the
            # district above it.
            pending = []
            current_district = None
            section_count = 0
            continue

        total_match = DISTRICT_TOTAL_RE.search(line)
        if total_match:
            pending = []
            sections.append(
                {
                    "district": current_district,
                    "declared_total": int(total_match.group(1)),
                    "parsed_total": section_count,
                }
            )
            section_count = 0
            continue

        if PAGE_FURNITURE_RE.match(line):
            # The footer, the print date and the repeated column header. These
            # would otherwise be appended to a pending row — and the print date
            # is a date, so it would complete one and invent an incident.
            pending = []
            continue

        tokens = line.strip().split()
        opens_a_row = bool(ROW_START_RE.match(line))

        if pending and not opens_a_row:
            # Continuation lines carry no beat or case number, so take the
            # whole line rather than dropping the first two tokens.
            pending.append(line.strip())
            body = " ".join(pending)
            if DATE_RE.search(body):
                record(body)
                pending = []
            elif len(pending) >= MAX_WRAPPED_LINES:
                pending = []
            continue

        # A new row starts here, so whatever was pending never found its date.
        pending = []

        if len(tokens) < 3:
            continue
        # Lines look like: " <row#> <report-id> INCIDENT-TYPE<DATE> <LOCATION>"
        body = " ".join(tokens[2:]).strip()
        if DATE_RE.search(body):
            record(body)
        elif opens_a_row:
            pending = [body]

    return incidents, sections


@tool
def parse_incidents(pdf_path: str, output_json_path: str = "work/extracted_incidents.json") -> str:
    """Parse every incident out of a Garland weekly incident PDF.

    Writes the incidents to JSON and returns a summary that reconciles what was
    parsed against the `District Total: N` figure the PDF declares for each
    district block. Read `reconciliation` before trusting the parse: any entry
    in `discrepancies` means rows were lost, and `unnumbered_district_rows`
    counts rows the report filed under a `DISTRICT` header with no number.

    Args:
        pdf_path: Path to the downloaded PDF.
        output_json_path: Where to write the extracted incidents as JSON.
    """
    text = _extract_text(pdf_path)
    lines = text.split("\n")
    report_period = _find_report_period(lines)
    incidents, sections = _parse_report(lines)

    # Stamp the period on every row so downstream consumers can group by week
    # and so re-ingesting the same report is detectable.
    for inc in incidents:
        inc["report_period"] = report_period

    if not incidents:
        raise ValueError(
            f"Parsed 0 incidents out of {pdf_path} (report period "
            f"{report_period!r}). The PDF layout likely changed — failing "
            "rather than publishing an empty week."
        )

    os.makedirs(os.path.dirname(os.path.abspath(output_json_path)) or ".", exist_ok=True)
    with open(output_json_path, "w") as f:
        json.dump(incidents, f, indent=2)

    per_district: dict[str, int] = {}
    for inc in incidents:
        per_district[inc["district"]] = per_district.get(inc["district"], 0) + 1
    unique_types = sorted({inc["incident"] for inc in incidents})

    discrepancies = [
        {
            "district": s["district"],
            "declared_total": s["declared_total"],
            "parsed_total": s["parsed_total"],
            "missing": s["declared_total"] - s["parsed_total"],
        }
        for s in sections
        if s["declared_total"] != s["parsed_total"]
    ]
    unnumbered = sum(s["parsed_total"] for s in sections if s["district"] is None)

    # The verdict is arithmetic, so compute it here rather than paying a model
    # round-trip to reach the same conclusion. `audit_required` is the whole
    # decision: every numbered district either matches the total the PDF
    # declares for it, or it does not.
    #
    # Rows under an unnumbered DISTRICT header are deliberately not a
    # discrepancy. They are a known, quantified, by-design loss — auditing them
    # every week would re-derive the same answer at the same cost.
    audit_required = bool(discrepancies)

    summary = {
        "json_path": os.path.abspath(output_json_path),
        "report_period": report_period,
        "total_incidents": len(incidents),
        "per_district_counts": per_district,
        "unique_incident_types": unique_types,
        "reconciliation": {
            "audit_required": audit_required,
            "summary": (
                f"{len(discrepancies)} district(s) do not match the total the "
                "report declares for them — audit before storing."
                if audit_required
                else "Every numbered district matches its declared total."
            ),
            "declared_total_all_districts": sum(s["declared_total"] for s in sections),
            "stored_incidents": len(incidents),
            "unnumbered_district_rows": unnumbered,
            "sections": sections,
            "discrepancies": discrepancies,
        },
    }
    return json.dumps(summary, indent=2)


@tool
def read_report_text(pdf_path: str, page: Optional[int] = None) -> str:
    """Return the raw extracted text of the PDF, for inspecting it directly.

    Use this when the parse does not reconcile with the report's own district
    totals and you need to see what the source actually says.

    Args:
        pdf_path: Path to the PDF.
        page: 1-based page number. Omit to get every page.
    """
    reader = PdfReader(pdf_path)
    if page is None:
        return "\n".join(
            f"--- page {i} ---\n{(p.extract_text() or '')}"
            for i, p in enumerate(reader.pages, start=1)
        )
    if not 1 <= page <= len(reader.pages):
        raise ValueError(f"Page {page} out of range; the PDF has {len(reader.pages)} pages.")
    return reader.pages[page - 1].extract_text() or ""


# The connection string never passes through the agent: `store_incidents` reads
# it from the environment itself. Tool arguments are echoed into the model's
# context and the run transcript, and a Postgres URL carries a password.
DATABASE_URL_ENV = "DATABASE_URL"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id       bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    -- Nullable only because 344 rows imported from the pre-Postgres history
    -- predate the field. Everything the agent writes carries one.
    report_period     text,
    district          text,
    occurred_on       date NOT NULL,
    incident          text NOT NULL,
    location          text NOT NULL,
    short_description text,
    inserted_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS incidents_natural_key_idx
    ON incidents (report_period, district, occurred_on, incident, location);
CREATE INDEX IF NOT EXISTS incidents_report_period_idx ON incidents (report_period);
CREATE INDEX IF NOT EXISTS incidents_occurred_on_idx ON incidents (occurred_on);
"""


def connect() -> psycopg.Connection:
    """Open a connection using the URL in the environment."""
    url = os.getenv(DATABASE_URL_ENV)
    if not url:
        raise ValueError(
            f"{DATABASE_URL_ENV} is not set. Point it at the Neon database "
            "(see .env) before storing incidents."
        )
    return psycopg.connect(url, row_factory=tuple_row)


def ensure_schema(conn: psycopg.Connection) -> None:
    """Create the incidents table and its indexes if they are not there yet.

    Idempotent, and cheap enough to run before every write so a fresh database
    works without a separate migration step.
    """
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)


def _parse_date(value) -> date:
    """The PDF writes dates as MM/DD/YYYY; the database stores real dates."""
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip(), "%m/%d/%Y").date()


def _incident_key(record: dict) -> tuple:
    """Natural key for an incident row.

    The PDF has no reliably-present unique id (the case number is missing on
    some rows), so identity is report period + district + date + type +
    location. Derived fields like incident_id and short_description are
    excluded on purpose so a re-run with a different label mapping still counts
    as the same incident.

    This key is NOT unique within a report: a single week legitimately contains
    several "same block, same day, same offense" rows (7 in the 07/12/2026
    report). Callers must compare *counts* per key rather than treating a
    repeat as a duplicate — which is why the incidents table deliberately has
    no unique constraint over these columns. See store_incidents.

    Every field uses `or ""` rather than a dict default: the parser writes these
    keys with a None value when the PDF does not carry them (report_period is
    absent from pre-2023 layouts), and `.get(k, "")` returns the default only
    when the key is *missing*. `str(None)` would put the literal text "None" in
    the key, and then in the database.
    """
    return (
        (record.get("report_period") or "").strip(),
        (record.get("district") or "").strip(),
        _parse_date(record.get("date")),
        (record.get("incident") or "").strip(),
        (record.get("location") or "").strip(),
    )


def _existing_counts(conn: psycopg.Connection, periods: list[str]) -> Counter:
    """Count what the database already holds, per natural key.

    Scoped to the report periods being written: the period is part of the key,
    so rows from other weeks can never collide with this batch. NULL periods
    are compared as '' so a report whose period header did not parse still
    dedupes against the rows it already put there.
    """
    if not periods:
        return Counter()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT coalesce(report_period, ''), coalesce(district, ''),
                   occurred_on, incident, location, count(*)
              FROM incidents
             WHERE coalesce(report_period, '') = ANY(%s)
             GROUP BY 1, 2, 3, 4, 5
            """,
            (periods,),
        )
        return Counter({tuple(row[:5]): row[5] for row in cur.fetchall()})


@tool
def store_incidents(
    json_path: str,
    short_description_map: Optional[dict[str, str]] = None,
    enriched_json_path: str = "work/enriched_incidents.json",
) -> str:
    """Label the incidents, append new ones to Postgres, and write the map feed.

    Every incident reaches the enriched JSON the map renders; only incidents the
    database already holds are skipped when appending to it. The database
    connection comes from the environment — you do not pass it.

    Args:
        json_path: Path to the JSON written by parse_incidents.
        short_description_map: Maps the exact verbose incident type from the PDF
            to a concise label, e.g.
            {"THEFT-MOTOR VEHICLE-$2,500 L/T $30,000": "Motor Vehicle Theft"}.
            Types missing from the map fall back to the verbose name.
        enriched_json_path: Where to write the flat enriched list the
            geo-analysis step reads.
    """
    try:
        with open(json_path, "r") as f:
            incidents = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return f"Error reading incidents JSON at {json_path}: {e}"

    mapping = short_description_map or {}

    # The database is the append-only history; the enriched JSON is what the map
    # renders for the current week. These are deliberately different sets:
    # deduping the JSON too would blank the map on any re-run.
    enriched = []
    for inc in incidents:
        record = dict(inc)
        record["short_description"] = mapping.get(
            inc.get("incident", ""), inc.get("incident", "")
        )
        enriched.append(record)

    # The writer used to append unconditionally, so re-running the pipeline in
    # the same week (or after a failed download served up last week's PDF)
    # stacked the same incidents into the database over and over — that is where
    # ~30% of the pre-migration rows came from.
    #
    # Compare per-key *counts* rather than mere presence: a report can hold
    # several rows sharing a natural key, and those are distinct incidents we
    # must keep. Only the surplus beyond what the database already holds is new.
    with connect() as conn:
        ensure_schema(conn)
        periods = sorted({(r.get("report_period") or "").strip() for r in enriched})
        already = _existing_counts(conn, periods)
        incoming = Counter(_incident_key(row) for row in enriched)
        to_insert = {k: max(0, n - already[k]) for k, n in incoming.items()}

        new_rows = []
        for record in enriched:
            key = _incident_key(record)
            if to_insert.get(key, 0) <= 0:
                continue
            to_insert[key] -= 1
            period, district, *rest = key
            # '' is the in-memory spelling of "this report carried no period";
            # the column stores that as NULL, matching the legacy import.
            new_rows.append((period or None, district or None, *rest,
                             record.get("short_description")))

        if new_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO incidents (report_period, district, occurred_on,
                                           incident, location, short_description)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    new_rows,
                )
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM incidents")
            total = cur.fetchone()[0]

    skipped = len(enriched) - len(new_rows)
    unlabelled = sorted({inc.get("incident", "") for inc in incidents} - set(mapping))

    try:
        os.makedirs(os.path.dirname(os.path.abspath(enriched_json_path)) or ".", exist_ok=True)
        with open(enriched_json_path, "w") as f:
            json.dump(enriched, f, indent=2)
    except OSError as e:
        return (
            f"Inserted {len(new_rows)} into Postgres (now {total} total) "
            f"but failed to write enriched JSON to {enriched_json_path}: {e}"
        )

    return (
        f"Read {len(incidents)} incidents from {json_path}. "
        f"Inserted {len(new_rows)} new into Postgres "
        f"(skipped {skipped} already-present duplicates). "
        f"Wrote all {len(enriched)} enriched incidents to "
        f"{os.path.abspath(enriched_json_path)} for the map. "
        f"Database now contains {total} total records. "
        + (
            f"WARNING: {len(unlabelled)} incident types had no label and fell "
            f"back to the verbose name: {unlabelled}"
            if unlabelled
            else "Every incident type was labelled."
        )
    )
