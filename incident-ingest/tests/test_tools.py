import json
import os
from datetime import date

import pytest
import requests
from conftest import fetch_incidents

from garland_tx_data_analysis import tools
from garland_tx_data_analysis.tools import (
    download_weekly_report,
    parse_incidents,
    read_report_text,
    store_incidents,
)

PDF_PATH = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "weekly_report.pdf",
)


def _rows(period: str = "05/03/2026 - 05/09/2026", day: str = "05/03/2026") -> list[dict]:
    """One incident, in the shape parse_incidents writes."""
    return [
        {
            "district": "22",
            "date": day,
            "incident": "BURGLARY-VEH",
            "location": "22XX KNIGHTHOOD LN",
            "report_period": period,
        }
    ]


def test_parser_writes_full_incident_list(tmp_path):
    """The parser must persist every incident to JSON, not relay them via the agent."""
    out_json = tmp_path / "incidents.json"

    summary = json.loads(
        parse_incidents.invoke(
            {"pdf_path": PDF_PATH, "output_json_path": str(out_json)}
        )
    )

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


def test_parser_reconciles_against_the_reports_own_district_totals():
    """The PDF declares `District Total: N` per block; the parse is checked against it.

    This is the signal the extraction auditor works from, so the numbers have to
    add up: every row the parser saw is either stored under a numbered district
    or counted as belonging to an unnumbered one.
    """
    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )
    rec = summary["reconciliation"]

    assert rec["sections"], "each district block should be reconciled"
    seen = sum(s["parsed_total"] for s in rec["sections"])
    assert seen == rec["stored_incidents"] + rec["unnumbered_district_rows"], (
        "every parsed row must be accounted for as stored or unattributable"
    )
    assert rec["declared_total_all_districts"] >= rec["stored_incidents"]
    for d in rec["discrepancies"]:
        assert d["declared_total"] != d["parsed_total"]


def test_rows_under_an_unnumbered_district_header_are_counted_not_silently_dropped():
    """The report's first block is headed by a bare `DISTRICT` with no number.

    Those rows cannot be attributed and are dropped — as they always were. What
    changed is that the drop is now reported, so a run can say how much of the
    week it is missing instead of looking complete.
    """
    lines = [
        " DISTRICT ",
        " 0 01502026R035903 BURGLARY-VEH07/14/2026 51XX LONGHORN TRAIL",
        "District Total: 1",
        " DISTRICT 21",
        " 1 08002026R035839 INFO-IDENTITY THEFT07/13/2026 66XX LAKE SHORE DR",
        "District Total: 1",
    ]

    incidents, sections = tools._parse_report(lines)

    assert [i["district"] for i in incidents] == ["21"], (
        "unattributable rows must not leak into a neighbouring district"
    )
    assert sections == [
        {"district": None, "declared_total": 1, "parsed_total": 1},
        {"district": "21", "declared_total": 1, "parsed_total": 1},
    ]


def test_parser_reports_report_period():
    """The period makes a stale download visible in the run output."""
    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )
    assert summary["report_period"], "report period should be parsed off page 1"
    assert " - " in summary["report_period"]


def test_read_report_text_exposes_the_source_to_the_auditor():
    """The auditor reads the source rather than trusting the parse summary."""
    page = read_report_text.invoke({"pdf_path": PDF_PATH, "page": 1})
    assert "DISTRICT" in page

    with pytest.raises(ValueError, match="out of range"):
        read_report_text.invoke({"pdf_path": PDF_PATH, "page": 999})


def test_store_writes_every_row_with_mapping(db, tmp_path):
    out_json = tmp_path / "incidents.json"

    summary = json.loads(
        parse_incidents.invoke(
            {"pdf_path": PDF_PATH, "output_json_path": str(out_json)}
        )
    )
    mapping = {t: t.split("-")[0].title() for t in summary["unique_incident_types"]}

    msg = store_incidents.invoke(
        {
            "json_path": str(out_json),
            "short_description_map": mapping,
            # Without this the default relative path writes into the project
            # directory and overwrites the real enriched_incidents.json.
            "enriched_json_path": str(tmp_path / "enriched.json"),
        }
    )
    assert f"Inserted {summary['total_incidents']}" in msg
    assert "Every incident type was labelled." in msg

    rows = fetch_incidents()
    assert len(rows) == summary["total_incidents"]
    assert all(r["short_description"] and r["incident_id"] for r in rows)
    assert all(isinstance(r["occurred_on"], date) for r in rows), (
        "dates belong in a date column, not as MM/DD/YYYY text"
    )


def test_store_warns_when_an_incident_type_has_no_label(db, tmp_path):
    """An unlabelled type reaches the public map as a raw offence code."""
    src = tmp_path / "in.json"
    src.write_text(json.dumps(_rows()))

    msg = store_incidents.invoke(
        {"json_path": str(src), "enriched_json_path": str(tmp_path / "e.json")}
    )
    assert "WARNING" in msg and "BURGLARY-VEH" in msg


def test_store_is_idempotent_across_reruns(db, tmp_path):
    """Re-running on the same report must not restack rows in the database."""
    out_json = tmp_path / "incidents.json"
    enriched_json = tmp_path / "enriched.json"

    summary = json.loads(
        parse_incidents.invoke(
            {"pdf_path": PDF_PATH, "output_json_path": str(out_json)}
        )
    )
    total = summary["total_incidents"]

    args = {
        "json_path": str(out_json),
        "enriched_json_path": str(enriched_json),
    }
    assert f"Inserted {total} new" in store_incidents.invoke(args)
    assert "Inserted 0 new" in store_incidents.invoke(args)

    assert len(fetch_incidents()) == total, "second run must add nothing"
    # The map reads the enriched JSON, so it must still hold the whole week even
    # when the database skipped everything as already-present.
    assert len(json.load(open(enriched_json))) == total


