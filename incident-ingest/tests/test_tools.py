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
    unlabelled_incident_types,
)

PDF_PATH = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "weekly_report.pdf",
)

# The 07/26/2026 report: the first one carrying an offence name long enough to
# wrap, which is the case weekly_report.pdf does not contain.
WRAPPED_ROW_PDF_PATH = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "weekly_report_wrapped_row.pdf",
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


def test_a_clean_parse_does_not_ask_for_an_audit():
    """`audit_required` is the whole stop/go decision, and it is arithmetic.

    The fixture reconciles against every district total it declares, so no
    reasoning call is warranted — a clean week should cost zero LLM audits.
    """
    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )
    rec = summary["reconciliation"]

    assert rec["discrepancies"] == []
    assert rec["audit_required"] is False
    assert "matches its declared total" in rec["summary"]
    # Rows under an unnumbered header are a known, by-design loss. They must
    # not drag a clean week into an audit.
    assert rec["unnumbered_district_rows"] > 0


def test_a_district_that_does_not_reconcile_asks_for_an_audit(monkeypatch):
    """A district short of its declared total is exactly what the auditor is for.

    This is the shape of the real 07/26/2026 failure: district 51 declared 6
    and parsed 5. That particular cause — a wrapped offence name — is handled
    now, so what reaches this state is a row the parser still cannot read.
    """
    page = "\n".join(
        [
            "Reported Between 05/03/2026 & 05/09/2026",
            " DISTRICT 51",
            " 1 11442026R036639 BURGLARY-VEH05/03/2026 8XX HUDSON DR",
            "District Total: 2",
        ]
    )
    monkeypatch.setattr(tools, "_extract_text", lambda _: page)

    summary = json.loads(
        parse_incidents.invoke({"pdf_path": "ignored.pdf", "output_json_path": os.devnull})
    )
    rec = summary["reconciliation"]

    assert rec["audit_required"] is True
    assert rec["discrepancies"] == [
        {"district": "51", "declared_total": 2, "parsed_total": 1, "missing": 1}
    ]
    assert "audit before storing" in rec["summary"]


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


def test_parser_recovers_a_row_whose_offence_name_wrapped():
    """A long offence name is emitted across several lines, date on none of them.

    District 51 of the 07/26/2026 report declared 6 and parsed 5 for exactly
    this reason: a $20,000 metal theft whose description wrapped was dropped
    without a trace, and only the district total showed it had gone.
    """
    lines = [
        " DISTRICT 51",
        " 148 11442026R036639 BURGLARY-VEH-(CRIM ATT)07/26/2026 8XX HUDSON DR",
        " 148 02062026R036876 THEFT-MATERIAL ALUMINUM/ BRONZE/ COPPER/ BRASS L/T ",
        "$20,000",
        "07/30/2026 10XX W CENTERVILLE RD",
        "District Total: 2",
    ]

    incidents, sections = tools._parse_report(lines)

    assert sections == [{"district": "51", "declared_total": 2, "parsed_total": 2}]
    assert incidents[1] == {
        "district": "51",
        "date": "07/30/2026",
        "incident": "THEFT-MATERIAL ALUMINUM/ BRONZE/ COPPER/ BRASS L/T $20,000",
        "location": "10XX W CENTERVILLE RD",
    }


def test_a_wrapped_row_is_abandoned_at_a_page_break_not_completed_by_the_print_date():
    """Page furniture must never finish a pending row.

    The report reprints a footer, the print date and the column header on every
    page. The print date is a date, so pasting it onto a half-built row would
    manufacture an incident that never happened — dated to the day the report
    was printed. Losing the row instead is caught by the district total; a
    fabricated one would be published as fact.
    """
    lines = [
        " DISTRICT 51",
        " 148 02062026R036876 THEFT-MATERIAL ALUMINUM/ BRONZE/ COPPER/ BRASS L/T ",
        "$20,000",
        "Page 4 of 6Prepared by:  Crime Analysis",
        "08/03/2026",
        "ADDRESSOFFENSECASE#BEAT",
        "07/30/2026 10XX W CENTERVILLE RD",
        "District Total: 1",
    ]

    incidents, sections = tools._parse_report(lines)

    assert incidents == [], "a page break must abandon the row, not complete it"
    assert sections == [{"district": "51", "declared_total": 1, "parsed_total": 0}], (
        "and the loss must show up against the declared total"
    )


