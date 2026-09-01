"""Tools the deep agent drives the weekly-report pipeline with.

Each one is a plain LangChain tool: deterministic work stays in Python, and
every decision the agent could get wrong is left to the agent.

The important one is `parse_incidents`. The PDF closes every district block
with its own `District Total: N` line, so the parser can be checked against
the source rather than trusted. The tool reports that reconciliation instead
of asserting success, and `store_incidents` refuses a week that does not add
up. The check the report hands us is only worth having if something acts on
it.
"""

import calendar
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg
import requests
from langchain_core.tools import tool
from psycopg.rows import tuple_row
from pypdf import PdfReader

# Where a run keeps its files, and what they are called. These are fixed: the
# city serves one URL, and the pipeline writes the same four files every week.
#
# They are defaults on the tools rather than values the agent types, because an
# agent that types them can mistype them, and gets nothing right by doing so.
# The system prompt used to carry all four paths plus six lines explaining that
# tool paths are project-relative while the agent's own file tools are rooted at
# work/. On its first unattended run the agent wrote its report to
# `/work/run-report.md`, which those file tools resolved to `work/work/`.
WORK_DIR = "work"
PDF_URL = (
    "https://www.garlandtx.gov/DocumentCenter/View/802/"
    "Previous-Week-Selected-Incident-Report-PDF?bidId="
)
PDF_PATH = f"{WORK_DIR}/police_incidents.pdf"
INCIDENTS_JSON_PATH = f"{WORK_DIR}/extracted_incidents.json"
ENRICHED_JSON_PATH = f"{WORK_DIR}/enriched_incidents.json"


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
def download_weekly_report(url: str = PDF_URL, save_path: str = PDF_PATH) -> str:
    """Download this week's police-incidents PDF. Call it with no arguments.

    Verifies the response really is a PDF and raises if it is not, so the run
    stops instead of silently reusing an older file.

    Args:
        url: The city's weekly report URL. Defaults to the right one.
        save_path: Where to write it. Defaults to the run directory.
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


def _pad_date(value: str) -> str:
    """`8/16/2026` -> `08/16/2026`.

    The report writes its own date range both ways depending on the week, and
    the period string is a key: it groups a week, dedupes a re-ingest, and
    answers whether a download was stale. Two spellings of the same week are
    two different weeks to all of that, so it is normalised once, here, rather
    than compared loosely everywhere downstream.
    """
    month, day, year = value.split("/")
    return f"{int(month):02d}/{int(day):02d}/{year}"


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
            return f"{_pad_date(m.group(1))} - {_pad_date(m.group(2))}"
    return None


MONTH_HEADER_RE = re.compile(
    r"^\s*(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+(\d{4})\s*$",
    re.IGNORECASE,
)
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}


def _find_report_month(lines: list[str]) -> Optional[str]:
    """Pull `April 2026` off a monthly report's cover, as `2026-04`.

    Monthly reports carry no `Reported Between` header — the month is a line
    of its own near the top. Taken from the PDF rather than from the archive
    link's label, because the labels are inconsistent ("April Crime Watch
    Reports (PDF)" carries no year at all) and the document knows what it is.
    """
    for line in lines[:20]:
        m = MONTH_HEADER_RE.match(line)
        if m:
            return f"{int(m.group(2)):04d}-{MONTHS[m.group(1).lower()]:02d}"

    # Most monthly reports use the same `Reported Between X & Y` header as the
    # weekly ones, over a whole calendar month. Accepted only when the range
    # really is one entire month — first to last day — so that a weekly report
    # can never be mistaken for a monthly one.
    period = _find_report_period(lines)
    if period:
        start, end = (_parse_date(p) for p in period.split(" - "))
        last_day = calendar.monthrange(start.year, start.month)[1]
        if (start.day, end.day, start.month) == (1, last_day, end.month) and (
            start.year == end.year
        ):
            return f"{start.year:04d}-{start.month:02d}"
    return None


NUMBERED_HEADER_RE = re.compile(r"DISTRICT\s+(\d+)")
BARE_HEADER_RE = re.compile(r"^DISTRICT$")
DISTRICT_TOTAL_RE = re.compile(r"District Total:\s*(\d+)")
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

# A row opens with the beat and case numbers the report runs together, e.g.
# ` 148 02062026R036876 THEFT-...`. Matching that shape is what lets us tell a
# row whose offence name wrapped from an ordinary line of page furniture.
ROW_START_RE = re.compile(r"^\s*\d+\s+\d+R\d+\b")

# The case number, e.g. `2025R022894`. When it is already in the first token the
# beat was run together with it, and only one token precedes the offence.
CASE_IN_TOKEN = re.compile(r"\d{4}R\d{3,}")

# Page furniture: the report repeats a footer, a print date and the column
# header on every page. A wrapped row never continues across these, so they
# abandon a part-built row rather than being pasted into it — the print date in
# particular would otherwise supply a date and manufacture a phantom incident.
PAGE_FURNITURE_RE = re.compile(
    r"^\s*(Page \d+ of \d+|Prepared by:|ADDRESSOFFENSECASE#BEAT|Murder \(incl"
    # `Reported Between 8/16/2026 & 8/22/2026` is the report's own date range,
    # repeated at the top of every page. It carries a date and enough tokens to
    # look exactly like an incident row, so it parsed as one — an incident with
    # no offence, at "& 8/22/2026" — and inflated the count of whichever
    # district the page break happened to fall inside.
    r"|Reported Between|Monthly Selected|Weekly Selected|Selected Crime)"
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
        date_match = DATE_RE.search(body)
        incident_type, _, location = body.partition(date_match.group())
        if not incident_type.strip():
            # No offence text before the date, so this is not an incident
            # whatever else it looks like. Deliberately not counted either:
            # counting it would inflate the district's parsed total and hide
            # the very mismatch that catches lines like this.
            return

        section_count += 1
        if current_district is None:
            return
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

        if not tokens:
            continue
        # Lines look like: " <beat> <case#> INCIDENT-TYPE<DATE> <LOCATION>".
        # Some rows run the beat and case together — `08552025R022894
        # THEFT-ALL ...` — and dropping two tokens then eats the first word of
        # the offence, storing "OTHER-$100 L/T $750" where the report says
        # "THEFT-ALL OTHER-$100 L/T $750". The row still carries a date, so it
        # reconciles against the district total and nothing catches it.
        skip = 1 if CASE_IN_TOKEN.search(tokens[0]) else 2
        # A merged row with no address is only two tokens long
        # (`17002025R000547 BURGLARY-VEH01/10/2025`), and a flat minimum of
        # three dropped it. The report does omit addresses; that is not a
        # reason to lose the incident.
        if len(tokens) <= skip:
            continue
        body = " ".join(tokens[skip:]).strip()
        if DATE_RE.search(body):
            record(body)
        elif opens_a_row:
            pending = [body]

    return incidents, sections


def _district_block_lines(lines: list[str], district: str) -> list[str]:
    """The raw source lines of one district block, header to total inclusive.

    This is what the extraction auditor used to be for. A district that does
    not reconcile has lost rows, and the answer is always visible in its own
    block — a wrapped offence name, a date format the parser does not match, a
    header it did not recognise. A model reading the block and describing it
    produced prose about the source; printing the block produces the source.
    Page furniture is left in deliberately: a row lost across a page break is
    only diagnosable if the break is visible.
    """
    out: list[str] = []
    inside = False
    for line in lines:
        header = NUMBERED_HEADER_RE.search(line)
        if header:
            if inside:
                break  # a second block opened without a total; stop here
            inside = header.group(1) == district
        if inside:
            out.append(line)
            if DISTRICT_TOTAL_RE.search(line):
                break
    return out


# Written next to the parsed incidents so `store_incidents` can refuse a week
# the parse could not vouch for. The incidents JSON itself stays a flat array —
# the geo-analysis step reads it directly and would break on anything else.
SUMMARY_SUFFIX = ".summary.json"


def _summary_path_for(json_path: str) -> str:
    """Where the parse summary sits, given the incidents JSON path."""
    base = json_path[: -len(".json")] if json_path.endswith(".json") else json_path
    return base + SUMMARY_SUFFIX


@tool
def parse_incidents(
    pdf_path: str = PDF_PATH, output_json_path: str = INCIDENTS_JSON_PATH
) -> str:
    """Parse the downloaded report. Call it with no arguments.

    Writes the incidents to JSON and returns a summary that reconciles what was
    parsed against the `District Total: N` figure the PDF declares for each
    district block. Read `reconciliation` before trusting the parse: any entry
    in `discrepancies` means rows were lost, and `unnumbered_district_rows`
    counts rows the report filed under a `DISTRICT` header with no number.

    Also reports `period_check`, which says whether this week is already in the
    database. `status` is one of `new`, `partially-stored`, `already-stored`
    (the download probably served a stale file) or `unknown` (the database
    could not be reached — not the same as nothing being stored).

    Args:
        pdf_path: The downloaded PDF. Defaults to what download_weekly_report
            just wrote.
        output_json_path: Where to write the incidents. Defaults to the run
            directory.
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
            # The block itself, so whoever reads this can see what was lost
            # rather than being told about it.
            "source_lines": _district_block_lines(lines, s["district"]),
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

    # A download that quietly served last week's file is this pipeline's other
    # characteristic failure, and the report period is the thing that shows it.
    # Asking the agent to notice was never going to work: nothing in its
    # toolset could read the database. So the lookup happens here, and what it
    # reports is a status rather than a raw count.
    #
    # "Already stored" and "partially stored" have to be separable. A stale
    # download and a re-run that crashed halfway both put rows for this period
    # in the table, but one must stop the run and the other must be allowed to
    # finish — dedup tops up the missing rows on a second pass.
    stored_rows = _rows_for_period(report_period)
    if stored_rows is None:
        period_status = "unknown"
        period_summary = (
            "Could not reach the database to check whether this week is "
            "already stored."
        )
    elif stored_rows == 0:
        period_status = "new"
        period_summary = "No rows for this period yet."
    elif stored_rows >= len(incidents):
        period_status = "already-stored"
        period_summary = (
            f"The database already holds {stored_rows} rows for "
            f"{report_period!r} and this parse found {len(incidents)}. The "
            "download probably served a stale file."
        )
    else:
        period_status = "partially-stored"
        period_summary = (
            f"The database holds {stored_rows} of this parse's "
            f"{len(incidents)} rows for {report_period!r} — an earlier run "
            "stored some of this week. Storing again tops up the remainder."
        )

    summary = {
        "json_path": os.path.abspath(output_json_path),
        "report_period": report_period,
        "total_incidents": len(incidents),
        "period_check": {
            "status": period_status,
            "already_stored_rows": stored_rows,
            "parsed_rows": len(incidents),
            "stale_download_suspected": period_status == "already-stored",
            "summary": period_summary,
        },
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

    # Alongside the incidents, so store_incidents can refuse a week whose parse
    # did not reconcile. The decision is arithmetic and belongs where it cannot
    # be talked out of: it used to be a subagent's verdict string that the main
    # agent was asked to honour.
    with open(_summary_path_for(output_json_path), "w") as f:
        json.dump(summary, f, indent=2)

    return json.dumps(summary, indent=2)


@tool
def read_report_text(pdf_path: str = PDF_PATH, page: Optional[int] = None) -> str:
    """Return the raw extracted text of the PDF, for inspecting it directly.

    Use this when the parse does not reconcile with the report's own district
    totals and you need to see what the source actually says.

    Args:
        pdf_path: The PDF. Defaults to this week's download.
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

-- The monthly Crime Watch archive, kept deliberately apart from `incidents`.
--
-- The city publishes one weekly PDF (always the latest) and a monthly archive
-- going back to 2022. They overlap: December 2025 appears in both, at two
-- different grains, and the same incident would be two rows with no way to
-- tell. Rather than reconcile a week against a month, the archive lives in its
-- own tables and feeds its own page. Nothing here is geocoded or mapped — the
-- weekly map remains the only thing plotting points.
CREATE TABLE IF NOT EXISTS monthly_incidents (
    monthly_incident_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    report_month        text NOT NULL,   -- 'YYYY-MM', off the PDF's own header
    district            text,
    occurred_on         date NOT NULL,
    incident            text NOT NULL,
    location            text,            -- some rows carry no address at all
    inserted_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS monthly_incidents_month_idx
    ON monthly_incidents (report_month);
CREATE INDEX IF NOT EXISTS monthly_incidents_occurred_on_idx
    ON monthly_incidents (occurred_on);

-- Which months have been ingested, and what the report declared versus what
-- was stored. Same reason as report_weeks: a month absent from this table is
-- a month nobody fetched, not a month without crime.
CREATE TABLE IF NOT EXISTS monthly_reports (
    report_month       text PRIMARY KEY,
    archive_id         integer NOT NULL,
    declared_total     integer NOT NULL,
    stored_total       integer NOT NULL,
    unattributed_rows  integer NOT NULL DEFAULT 0,
    -- Rows a numbered district declared but the PDF's text layer does not
    -- contain. Not a parser gap: pypdf finds the same case numbers in both
    -- extraction modes. Recorded so the page can say a month is incomplete
    -- rather than quietly presenting it as whole.
    shortfall_rows     integer NOT NULL DEFAULT 0,
    ingested_at        timestamptz NOT NULL DEFAULT now()
);

-- `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists,
-- so a column added later needs saying explicitly. Postgres supports
-- IF NOT EXISTS here, which keeps this file the whole schema rather than
-- splitting it across a migrations directory for one column.
ALTER TABLE monthly_reports
    ADD COLUMN IF NOT EXISTS shortfall_rows integer NOT NULL DEFAULT 0;

-- One label per offence code, decided once and reused forever after.
-- Re-deriving labels every week produced 94 different labels for 68 codes:
-- the same code read as "Vandalism" one week and "Criminal Mischief
-- ($100-$750)" the next, so the map's legend was never stable. A model is the
-- right tool for naming a code nobody has seen before and the wrong one for
-- re-deciding a name that already exists.
-- Every week the pipeline has actually ingested.
--
-- Without this, a gap in the data is indistinguishable from a quiet week.
-- The history holds five report periods spread across nine months: February
-- through April 2026 have no rows not because Garland had no crime, but
-- because nobody ran the pipeline. Any statement about a month or a year is a
-- statement about coverage first, and `incidents` alone cannot tell the two
-- apart — a month with no rows looks exactly like a month with no incidents.
CREATE TABLE IF NOT EXISTS report_weeks (
    report_period     text PRIMARY KEY,
    period_start      date NOT NULL,
    period_end        date NOT NULL,
    incidents_stored  integer NOT NULL,
    first_ingested_at timestamptz NOT NULL DEFAULT now(),
    last_ingested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incident_labels (
    incident          text PRIMARY KEY,
    short_description text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- News coverage of Garland policing and public safety. Deliberately not joined
-- to the incident tables: the two describe overlapping events at different
-- grains with no shared identifier, and the stories worth featuring — the
-- Wynne Park devices, the hot-car death — have no record at all, because no
-- offence in the reports' eight categories was ever charged.
-- See docs/adr/0001-news-items-are-separate-from-incident-records.md
CREATE TABLE IF NOT EXISTS news_items (
    news_item_id   bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,

    -- What arrived. May name people; never published as-is.
    url            text NOT NULL UNIQUE,
    source_title   text NOT NULL,
    source_summary text,
    outlet         text,
    published_on   date NOT NULL,

    -- What publishes. Written by hand when an item is featured, with people
    -- referred to by role rather than name.
    -- See docs/adr/0002-news-items-never-name-anyone.md
    featured        boolean NOT NULL DEFAULT false,
    display_title   text,
    display_summary text,
    featured_at     timestamptz,

    -- Set by hand or not at all. The reports carry no identifier a story could
    -- be matched on, and a link to the wrong incident is worse than none.
    monthly_incident_id bigint REFERENCES monthly_incidents(monthly_incident_id),

    ingested_at    timestamptz NOT NULL DEFAULT now(),

    -- A featured item with nothing written for it cannot be published: the
    -- export would otherwise fall back to source_title and print a name.
    CONSTRAINT featured_items_are_written
        CHECK (NOT featured OR (display_title IS NOT NULL
                                AND display_summary IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS news_items_featured_idx
    ON news_items (featured, published_on DESC);
CREATE INDEX IF NOT EXISTS news_items_published_on_idx
    ON news_items (published_on DESC);
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


def _rows_for_period(period: Optional[str]) -> Optional[int]:
    """How many rows the database already holds for this report period.

    Returns None when the database cannot be reached — the parse itself does
    not need it, and a missing DATABASE_URL must not turn a working parse into
    a failure. The caller reports the difference between "nothing stored" and
    "could not check"; they are not the same answer.
    """
    if not period:
        return None
    try:
        with connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM incidents WHERE report_period = %s", (period,)
                )
                return cur.fetchone()[0]
    except Exception:
        return None


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


def _stored_labels(conn: psycopg.Connection, types: list[str]) -> dict[str, str]:
    """The labels already decided for these offence codes."""
    if not types:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident, short_description FROM incident_labels WHERE incident = ANY(%s)",
            (types,),
        )
        return dict(cur.fetchall())


def _learn_labels(
    conn: psycopg.Connection, mapping: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Record labels for codes that do not have one yet.

    Returns (learned, ignored): the codes this call gave a label to for the
    first time, and the codes that already had one and kept it.

    `ON CONFLICT DO NOTHING` is the whole point. A code that already has a label
    keeps it, so whatever the model returns for a code it has seen before is
    discarded and a label cannot drift by being regenerated. Stability is a
    constraint here rather than an instruction in a prompt — the drift this
    replaced happened despite the prompt telling the model not to.
    """
    rows = [(incident, label) for incident, label in mapping.items() if incident and label]
    if not rows:
        return [], []

    # Read before writing: afterwards every code is present and there is no way
    # to tell which ones this call actually taught us.
    existing = _stored_labels(conn, [incident for incident, _ in rows])
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO incident_labels (incident, short_description)
            VALUES (%s, %s)
            ON CONFLICT (incident) DO NOTHING
            """,
            rows,
        )

    learned = sorted(incident for incident, _ in rows if incident not in existing)
    ignored = sorted(
        incident
        for incident, label in rows
        if incident in existing and existing[incident] != label
    )
    return learned, ignored


@tool
def unlabelled_incident_types(json_path: str = INCIDENTS_JSON_PATH) -> str:
    """List the offence codes in a parsed report that have no label yet.

    Returns JSON: the codes needing a label, and how many already have one.
    An empty list means every code in this week's report has been labelled
    before and the labeller has nothing to do — the stored labels are reused.

    Args:
        json_path: The parse output. Defaults to what parse_incidents wrote.
    """
    try:
        with open(json_path, "r") as f:
            incidents = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return json.dumps({"error": f"Error reading incidents JSON at {json_path}: {e}"})

    types = sorted({inc.get("incident", "") for inc in incidents} - {""})
    with connect() as conn:
        ensure_schema(conn)
        known = _stored_labels(conn, types)

    needing = [t for t in types if t not in known]
    return json.dumps(
        {
            "types_in_report": len(types),
            "already_labelled": len(known),
            "needing_labels": needing,
        },
        indent=2,
    )


def _split_period(period: str) -> tuple[date, date]:
    """`08/16/2026 - 08/22/2026` -> the two dates it names."""
    start, _, end = period.partition(" - ")
    return _parse_date(start), _parse_date(end)


def _record_weeks(conn: psycopg.Connection, rows: list[dict]) -> None:
    """Note which weeks this write covered, and how many rows each holds.

    Written in the same transaction as the incidents, so coverage cannot claim
    a week the database does not hold. Re-running a week updates the count and
    the last-seen time rather than adding a second row: it is the same week.

    Rows with no report period are skipped. The 211 legacy rows imported from
    the pre-Postgres history predate the field and cannot be attributed to a
    week, and inventing one for them would be the exact error this table is
    built to prevent.
    """
    periods = sorted({(r.get("report_period") or "").strip() for r in rows} - {""})
    if not periods:
        return
    with conn.cursor() as cur:
        for period in periods:
            try:
                start, end = _split_period(period)
            except ValueError:
                continue  # not a period we can place on a calendar
            cur.execute(
                "SELECT count(*) FROM incidents WHERE report_period = %s", (period,)
            )
            cur.execute(
                """
                INSERT INTO report_weeks
                       (report_period, period_start, period_end, incidents_stored)
                VALUES (%s, %s, %s,
                        (SELECT count(*) FROM incidents WHERE report_period = %s))
                ON CONFLICT (report_period) DO UPDATE
                   SET incidents_stored = EXCLUDED.incidents_stored,
                       last_ingested_at = now()
                """,
                (period, start, end, period),
            )


def coverage(start: date, end: date) -> dict:
    """Which weeks in a date range the database holds, and which it does not.

    The missing list is the point. A summary or a trend that spans a gap is
    describing collection, not crime, and the only way to know is to ask what
    should be there.

    Weeks are taken to run Sunday to Saturday, which is how the report is
    published, and are enumerated from the first stored week's start date so
    the grid lines up with real report periods rather than with an arbitrary
    calendar week.
    """
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT report_period, period_start, period_end, incidents_stored
                  FROM report_weeks
                 WHERE period_end >= %s AND period_start <= %s
                 ORDER BY period_start
                """,
                (start, end),
            )
            present = [
                {
                    "report_period": r[0],
                    "period_start": r[1].isoformat(),
                    "period_end": r[2].isoformat(),
                    "incidents_stored": r[3],
                }
                for r in cur.fetchall()
            ]
            cur.execute("SELECT min(period_start) FROM report_weeks")
            anchor_row = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM incidents WHERE report_period IS NULL"
            )
            unattributed = cur.fetchone()[0]

    missing: list[str] = []
    if anchor_row:
        have = {p["period_start"] for p in present}
        cursor = anchor_row
        while cursor < start:
            cursor += timedelta(days=7)
        while cursor <= end:
            if cursor.isoformat() not in have:
                missing.append(
                    f"{cursor.strftime('%m/%d/%Y')} - "
                    f"{(cursor + timedelta(days=6)).strftime('%m/%d/%Y')}"
                )
            cursor += timedelta(days=7)

    weeks_expected = len(present) + len(missing)
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "weeks_present": len(present),
        "weeks_missing": len(missing),
        "complete": not missing,
        "incidents_stored": sum(p["incidents_stored"] for p in present),
        "coverage_statement": (
            f"{len(present)} of {weeks_expected} weeks in this range are stored."
            + ("" if not missing else f" Missing: {', '.join(missing)}.")
            + (
                ""
                if not unattributed
                else f" A further {unattributed} rows predate the report-period "
                "field and belong to no week."
            )
        ),
        "present": present,
        "missing": missing,
        "unattributed_rows": unattributed,
    }


