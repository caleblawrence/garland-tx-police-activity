"""Hand-written summaries must survive the worksheet being rebuilt.

These are the most expensive artefact in the project — a person reading a
month's figures and writing the paragraph they want — and an earlier version of
the generator rewrote the worksheet in place and destroyed one. Twice. Every
test here is a way that happened or nearly happened.
"""

import json

from garland_tx_data_analysis.gold_worksheet import read_answers

REAL = ("The biggest incident for Garland police in February 2022 was a "
        "manslaughter down in south-central Garland on Vista Drive. Aside from "
        "that, theft was the main issue with 215 cases.")


def _sheet(tmp_path, answer):
    p = tmp_path / "gold_worksheet.md"
    p.write_text(
        "# Gold summaries — worksheet\n\n---\n\n"
        "# February 2022  (`2022-02`)\n\n"
        "## Digest\n```\n  total ... 602\n```\n\n"
        "## The exact JSON the model is given\n```json\n"
        '{"month": "February 2022"}\n```\n\n'
        f"## Your version\n{answer}\n\n\n---\n\n"
        "# June 2022  (`2022-06`)\n\n"
        "## Your version\n\n\n\n"
    )
    return p


def test_a_written_answer_is_read_back(tmp_path):
    assert read_answers(_sheet(tmp_path, REAL)) == {"2022-02": REAL}


def test_a_blank_answer_is_not_an_answer(tmp_path):
    """The separator rule below a blank section read as an answer, and then
    overwrote the real one on the next rebuild."""
    answers = read_answers(_sheet(tmp_path, ""))
    assert answers == {}
    assert "---" not in answers.values()


def test_a_fragment_is_not_treated_as_gold(tmp_path):
    """A stray line is not a hand-written summary; taking one as gold is how
    the real one was lost."""
    assert read_answers(_sheet(tmp_path, "todo")) == {}


def test_a_missing_worksheet_loses_nothing(tmp_path):
    assert read_answers(tmp_path / "absent.md") == {}


def test_answers_survive_a_rebuild_round_trip(tmp_path):
    """Write, read, write again: the answer has to come back both times."""
    sheet = _sheet(tmp_path, REAL)
    first = read_answers(sheet)
    sheet.write_text(sheet.read_text().replace("  total ... 602", "  total ... 999"))
    assert read_answers(sheet) == first == {"2022-02": REAL}


def test_the_json_store_is_a_second_copy(tmp_path):
    """Either store alone can restore the other, so one bad parse cannot end it."""
    store = tmp_path / "monthly_summaries.json"
    store.write_text(json.dumps({"2022-02": REAL}))
    kept = json.loads(store.read_text())
    kept.update(read_answers(_sheet(tmp_path, "")))
    assert kept == {"2022-02": REAL}