def test_store_keeps_same_key_incidents_within_one_report(db, tmp_path):
    """A week legitimately contains repeat 'same block, same day, same offense'
    rows; those are distinct incidents, not duplicates to collapse.

    This is why the incidents table has no unique constraint over the natural
    key — a constraint would silently swallow two of these three.
    """
    src = tmp_path / "in.json"
    src.write_text(json.dumps(_rows() * 3))

    msg = store_incidents.invoke(
        {"json_path": str(src), "enriched_json_path": str(tmp_path / "e.json")}
    )
    assert "Inserted 3 new" in msg

    stored = fetch_incidents()
    assert len(stored) == 3
    assert len({r["incident_id"] for r in stored}) == 3, "ids must stay unique"


def test_store_does_not_confuse_weeks_that_share_a_natural_key(db, tmp_path):
    """The report period is part of the key, so the same block and offence
    recurring in a later week is a new incident, not a duplicate."""
    week_one = tmp_path / "w1.json"
    week_one.write_text(json.dumps(_rows()))
    week_two = tmp_path / "w2.json"
    week_two.write_text(
        json.dumps(_rows(period="05/10/2026 - 05/16/2026", day="05/12/2026"))
    )

    store_incidents.invoke(
        {"json_path": str(week_one), "enriched_json_path": str(tmp_path / "e1.json")}
    )
    msg = store_incidents.invoke(
        {"json_path": str(week_two), "enriched_json_path": str(tmp_path / "e2.json")}
    )

    assert "Inserted 1 new" in msg
    assert len(fetch_incidents()) == 2


def test_store_writes_null_not_the_string_None_when_a_period_is_missing(db, tmp_path):
    """Pre-2023 report layouts have no `Reported Between` header, so the parser
    stamps report_period=None on every row.

    `.get("report_period", "")` returns its default only when the key is
    absent — here it is present and None, so the naive version stored the
    literal text "None" as the report period.
    """
    rows = _rows()
    rows[0]["report_period"] = None
    src = tmp_path / "in.json"
    src.write_text(json.dumps(rows))

    store_incidents.invoke(
        {"json_path": str(src), "enriched_json_path": str(tmp_path / "e.json")}
    )

    stored = fetch_incidents()
    assert len(stored) == 1
    assert stored[0]["report_period"] is None, (
        f"expected NULL, got {stored[0]['report_period']!r}"
    )

    # And it must still dedupe against itself on a second pass.
    store_incidents.invoke(
        {"json_path": str(src), "enriched_json_path": str(tmp_path / "e2.json")}
    )
    assert len(fetch_incidents()) == 1, "a period-less report must not restack"


def test_store_default_paths_stay_inside_cwd(db, tmp_path):
    """The store tool's default enriched_json_path is a bare relative filename.

    A test that forgot to override it overwrote the project's real
    enriched_incidents.json with this suite's December 2025 fixture data. The
    autouse tmp-cwd fixture keeps that contained; this pins the behaviour.
    """
    src = tmp_path / "in.json"
    src.write_text(json.dumps(_rows()))

    store_incidents.invoke({"json_path": str(src)})

    written = os.path.join(os.getcwd(), "work", "enriched_incidents.json")
    assert os.path.exists(written), "default write should land in the cwd"
    # The cwd is the per-test tmp dir, never the checked-out project.
    assert os.path.realpath(os.getcwd()) != os.path.realpath(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


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
    run silently fall back to whatever stale PDF was already on disk."""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers or {}
        captured["timeout"] = timeout
        return _FakeResponse(b"%PDF-1.4 fake", headers={"content-type": "application/pdf"})

    monkeypatch.setattr(tools.requests, "get", fake_get)

    dest = tmp_path / "out.pdf"
    download_weekly_report.invoke(
        {"url": "https://example.test/doc.pdf", "save_path": str(dest)}
    )

    assert "Mozilla/5.0" in captured["headers"].get("User-Agent", "")
    assert captured["timeout"], "download must not hang without a timeout"
    assert dest.read_bytes().startswith(b"%PDF-")


def test_download_refuses_to_overwrite_with_non_pdf(monkeypatch, tmp_path):
    """A filter page served with a 200 must not clobber a good PDF."""
    monkeypatch.setattr(
        tools.requests,
        "get",
        lambda url, headers=None, timeout=None: _FakeResponse(
            b"<html>blocked</html>", headers={"content-type": "text/html"}
        ),
    )

    dest = tmp_path / "out.pdf"
    dest.write_bytes(b"%PDF-1.4 previously good")

    with pytest.raises(ValueError, match="not a PDF"):
        download_weekly_report.invoke(
            {"url": "https://example.test/doc.pdf", "save_path": str(dest)}
        )
    assert dest.read_bytes() == b"%PDF-1.4 previously good"


def test_download_raises_on_http_error(monkeypatch, tmp_path):
    """Failure must be loud — returning an error string let the run continue."""
    monkeypatch.setattr(
        tools.requests,
        "get",
        lambda url, headers=None, timeout=None: _FakeResponse(b"", status=404),
    )

    with pytest.raises(requests.exceptions.HTTPError):
        download_weekly_report.invoke(
            {"url": "https://example.test/doc.pdf", "save_path": str(tmp_path / "out.pdf")}
        )