def _reconciliation_gate(json_path: str) -> tuple[Optional[str], str]:
    """Refuse to store a week whose parse did not reconcile.

    Returns (refusal, note). A refusal is the message to return instead of
    storing; the note is appended to a successful store's report.

    A numbered district short of the total the report declares for it means
    rows were dropped, and a week quietly missing incidents is worse on a
    public map than a week that is late. That used to be a subagent's verdict
    which the main agent was asked to respect. It is arithmetic, so it is
    enforced here instead: there is no argument to be had with it, and no tool
    argument that turns it off.

    A missing summary is neither a pass nor a failure. It means nothing checked
    this JSON, and the store says so rather than implying a check that did not
    happen.
    """
    summary_path = _summary_path_for(json_path)
    try:
        with open(summary_path, "r") as f:
            summary = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, (
            f" NOTE: no parse summary at {os.path.basename(summary_path)}, so "
            "nothing verified this JSON against the report's district totals."
        )

    discrepancies = (summary.get("reconciliation") or {}).get("discrepancies") or []
    if not discrepancies:
        return None, ""

    detail = []
    for d in discrepancies:
        block = "\n".join(d.get("source_lines") or []) or "(source not recorded)"
        detail.append(
            f"District {d['district']}: the report declares {d['declared_total']}, "
            f"the parse found {d['parsed_total']}. The block reads:\n{block}"
        )
    return (
        "REFUSED: not storing this week. "
        f"{len(discrepancies)} numbered district(s) do not match the total the "
        "report declares for them, so rows were dropped. Publishing a week that "
        "is quietly missing incidents is worse than publishing it late — fix the "
        "parser and re-run.\n\n" + "\n\n".join(detail)
    ), ""