def test_an_unfinished_row_does_not_swallow_the_one_after_it():
    """A row that never finds its date is dropped when the next row opens."""
    lines = [
        " DISTRICT 22",
        " 1 11442026R036639 THEFT-MATERIAL ALUMINUM/ BRONZE/ COPPER/ BRASS L/T ",
        " 2 22002026R036021 BURGLARY-VEH07/16/2026 23XX APOLLO RD",
        "District Total: 2",
    ]

    incidents, sections = tools._parse_report(lines)

    assert incidents == [
        {
            "district": "22",
            "date": "07/16/2026",
            "incident": "BURGLARY-VEH",
            "location": "23XX APOLLO RD",
        }
    ], "the abandoned row's text must not leak into the row that follows it"
    assert sections == [{"district": "22", "declared_total": 2, "parsed_total": 1}]


def test_wrapped_rows_reconcile_end_to_end():
    """The 07/26/2026 report, which is what found this bug.

    The synthetic cases above pin the logic; this pins that pypdf really does
    hand us the lines they describe.
    """
    summary = json.loads(
        parse_incidents.invoke(
            {"pdf_path": WRAPPED_ROW_PDF_PATH, "output_json_path": os.devnull}
        )
    )

    assert summary["report_period"] == "07/26/2026 - 08/01/2026"
    assert summary["reconciliation"]["audit_required"] is False
    assert summary["reconciliation"]["discrepancies"] == []
    assert summary["total_incidents"] == 100


def test_period_check_reports_unknown_without_a_database():
    """No database is not the same answer as nothing stored.

    A run that cannot reach Postgres must not read as "this week is new" —
    that is how a stale week gets published. It says so instead.
    """
    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )

    check = summary["period_check"]
    assert check["status"] == "unknown"
    assert check["already_stored_rows"] is None
    assert check["stale_download_suspected"] is False


def test_period_check_reports_a_week_the_database_has_never_seen(db):
    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )

    assert summary["period_check"]["status"] == "new"
    assert summary["period_check"]["already_stored_rows"] == 0


def test_period_check_flags_a_week_that_is_already_stored(db, tmp_path):
    """The stale-download signal: this week is in the database, all of it.

    Step 2 of the agent's prompt used to ask it to notice this. It never could
    — nothing in its toolset reads the database — so the check lives here.
    """
    out_json = tmp_path / "incidents.json"
    parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": str(out_json)})
    store_incidents.invoke({
        "json_path": str(out_json),
        "enriched_json_path": str(tmp_path / "enriched.json"),
    })

    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )

    check = summary["period_check"]
    assert check["status"] == "already-stored"
    assert check["stale_download_suspected"] is True
    assert check["already_stored_rows"] == check["parsed_rows"]


def test_period_check_distinguishes_a_half_finished_run_from_a_stale_download(db, tmp_path):
    """Both leave rows for this period behind; only one must stop the run.

    A run that stored some of the week and died has to be allowed to finish,
    or the week can never be completed.
    """
    out_json = tmp_path / "incidents.json"
    parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": str(out_json)})
    with open(out_json) as f:
        rows = json.load(f)

    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps(rows[:10]))
    store_incidents.invoke({
        "json_path": str(partial),
        "enriched_json_path": str(tmp_path / "enriched.json"),
    })

    summary = json.loads(
        parse_incidents.invoke({"pdf_path": PDF_PATH, "output_json_path": os.devnull})
    )

    check = summary["period_check"]
    assert check["status"] == "partially-stored"
    assert check["stale_download_suspected"] is False
    assert check["already_stored_rows"] == 10


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


def test_a_label_survives_a_rerun_that_supplies_a_different_one(db, tmp_path):
    """The bug this table exists for.

    Re-deriving labels every week gave 68 offence codes 94 different labels —
    the same code read as "Vandalism" one week and "Criminal Mischief
    ($100-$750)" the next, so the map's legend never settled. A code is named
    once; a later run offering a different name is ignored, and told so.
    """
    incidents = tmp_path / "incidents.json"
    incidents.write_text(json.dumps(_rows()))

    store_incidents.invoke({
        "json_path": str(incidents),
        "short_description_map": {"BURGLARY-VEH": "Vehicle Burglary"},
        "enriched_json_path": str(tmp_path / "a.json"),
    })
    msg = store_incidents.invoke({
        "json_path": str(incidents),
        "short_description_map": {"BURGLARY-VEH": "Burglary of a Vehicle"},
        "enriched_json_path": str(tmp_path / "b.json"),
    })

    assert "kept the stored label" in msg and "BURGLARY-VEH" in msg
    assert [r["short_description"] for r in fetch_incidents()] == ["Vehicle Burglary"]
    with open(tmp_path / "b.json") as f:
        assert [r["short_description"] for r in json.load(f)] == ["Vehicle Burglary"]


