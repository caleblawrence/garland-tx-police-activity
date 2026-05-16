import json
import os

from garland_tx_data_analysis.tools.custom_tool import (
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