@tool
def store_incidents(
    json_path: str = INCIDENTS_JSON_PATH,
    short_description_map: Optional[dict[str, str]] = None,
    enriched_json_path: str = ENRICHED_JSON_PATH,
) -> str:
    """Label the incidents, append new ones to Postgres, and write the map feed.

    Every incident reaches the enriched JSON the map renders; only incidents the
    database already holds are skipped when appending to it. The database
    connection comes from the environment — you do not pass it.

    Labels come from the `incident_labels` table, not from this call. Anything
    passed in for a code that already has a label is ignored and reported: a
    code is named once and keeps that name, so the map's legend stays the same
    from week to week.

    Args:
        json_path: The parse output. Defaults to what parse_incidents wrote.
        short_description_map: Labels for codes that do not have one yet, as
            returned by `unlabelled_incident_types`, e.g.
            {"THEFT-MOTOR VEHICLE-$2,500 L/T $30,000": "Motor Vehicle Theft"}.
            Codes with no stored and no supplied label fall back to the verbose
            name from the PDF.
        enriched_json_path: Where to write the flat enriched list the
            geo-analysis step reads. Defaults to the run directory.
    """
    try:
        with open(json_path, "r") as f:
            incidents = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return f"Error reading incidents JSON at {json_path}: {e}"

    refusal, parse_note = _reconciliation_gate(json_path)
    if refusal:
        return refusal

    supplied = short_description_map or {}
    types = sorted({inc.get("incident", "") for inc in incidents} - {""})

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

        # Learn the codes we have never seen, then read every label back. What
        # gets written is what the table says, so a re-run cannot relabel a week
        # that is already published.
        learned, ignored = _learn_labels(conn, supplied)
        mapping = _stored_labels(conn, types)

        # The database is the append-only history; the enriched JSON is what the
        # map renders for the current week. These are deliberately different
        # sets: deduping the JSON too would blank the map on any re-run.
        enriched = []
        for inc in incidents:
            record = dict(inc)
            record["short_description"] = mapping.get(
                inc.get("incident", ""), inc.get("incident", "")
            )
            enriched.append(record)

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
        # Same transaction as the insert: coverage must never claim a week the
        # database does not actually hold.
        _record_weeks(conn, enriched)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM incidents")
            total = cur.fetchone()[0]

    skipped = len(enriched) - len(new_rows)
    unlabelled = [t for t in types if t not in mapping]

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
        f"Labelled {len(types)} incident types: {len(learned)} newly learned, "
        f"{len(types) - len(learned) - len(unlabelled)} reused from "
        f"incident_labels. "
        + (
            f"NOTE: kept the stored label for {len(ignored)} code(s) that were "
            f"supplied a different one: {ignored}. "
            if ignored
            else ""
        )
        + (
            f"WARNING: {len(unlabelled)} incident types had no label and fell "
            f"back to the verbose name: {unlabelled}"
            if unlabelled
            else "Every incident type was labelled."
        )
        + parse_note
    )
