"""The news pool: what gets in, what gets merged, and what may be published."""

import json
from datetime import date

import pytest

from garland_tx_data_analysis import news_ingest
from garland_tx_data_analysis.tools import connect, ensure_schema


def _result(title, url="https://www.wfaa.com/article/x", content=""):
    return {"title": title, "url": url, "content": content}


def test_a_result_that_never_says_garland_is_dropped():
    """The index is metro-wide; one Garland story turned up in 65 items.

    Without this filter the block fills with Dallas and Fort Worth, which is a
    category error on a page that is entirely about Garland.
    """
    keep = news_ingest.to_item(
        _result("5-year-old dies after being left in hot car, Garland police say"),
        fallback_day=date(2026, 8, 18),
    )
    assert keep is not None

    for headline in [
        "Suspect charged with murder in Pleasant Grove shooting",
        "Armed witness shoots bank robbery suspect in Cedar Hill",
        "Fort Worth murder suspect smoked crack cocaine before stabbing cousin",
    ]:
        assert news_ingest.to_item(_result(headline), fallback_day=date.today()) is None


def test_garland_can_be_mentioned_in_the_body_rather_than_the_headline():
    item = news_ingest.to_item(
        _result(
            "Child dies after being left in vehicle",
            content="Police in Garland said the girl was found unresponsive.",
        ),
        fallback_day=date(2026, 8, 18),
    )
    assert item is not None
    assert item["outlet"] == "wfaa.com"


def test_a_result_without_a_url_or_title_is_dropped():
    assert news_ingest.to_item({"title": "Garland thing", "url": ""}, date.today()) is None
    assert news_ingest.to_item({"title": "", "url": "https://x/1"}, date.today()) is None


def test_nothing_is_a_duplicate_when_the_pool_is_empty():
    called = []
    item = {"source_title": "Garland police investigate shooting"}
    assert news_ingest.is_duplicate(item, [], lambda p: called.append(p) or "DUPLICATE") is False
    assert called == [], "must not spend a model call with nothing to compare against"


def test_the_same_story_from_another_outlet_is_a_duplicate():
    """Eight outlets carried the hot-car death. The pool should hold one."""
    item = {"source_title": "5-year-old girl dies after being left in hot car, Garland police say"}
    neighbours = [(1, "Girl, 5, dies after being left in car Tuesday, Garland Police say", date(2026, 8, 18))]
    assert news_ingest.is_duplicate(item, neighbours, lambda p: "DUPLICATE") is True


def test_the_dedupe_prompt_protects_follow_up_coverage():
    """An arrest weeks later is a new item, not a duplicate of the incident.

    This is the whole reason a model reads the headlines rather than a
    similarity score: 'hot car death' and 'mother charged in hot car death'
    look almost identical and are different events.
    """
    seen = {}
    news_ingest.is_duplicate(
        {"source_title": "Mother charged in Garland hot car death"},
        [(1, "5-year-old dies after being left in hot car, Garland police say", date(2026, 8, 18))],
        lambda p: seen.setdefault("prompt", p) and "NEW",
    )
    prompt = seen["prompt"].lower()
    assert "not a duplicate" in prompt
    assert "arrest" in prompt and "charge" in prompt and "verdict" in prompt


def test_a_featured_item_cannot_exist_without_hand_written_text(db):
    """The database refuses it, so the export can never fall back to the
    source headline and print a name."""
    import psycopg

    with connect() as conn:
        ensure_schema(conn)
        row = dict(
            url="https://www.wfaa.com/a", source_title="Marcus Webb charged in Garland shooting",
            published_on=date(2026, 8, 18),
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO news_items (url, source_title, published_on) "
                "VALUES (%(url)s, %(source_title)s, %(published_on)s)", row
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("UPDATE news_items SET featured = true")


def test_export_publishes_only_featured_items_and_caps_them(db, tmp_path):
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            for i in range(8):
                cur.execute(
                    """
                    INSERT INTO news_items (url, source_title, published_on, featured,
                                            display_title, display_summary)
                    VALUES (%s, %s, %s, true, %s, %s)
                    """,
                    (f"https://x/{i}", f"Named Person {i} charged in Garland",
                     date(2026, 8, 1 + i), f"A person was charged, item {i}",
                     "Charges were filed in an August incident."),
                )
            cur.execute(
                "INSERT INTO news_items (url, source_title, published_on) VALUES (%s, %s, %s)",
                ("https://x/pool", "Unfeatured Garland story", date(2026, 8, 20)),
            )

    out = tmp_path / "news.json"
    result = news_ingest.export(str(out))

    assert result["featured"] == 5, "at most five are ever shown"
    assert result["pool"] == 9

    payload = json.loads(out.read_text())
    assert [i["title"] for i in payload["items"]] == [
        f"A person was charged, item {i}" for i in (7, 6, 5, 4, 3)
    ], "newest first"
    assert all("Named Person" not in json.dumps(i) for i in payload["items"]), (
        "no source headline may reach the export"
    )
