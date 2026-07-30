import json
import os

import pytest
import requests

from garland_tx_data_analysis.tools import custom_tool
from garland_tx_data_analysis.tools.custom_tool import (
    FileDownloadTool,
    PDFIncidentExtractorTool,
    TinyDBWriterTool,
)


PDF_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "police_incidents_report.pdf",
)


def test_pdf_extractor_writes_full_incident_list(tmp_path):
    """The extractor must persist every incident to JSON, not relay them via the agent."""
    out_json = tmp_path / "incidents.json"
    tool = PDFIncidentExtractorTool()

    summary_str = tool._run(pdf_path=PDF_PATH, output_json_path=str(out_json))
    summary = json.loads(summary_str)

    assert summary["json_path"].endswith("incidents.json")
    assert summary["total_incidents"] > 100, (
        f"Expected >100 incidents for the test PDF, got {summary['total_incidents']}"
    )
    assert summary["unique_incident_types"], "Should report unique incident types"

    with open(out_json) as f:
        incidents = json.load(f)
    assert len(incidents) == summary["total_incidents"]
    for inc in incidents:
        assert {"district", "date", "incident", "location"} <= inc.keys()


def test_tinydb_writer_stores_every_row_with_mapping(tmp_path):
    out_json = tmp_path / "incidents.json"
    db_path = tmp_path / "incidents.db"

    extractor = PDFIncidentExtractorTool()
    summary = json.loads(extractor._run(pdf_path=PDF_PATH, output_json_path=str(out_json)))

    mapping = {t: t.split("-")[0].title() for t in summary["unique_incident_types"]}

    writer = TinyDBWriterTool()
    msg = writer._run(
        json_path=str(out_json),
        db_path=str(db_path),
        short_description_map=mapping,
    )
    assert f"Inserted {summary['total_incidents']}" in msg

    from tinydb import TinyDB
    db = TinyDB(str(db_path))
    rows = db.all()
    assert len(rows) == summary["total_incidents"]
    assert all("short_description" in r and "incident_id" in r for r in rows)


class _FakeResponse:
    def __init__(self, content=b"", status=200, headers=None):
        self.content = content
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error")


def test_download_sends_browser_user_agent(monkeypatch, tmp_path):
    """garlandtx.gov 404s the default python-requests UA, which used to make the
    crew silently fall back to whatever stale PDF was already on disk."""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers or {}
        captured["timeout"] = timeout
        return _FakeResponse(b"%PDF-1.4 fake", headers={"content-type": "application/pdf"})

    monkeypatch.setattr(custom_tool.requests, "get", fake_get)

    dest = tmp_path / "out.pdf"
    FileDownloadTool()._run(url="https://example.test/doc.pdf", save_path=str(dest))

    assert "Mozilla/5.0" in captured["headers"].get("User-Agent", "")
    assert captured["timeout"], "download must not hang without a timeout"
    assert dest.read_bytes().startswith(b"%PDF-")


def test_download_refuses_to_overwrite_with_non_pdf(monkeypatch, tmp_path):
    """A filter page served with a 200 must not clobber a good PDF."""
    monkeypatch.setattr(
        custom_tool.requests,
        "get",
        lambda url, headers=None, timeout=None: _FakeResponse(
            b"<html>blocked</html>", headers={"content-type": "text/html"}
        ),
    )

    dest = tmp_path / "out.pdf"
    dest.write_bytes(b"%PDF-1.4 previously good")

    with pytest.raises(ValueError, match="not a PDF"):
        FileDownloadTool()._run(url="https://example.test/doc.pdf", save_path=str(dest))
    assert dest.read_bytes() == b"%PDF-1.4 previously good"


def test_download_raises_on_http_error(monkeypatch, tmp_path):
    """Failure must be loud — returning an error string let the run continue."""
    monkeypatch.setattr(
        custom_tool.requests,
        "get",
        lambda url, headers=None, timeout=None: _FakeResponse(b"", status=404),
    )

    with pytest.raises(requests.exceptions.HTTPError):
        FileDownloadTool()._run(
            url="https://example.test/doc.pdf", save_path=str(tmp_path / "out.pdf")
        )


def test_extractor_reports_report_period():
    """The period makes a stale download visible in the run output."""
    tool = PDFIncidentExtractorTool()
    summary = json.loads(tool._run(pdf_path=PDF_PATH, output_json_path=os.devnull))
    assert summary["report_period"], "report period should be parsed off page 1"
    assert " - " in summary["report_period"]


def test_writer_is_idempotent_across_reruns(tmp_path):
    """Re-running the crew on the same report must not restack rows in the DB."""
    out_json = tmp_path / "incidents.json"
    db_path = tmp_path / "incidents.db"
    enriched_json = tmp_path / "enriched.json"

    extractor = PDFIncidentExtractorTool()
    summary = json.loads(
        extractor._run(pdf_path=PDF_PATH, output_json_path=str(out_json))
    )
    total = summary["total_incidents"]

    writer = TinyDBWriterTool()
    first = writer._run(
        json_path=str(out_json),
        db_path=str(db_path),
        enriched_json_path=str(enriched_json),
    )
    assert f"Inserted {total} new" in first

    second = writer._run(
        json_path=str(out_json),
        db_path=str(db_path),
        enriched_json_path=str(enriched_json),
    )
    assert "Inserted 0 new" in second

    from tinydb import TinyDB

    assert len(TinyDB(str(db_path)).all()) == total, "second run must add nothing"
    # The map reads the enriched JSON, so it must still hold the whole week even
    # when the DB skipped everything as already-present.
    assert len(json.load(open(enriched_json))) == total


def test_writer_keeps_same_key_incidents_within_one_report(tmp_path):
    """A week legitimately contains repeat 'same block, same day, same offense'
    rows; those are distinct incidents, not duplicates to collapse."""
    rows = [
        {
            "district": "22",
            "date": "05/03/2026",
            "incident": "BURGLARY-VEH",
            "location": "22XX KNIGHTHOOD LN",
            "report_period": "05/03/2026 - 05/09/2026",
        }
    ] * 3
    src = tmp_path / "in.json"
    src.write_text(json.dumps(rows))

    db_path = tmp_path / "i.db"
    writer = TinyDBWriterTool()
    msg = writer._run(
        json_path=str(src),
        db_path=str(db_path),
        enriched_json_path=str(tmp_path / "e.json"),
    )
    assert "Inserted 3 new" in msg

    from tinydb import TinyDB

    stored = TinyDB(str(db_path)).all()
    assert len(stored) == 3
    assert len({r["incident_id"] for r in stored}) == 3, "ids must stay unique"
