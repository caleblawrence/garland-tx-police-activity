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
        _result("Live homemade explosives found near Garland park, officials say"),
        fallback_day=date(2025, 3, 30),
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


def test_garland_the_surname_is_not_garland_the_city():
    """A bare-word filter over ten months returned 33 rows about people called
    Garland: an NBA guard, a former attorney general, a film director, an NHL
    winger. It is a surname before it is a city."""
    for headline in [
        "Clippers' Darius Garland: Not playing Friday - CBS Sports",
        "Hunter Biden calls Merrick Garland the 'greatest mistake' made",
        "Alex Garland's Elden Ring is in production",
        "Blue Jackets start NHL trade deadline day by acquiring Conor Garland",
    ]:
        assert news_ingest.to_item(_result(headline), fallback_day=date.today()) is None, headline

    # The city still gets through without needing to say Texas anywhere.
    assert news_ingest.to_item(
        _result("Double shooting shuts down Garland park - CBS News"),
        fallback_day=date(2026, 8, 24),
    ) is not None


def test_our_own_source_pdf_is_not_news():
    """The weekly report matches on any address with GARLAND AVE in it, and
    would arrive every single run. It is the data, not coverage of it."""
    assert news_ingest.to_item(
        _result("Weekly Selected Crime Incidents Report",
                url="https://www.garlandtx.gov/DocumentCenter/View/802",
                content="5 1535 2026R037999 THEFT-SHOPLIFTING 53XX N GARLAND AVE"),
        fallback_day=date(2026, 8, 24),
    ) is None


def test_a_result_without_a_url_or_title_is_dropped():
    assert news_ingest.to_item({"title": "Garland thing", "url": ""}, date.today()) is None
    assert news_ingest.to_item({"title": "", "url": "https://x/1"}, date.today()) is None


def test_nothing_is_a_duplicate_when_the_pool_is_empty():
    called = []
    item = {"source_title": "Garland police investigate shooting"}
    assert news_ingest.is_duplicate(item, [], lambda p: called.append(p) or "DUPLICATE") is False
    assert called == [], "must not spend a model call with nothing to compare against"


def test_the_same_story_from_another_outlet_is_a_duplicate():
    """Three outlets carried one May carjacking. The pool should hold one."""
    item = {"source_title": "Garland police identify attempted carjacker who was shot, killed by driver - CBS News"}
    neighbours = [(1, "Garland police identify attempted carjacker who was shot, killed by driver", date(2026, 5, 7))]
    assert news_ingest.is_duplicate(item, neighbours, lambda p: "DUPLICATE") is True


def test_a_charging_decision_is_not_a_duplicate_of_the_incident_it_follows():
    """A later development reads almost like the incident it followed.

    Protected twice over: the similarity gate never puts the pair to the model,
    and if a closer pair does get through, the prompt says a later development
    is a new event.
    """
    called = []
    assert news_ingest.is_duplicate(
        {"source_title": "Grand jury declines to charge driver in Garland carjacking"},
        [(1, "Garland police identify attempted carjacker who was shot, killed by driver",
          date(2026, 5, 7))],
        lambda p: called.append(p) or "DUPLICATE",
    ) is False
    assert called == [], "the gate should settle this without a model call"


def test_the_dedupe_prompt_still_protects_follow_ups_that_pass_the_gate():
    seen = {}
    news_ingest.is_duplicate(
        {"source_title": "Garland park explosives: reward offered after live devices found"},
        [(1, "Garland park explosives: live devices found near abandoned suitcase",
          date(2025, 3, 31))],
        lambda p: seen.setdefault("prompt", p) and "NEW",
    )
    prompt = seen["prompt"].lower()
    assert "not a duplicate" in prompt
    assert "arrest" in prompt and "charge" in prompt and "verdict" in prompt


def test_two_outlets_in_one_run_are_compared_against_each_other(db, monkeypatch):
    """The bug this catches: nothing is written until a run ends, so comparing
    only against the database meant a story carried by five outlets on one day
    arrived five times. The 10-month backfill stored three copies of the
    same carjacking three times before this was fixed."""
    one_story = [
        {"title": "Garland police identify attempted carjacker who was shot, killed by driver",
         "url": "https://www.cbsnews.com/a", "content": ""},
        {"title": "Garland police identify attempted carjacker who was shot, killed by a driver",
         "url": "https://www.fox4news.com/b", "content": ""},
        {"title": "Garland police identify attempted carjacker shot and killed by driver",
         "url": "https://www.audacy.com/c", "content": ""},
    ]
    monkeypatch.setattr(news_ingest, "QUERIES", ["one query"])
    monkeypatch.setattr(news_ingest, "search", lambda q, s, e: one_story)
    # Every comparison that has something to compare against says DUPLICATE.
    monkeypatch.setattr(news_ingest, "_asker", lambda: (lambda p: "DUPLICATE"))

    result = news_ingest.gather(date(2026, 8, 18), date(2026, 8, 20), apply=True)

    assert result["duplicates"] == 2, "the second and third outlet are the same story"
    assert result["stored"] == 1

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM news_items")
        assert cur.fetchone()[0] == 1


def test_a_follow_up_is_never_merged_even_if_the_model_says_so():
    """The gate exists because the model got this wrong on real data.

    It folded "A 17-year-old has been arrested" into "TEEN DIES: A 17-year-old
    Wylie boy", and "Double shooting shuts down Garland park" into "Garland
    teen charged after two juveniles shot at local park" — deleting the
    follow-up coverage that answers whether anyone was caught. Both scored
    0.17. The model is never asked about pairs that far apart.
    """
    always_yes = lambda p: "DUPLICATE"
    for new_title, stored in [
        ("A 17-year-old has been arrested in connection with a shooting",
         "TEEN DIES: A 17-year-old Wylie boy"),
        ("Double shooting shuts down Garland park",
         "Garland teen charged after two juveniles shot at local park"),
        ("Teen, man wounded after shooting at Garland park",
         "Garland school parking lot shooting leaves 2 men injured"),
    ]:
        assert news_ingest.is_duplicate(
            {"source_title": new_title}, [(1, stored, date(2026, 8, 24))], always_yes
        ) is False, new_title


def test_the_same_headline_from_another_outlet_still_merges():
    assert news_ingest.is_duplicate(
        {"source_title": "Garland police identify attempted carjacker who was shot, killed by driver"},
        [(1, "Garland police identify attempted carjacker shot and killed by driver",
          date(2026, 5, 7))],
        lambda p: "DUPLICATE",
    ) is True


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
