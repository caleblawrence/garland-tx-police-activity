import json
import os
import re
from collections import Counter

import requests
from pypdf import PdfReader
from crewai.tools import BaseTool
from typing import Type, List, Optional, Dict
from pydantic import BaseModel, Field
from tinydb import TinyDB


class FileDownloadToolInput(BaseModel):
    url: str = Field(..., description="The URL of the file to download.")
    save_path: str = Field(..., description="The local path to save the downloaded file.")


# garlandtx.gov sits behind a filter that answers `404` with an empty body to
# clients sending the default `python-requests/x.y` User-Agent. The download
# then "failed" while the rest of the crew happily re-parsed whatever stale PDF
# was already on disk, so a run could publish months-old data and still report
# success. Send a browser UA and verify we actually got a PDF.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DOWNLOAD_TIMEOUT_SECONDS = 60


class FileDownloadTool(BaseTool):
    name: str = "download_tool"
    description: str = (
        "Downloads a file from a URL and saves it locally. Verifies the response "
        "is a real PDF and raises if the download failed, so the crew stops "
        "instead of silently reusing an older file."
    )
    args_schema: Type[BaseModel] = FileDownloadToolInput

    def _run(self, url: str, save_path: str) -> str:
        response = requests.get(
            url,
            headers={"User-Agent": BROWSER_USER_AGENT},
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        content = response.content
        if not content.startswith(b"%PDF-"):
            # A filter/error page returned with a 200 is worse than an HTTP
            # error: it would parse to zero incidents and look like a quiet week.
            raise ValueError(
                f"{url} returned {len(content)} bytes that are not a PDF "
                f"(content-type={response.headers.get('content-type')!r}). "
                "Refusing to overwrite the local file."
            )

        with open(save_path, "wb") as f:
            f.write(content)
        return (
            f"Downloaded {len(content)} bytes to {os.path.abspath(save_path)}."
        )


def _extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _find_report_period(lines: List[str]) -> Optional[str]:
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


def _parse_incidents(lines: List[str]) -> List[dict]:
    """Walk the lines once, tracking which district section we're in.

    The PDF lists incidents under `DISTRICT NN` headers. We assign each
    incident-bearing line to the most recent district header seen. This
    avoids the previous bug where a per-district flag was never reset
    and re-occurrences of a district header were silently skipped.

    Every district in the report is kept. There used to be a districts-of-
    interest filter here, but it was built from the districts found in the
    very same PDF and so matched all of them — a no-op that still read like
    a working feature.
    """
    incidents: List[dict] = []
    current_district: Optional[str] = None

    header_re = re.compile(r"DISTRICT (\d+)")
    date_re = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

    for line in lines:
        header_match = header_re.search(line)
        if header_match:
            current_district = header_match.group(1)
            continue
        if current_district is None:
            continue

        tokens = line.strip().split()
        if len(tokens) < 3:
            continue
        # Lines look like: " <row#> <report-id> INCIDENT-TYPE<DATE> <LOCATION>"
        body = " ".join(tokens[2:]).strip()
        date_match = date_re.search(body)
        if not date_match:
            continue
        incident_type, _, location = body.partition(date_match.group())
        incidents.append({
            "district": current_district,
            "date": date_match.group(),
            "incident": incident_type.strip(),
            "location": location.strip(),
        })

    return incidents


class PDFIncidentExtractorToolInput(BaseModel):
    pdf_path: str = Field(..., description="The local path to the PDF file.")
    output_json_path: str = Field(
        "extracted_incidents.json",
        description="Where to write the extracted incidents as JSON.",
    )


class PDFIncidentExtractorTool(BaseTool):
    name: str = "pdf_extraction_tool"
    description: str = (
        "Extracts every incident from a Garland police-incidents PDF and writes "
        "them to a JSON file. Returns a short summary including the file path, "
        "total count, per-district counts, and the unique incident types found."
    )
    args_schema: Type[BaseModel] = PDFIncidentExtractorToolInput

    def _run(self, pdf_path: str, output_json_path: str = "extracted_incidents.json") -> str:
        text = _extract_text(pdf_path)
        lines = text.split("\n")
        report_period = _find_report_period(lines)
        incidents = _parse_incidents(lines)

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

        per_district: Dict[str, int] = {}
        for inc in incidents:
            per_district[inc["district"]] = per_district.get(inc["district"], 0) + 1
        unique_types = sorted({inc["incident"] for inc in incidents})

        summary = {
            "json_path": os.path.abspath(output_json_path),
            "report_period": report_period,
            "total_incidents": len(incidents),
            "per_district_counts": per_district,
            "unique_incident_types": unique_types,
        }
        return json.dumps(summary, indent=2)


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
    repeat as a duplicate — see TinyDBWriterTool.
    """
    return (
        str(record.get("report_period", "")).strip(),
        str(record.get("district", "")).strip(),
        str(record.get("date", "")).strip(),
        str(record.get("incident", "")).strip(),
        str(record.get("location", "")).strip(),
    )


class TinyDBWriterToolInput(BaseModel):
    json_path: str = Field(..., description="Path to the JSON file produced by pdf_extraction_tool.")
    db_path: str = Field(..., description="Path to the TinyDB database file to append to.")
    short_description_map: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional mapping from verbose incident type (exact string from the PDF) "
            "to a concise human-friendly description, e.g. "
            '{"THEFT-MOTOR VEHICLE-$2,500 L/T $30,000": "Motor Vehicle Theft"}.'
        ),
    )
    enriched_json_path: str = Field(
        "enriched_incidents.json",
        description=(
            "Where to write a flat JSON list of incidents enriched with "
            "short_description. The geo-analysis step reads this file."
        ),
    )


class TinyDBWriterTool(BaseTool):
    name: str = "tinydb_writer_tool"
    description: str = (
        "Reads incidents from a JSON file, enriches each with a short_description "
        "using the provided mapping, appends any not-yet-seen incidents to a "
        "TinyDB database, and writes the full enriched list back to JSON for "
        "downstream tools (e.g. the map renderer). Every incident reaches the "
        "JSON; only exact duplicates are skipped when writing to the DB."
    )
    args_schema: Type[BaseModel] = TinyDBWriterToolInput

    def _run(
        self,
        json_path: str,
        db_path: str,
        short_description_map: Optional[Dict[str, str]] = None,
        enriched_json_path: str = "enriched_incidents.json",
    ) -> str:
        try:
            with open(json_path, "r") as f:
                incidents = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return f"Error reading incidents JSON at {json_path}: {e}"

        mapping = short_description_map or {}
        db = TinyDB(db_path)
        existing = db.all()

        # The DB is the append-only history; the enriched JSON is what the map
        # renders for the current week. These are deliberately different sets:
        # deduping the JSON too would blank the map on any re-run.
        enriched = []
        for inc in incidents:
            record = dict(inc)
            record["short_description"] = mapping.get(
                inc.get("incident", ""), inc.get("incident", "")
            )
            enriched.append(record)

        # The writer used to append unconditionally, so re-running the crew in the
        # same week (or after a failed download served up last week's PDF) stacked
        # the same incidents into the DB over and over — that is where ~30% of the
        # existing rows came from.
        #
        # Compare per-key *counts* rather than mere presence: a report can hold
        # several rows sharing a natural key, and those are distinct incidents we
        # must keep. Only the surplus beyond what the DB already holds is new.
        already = Counter(_incident_key(row) for row in existing)
        incoming = Counter(_incident_key(row) for row in enriched)
        to_insert = {k: max(0, n - already[k]) for k, n in incoming.items()}

        next_id = max((row.get("incident_id") or 0) for row in existing) + 1 if existing else 1
        new_rows = []
        for record in enriched:
            key = _incident_key(record)
            if to_insert.get(key, 0) <= 0:
                continue
            to_insert[key] -= 1
            stored = dict(record)
            stored["incident_id"] = next_id
            next_id += 1
            new_rows.append(stored)

        if new_rows:
            db.insert_multiple(new_rows)
        total = len(db.all())
        skipped = len(enriched) - len(new_rows)

        try:
            os.makedirs(os.path.dirname(os.path.abspath(enriched_json_path)) or ".", exist_ok=True)
            with open(enriched_json_path, "w") as f:
                json.dump(enriched, f, indent=2)
        except OSError as e:
            return (
                f"Inserted {len(new_rows)} into {db_path} (now {total} total) "
                f"but failed to write enriched JSON to {enriched_json_path}: {e}"
            )

        return (
            f"Read {len(incidents)} incidents from {json_path}. "
            f"Inserted {len(new_rows)} new into {db_path} "
            f"(skipped {skipped} already-present duplicates). "
            f"Wrote all {len(enriched)} enriched incidents to "
            f"{os.path.abspath(enriched_json_path)} for the map. "
            f"DB now contains {total} total records."
        )