def test_a_stored_label_applies_without_being_supplied_again(db, tmp_path):
    """Once a code is named, later weeks need not ask the model about it."""
    first = tmp_path / "first.json"
    first.write_text(json.dumps(_rows(period="05/03/2026 - 05/09/2026", day="05/03/2026")))
    store_incidents.invoke({
        "json_path": str(first),
        "short_description_map": {"BURGLARY-VEH": "Vehicle Burglary"},
        "enriched_json_path": str(tmp_path / "a.json"),
    })

    second = tmp_path / "second.json"
    second.write_text(json.dumps(_rows(period="05/10/2026 - 05/16/2026", day="05/10/2026")))
    msg = store_incidents.invoke({
        "json_path": str(second),
        "enriched_json_path": str(tmp_path / "b.json"),
    })

    assert "WARNING" not in msg, "the stored label should cover it with nothing supplied"
    assert "1 reused from incident_labels" in msg
    assert {r["short_description"] for r in fetch_incidents()} == {"Vehicle Burglary"}


def test_unlabelled_types_reports_only_what_the_model_still_has_to_name(db, tmp_path):
    """The list sent to the labeller shrinks to nothing as codes are learned."""
    incidents = tmp_path / "incidents.json"
    rows = _rows() + [dict(_rows()[0], incident="THEFT-MAIL <10 ADDRESSES")]
    incidents.write_text(json.dumps(rows))

    before = json.loads(unlabelled_incident_types.invoke({"json_path": str(incidents)}))
    assert before["types_in_report"] == 2
    assert before["needing_labels"] == ["BURGLARY-VEH", "THEFT-MAIL <10 ADDRESSES"]

    store_incidents.invoke({
        "json_path": str(incidents),
        "short_description_map": {
            "BURGLARY-VEH": "Vehicle Burglary",
            "THEFT-MAIL <10 ADDRESSES": "Mail Theft",
        },
        "enriched_json_path": str(tmp_path / "a.json"),
    })

    after = json.loads(unlabelled_incident_types.invoke({"json_path": str(incidents)}))
    assert after["needing_labels"] == [], "nothing left for the labeller to do"
    assert after["already_labelled"] == 2


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


def test_backfill_settles_a_drifted_code_on_one_label(db, tmp_path):
    """The cleanup for the 30 codes that had already drifted.

    Also pins that the backfill writes where DATABASE_URL points. It must not
    reload .env — doing so would send a test aimed at the Neon `test` branch
    into the live database instead.
    """
    from garland_tx_data_analysis import backfill_labels

    incidents = tmp_path / "incidents.json"
    incidents.write_text(json.dumps(_rows()))
    store_incidents.invoke({
        "json_path": str(incidents),
        "short_description_map": {"BURGLARY-VEH": "Burglary of a Vehicle"},
        "enriched_json_path": str(tmp_path / "a.json"),
    })
    assert [r["short_description"] for r in fetch_incidents()] == ["Burglary of a Vehicle"]

    backfill_labels.main(apply=True)

    canonical = backfill_labels.CANONICAL_LABELS["BURGLARY-VEH"]
    assert canonical == "Vehicle Burglary"
    assert [r["short_description"] for r in fetch_incidents()] == [canonical]


def test_backfill_dry_run_changes_nothing(db, tmp_path):
    from garland_tx_data_analysis import backfill_labels

    incidents = tmp_path / "incidents.json"
    incidents.write_text(json.dumps(_rows()))
    store_incidents.invoke({
        "json_path": str(incidents),
        "short_description_map": {"BURGLARY-VEH": "Burglary of a Vehicle"},
        "enriched_json_path": str(tmp_path / "a.json"),
    })

    backfill_labels.main()

    assert [r["short_description"] for r in fetch_incidents()] == ["Burglary of a Vehicle"]


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
