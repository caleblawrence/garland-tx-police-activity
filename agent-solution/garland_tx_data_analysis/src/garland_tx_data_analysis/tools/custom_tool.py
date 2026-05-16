import json
import os
import re
import requests
from pypdf import PdfReader
from crewai.tools import BaseTool
from typing import Type, List, Optional, Dict
from pydantic import BaseModel, Field
from tinydb import TinyDB


class FileDownloadToolInput(BaseModel):
    url: str = Field(..., description="The URL of the file to download.")
    save_path: str = Field(..., description="The local path to save the downloaded file.")


class FileDownloadTool(BaseTool):
    name: str = "download_tool"
    description: str = "Downloads a file from a URL and saves it locally."
    args_schema: Type[BaseModel] = FileDownloadToolInput

    def _run(self, url: str, save_path: str) -> str:
        try:
            response = requests.get(url)
            response.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(response.content)
            return f"File downloaded successfully and saved at {save_path}"
        except requests.exceptions.RequestException as e:
            return f"Error downloading file: {e}"


def _extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _find_districts(lines: List[str]) -> List[int]:
    seen = []
    for line in lines:
        m = re.search(r"DISTRICT (\d+)", line)
        if m:
            seen.append(int(m.group(1)))
    return sorted(set(seen))


def _parse_incidents(lines: List[str], districts: List[int]) -> List[dict]:
    """Walk the lines once, tracking which district section we're in.

    The PDF lists incidents under `DISTRICT NN` headers. We assign each
    incident-bearing line to the most recent district header seen. This
    avoids the previous bug where a per-district flag was never reset
    and re-occurrences of a district header were silently skipped.
    """
    wanted = {str(d) for d in districts}
    incidents: List[dict] = []
    current_district: Optional[str] = None

    header_re = re.compile(r"DISTRICT (\d+)")
    date_re = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

    for line in lines:
        header_match = header_re.search(line)
        if header_match:
            current_district = header_match.group(1)
            continue
        if current_district is None or current_district not in wanted:
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
        districts = _find_districts(lines)
        incidents = _parse_incidents(lines, districts)

        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)) or ".", exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(incidents, f, indent=2)

        per_district: Dict[str, int] = {}
        for inc in incidents:
            per_district[inc["district"]] = per_district.get(inc["district"], 0) + 1
        unique_types = sorted({inc["incident"] for inc in incidents})

        summary = {
            "json_path": os.path.abspath(output_json_path),
            "total_incidents": len(incidents),
            "per_district_counts": per_district,
            "unique_incident_types": unique_types,
        }
        return json.dumps(summary, indent=2)


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
        "using the provided mapping, appends them to a TinyDB database, and writes "
        "the enriched list back to JSON for downstream tools (e.g. the map "
        "renderer). Stores every record — no filtering or batching."
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
        existing_count = len(db.all())

        enriched = []
        for i, inc in enumerate(incidents, start=1):
            record = dict(inc)
            record["incident_id"] = existing_count + i
            record["short_description"] = mapping.get(inc.get("incident", ""), inc.get("incident", ""))
            enriched.append(record)

        db.insert_multiple(enriched)
        total = len(db.all())

        try:
            os.makedirs(os.path.dirname(os.path.abspath(enriched_json_path)) or ".", exist_ok=True)
            with open(enriched_json_path, "w") as f:
                json.dump(enriched, f, indent=2)
        except OSError as e:
            return (
                f"Inserted {len(enriched)} into {db_path} (now {total} total) "
                f"but failed to write enriched JSON to {enriched_json_path}: {e}"
            )

        return (
            f"Read {len(incidents)} incidents from {json_path}. "
            f"Inserted {len(enriched)} into {db_path}. "
            f"Wrote enriched list to {os.path.abspath(enriched_json_path)}. "
            f"DB now contains {total} total records."
        )
