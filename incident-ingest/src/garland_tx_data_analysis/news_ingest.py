#!/usr/bin/env python
"""Gather news coverage of Garland policing into a pool to feature from.

The city's reports cover eight offence categories and nothing else, so the
events most worth showing are the ones they structurally cannot hold: the
explosive devices found near Wynne Park in March 2025, the child who died in a
hot car in August 2026. Neither produced a charge in those categories, so
neither will ever be a record. This is where they live instead.

RSS cannot do it. The outlet feeds reach back two days, and Garland PD
publishes no press feed at all — the city's own feed carried 26 items and no
police stories. Search can reach backwards; feeds cannot. Hence Tavily.

Nothing here publishes. Ingest fills a candidate pool that readers never see;
an item reaches the page only when a person features it and writes its title
and summary by hand.

    uv run python -m garland_tx_data_analysis.news_ingest --backfill
    uv run python -m garland_tx_data_analysis.news_ingest --since 2026-08-01
    uv run python -m garland_tx_data_analysis.news_ingest --export

Needs TAVILY_API_KEY. Dry by default; --apply writes.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import requests
from dotenv import load_dotenv

from garland_tx_data_analysis.tools import WORK_DIR, connect, ensure_schema

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_SECONDS = 60
EXPORT_PATH = f"{WORK_DIR}/news_items.json"

# Tavily caps a single call at 20 results, so the backfill walks a month at a
# time rather than asking for a year and silently getting the first twenty.
MAX_RESULTS = 20
BACKFILL_MONTHS = 10

# Deliberately about Garland policing rather than Garland crime. A hot-car
# death is neither a crime in the reports' categories nor a record they will
# ever hold, and it is exactly what this is for.
QUERIES = [
    "Garland Texas police",
    "Garland Texas shooting OR homicide OR assault",
    "Garland Texas arrested OR charged OR sentenced",
    "Garland Texas fire OR crash OR death investigation",
]

# The featured block never shows more than five.
FEATURED_LIMIT = 5


def _key() -> str:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise SystemExit(
            "TAVILY_API_KEY is not set. Get one at https://tavily.com and put "
            "it in .env."
        )
    return key


def search(query: str, start: date, end: date) -> list[dict]:
    """One Tavily news search over a date range."""
    response = requests.post(
        TAVILY_URL,
        headers={"Authorization": f"Bearer {_key()}"},
        json={
            "query": query,
            "topic": "news",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "max_results": MAX_RESULTS,
            "search_depth": "basic",
        },
        timeout=TAVILY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("results") or []


GARLAND = re.compile(r"\bgarland\b", re.I)

# Our own source, which matches on any address containing GARLAND AVE and would
# arrive every run. It is the data this site already ingests, not coverage of it.
EXCLUDED_URLS = re.compile(r"garlandtx\.gov/DocumentCenter", re.I)

# Garland is a surname before it is a city, and the index does not care which
# you meant. A bare-word filter over ten months returned NBA trade coverage of
# Darius Garland, Hunter Biden on Merrick Garland, an Alex Garland film and an
# NHL deadline deal — 33 of 101 rows. Excluding the people is far more precise
# than requiring a Texas signal, which drops real stories: Tavily's snippets
# are short, and "Double shooting shuts down Garland park" never says Texas.
PEOPLE_NAMED_GARLAND = re.compile(
    r"\b(darius|merrick|judy|beverly|bonnie|alex|conor|hank|red)\s+garland\b", re.I
)

# The filter is otherwise a bare word on purpose. Requiring "Garland" in the headline
# would drop real coverage — the hot-car death ran as "5-year-old girl dies
# inside hot car in North Texas" on two outlets — and a tighter rule costs
# recall on a pool nobody publishes from without reading. An occasional
# unrelated story is a row that never gets featured.


def _outlet(url: str) -> Optional[str]:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else None


def to_item(result: dict, fallback_day: date) -> Optional[dict]:
    """A Tavily result as a row, or None if it is not about Garland.

    The feeds and the index are both metro-wide: one Garland story turned up in
    65 items when this was checked. Anything that never says Garland is almost
    certainly Dallas or Fort Worth, and does not belong on a Garland page.
    """
    url = (result.get("url") or "").strip()
    title = (result.get("title") or "").strip()
    if not url or not title or EXCLUDED_URLS.search(url):
        return None
    body = f"{title} {result.get('content') or ''}"
    if not GARLAND.search(body) or PEOPLE_NAMED_GARLAND.search(body):
        return None

    published = result.get("published_date") or result.get("published")
    day = fallback_day
    if published:
        for fmt in ("%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S"):
            try:
                day = datetime.strptime(published[:len(fmt) + 6].strip(), fmt).date()
                break
            except ValueError:
                continue

    return {
        "url": url,
        "source_title": title,
        "source_summary": (result.get("content") or "").strip() or None,
        "outlet": _outlet(url),
        "published_on": day,
    }


def stored_urls(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT url FROM news_items")
        return {r[0] for r in cur.fetchall()}


def nearby_titles(conn, day: date, window_days: int = 5) -> list[tuple[int, str, date]]:
    """Stored items published near a date, as dedupe candidates."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT news_item_id, source_title, published_on
              FROM news_items
             WHERE published_on BETWEEN %s AND %s
             ORDER BY published_on DESC
            """,
            (day - timedelta(days=window_days), day + timedelta(days=window_days)),
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


# Words that carry no signal about which incident a headline is about.
_NOISE = {
    "the", "a", "an", "in", "of", "to", "after", "for", "and", "on", "at",
    "police", "say", "says", "said", "near", "outside", "new", "man", "woman",
}

# Only near-identical headlines are put to the model. Measured against real
# pairs from the first backfill: genuine cross-outlet repeats of the hot-car
# death scored 0.50 and 0.58, while every wrong merge the model made — an
# arrest folded into the incident it followed, two separate park shootings —
# scored 0.17 or less. The gate excludes all of them.
SIMILARITY_GATE = 0.45


def _title_tokens(title: str) -> set[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
    return {w for w in words if len(w) > 2 and w not in _NOISE}


def title_similarity(a: str, b: str) -> float:
    """Jaccard overlap of two headlines' meaningful words."""
    x, y = _title_tokens(a), _title_tokens(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def is_duplicate(item: dict, neighbours: list[tuple[int, str, date]], ask: Callable) -> bool:
    """Whether this article covers a story already in the pool.

    A unique URL is not enough: the hot-car death of 18 August 2026 came back
    as eight URLs across WFAA, FOX 4, NBCDFW, Yahoo, Audacy and Hoodline. Only
    a reader can tell those are one story, so a model reads them.

    Follow-up coverage is deliberately NOT a duplicate. An arrest reported
    weeks after the incident is a new item, and the prompt says so.
    """
    if not neighbours:
        return False

    # Deleting a story is worse than keeping a repeat: a reader skips a
    # duplicate, but a follow-up that was merged away is gone. So the model is
    # only consulted about headlines that already look the same, and it can
    # only ever veto a merge, never propose one.
    close = [
        n for n in neighbours
        if title_similarity(item["source_title"], n[1]) >= SIMILARITY_GATE
    ]
    if not close:
        return False
    neighbours = close

    listing = "\n".join(f"{i}. {title}" for i, (_, title, _) in enumerate(neighbours, 1))
    answer = ask(
        "You are deduplicating local news coverage.\n\n"
        f"NEW HEADLINE:\n{item['source_title']}\n\n"
        f"ALREADY STORED, published within a few days:\n{listing}\n\n"
        "Does the new headline report the SAME EVENT as any stored one — the "
        "same incident covered by another outlet?\n\n"
        "It is NOT a duplicate if it reports a later development: an arrest, a "
        "charge, a verdict, or a victim being identified. Those are new events "
        "about an earlier incident and must be kept.\n\n"
        "Answer with one word: DUPLICATE or NEW."
    )
    return "DUPLICATE" in (answer or "").upper()


def _asker() -> Callable:
    from langchain.chat_models import init_chat_model

    model = init_chat_model(
        os.getenv("GARLAND_LABEL_MODEL", "anthropic:claude-haiku-4-5")
    )

    def ask(prompt: str) -> str:
        reply = model.invoke(prompt).content
        if isinstance(reply, list):
            reply = " ".join(
                p.get("text", "") for p in reply if isinstance(p, dict)
            )
        return str(reply)

    return ask


def store(items: list[dict]) -> int:
    if not items:
        return 0
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO news_items (url, source_title, source_summary,
                                        outlet, published_on)
                VALUES (%(url)s, %(source_title)s, %(source_summary)s,
                        %(outlet)s, %(published_on)s)
                ON CONFLICT (url) DO NOTHING
                """,
                items,
            )
            return cur.rowcount


def gather(start: date, end: date, apply: bool = False) -> dict:
    """Search every query over the window, month by month, and store what is new."""
    with connect() as conn:
        ensure_schema(conn)
        seen = stored_urls(conn)

    found, kept, dropped_not_garland, dropped_dupe = 0, [], 0, 0
    ask = _asker() if apply else (lambda _: "NEW")

    cursor = start
    while cursor <= end:
        month_end = min(
            end, (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            - timedelta(days=1),
        )
        for query in QUERIES:
            for result in search(query, cursor, month_end):
                found += 1
                item = to_item(result, fallback_day=month_end)
                if item is None:
                    dropped_not_garland += 1
                    continue
                if item["url"] in seen or any(k["url"] == item["url"] for k in kept):
                    continue
                with connect() as conn:
                    neighbours = nearby_titles(conn, item["published_on"])
                # Also compare against what this run has already kept. Nothing
                # is written until the run ends, so without this the pool is
                # only ever compared against previous runs and a story carried
                # by five outlets on one day arrives five times.
                neighbours += [
                    (None, k["source_title"], k["published_on"])
                    for k in kept
                    if abs((k["published_on"] - item["published_on"]).days) <= 5
                ]
                if is_duplicate(item, neighbours, ask):
                    dropped_dupe += 1
                    continue
                kept.append(item)
                seen.add(item["url"])
        cursor = month_end + timedelta(days=1)

    stored = store(kept) if apply else 0
    return {
        "window": f"{start} .. {end}",
        "results_seen": found,
        "not_about_garland": dropped_not_garland,
        "duplicates": dropped_dupe,
        "new": len(kept),
        "stored": stored,
        "applied": apply,
    }


def dedupe_pool(apply: bool = False) -> dict:
    """Collapse stories already in the pool that are the same event.

    For a pool gathered before same-run comparison worked, and as a safety net
    for anything the per-item check let through. Keeps the earliest row of each
    cluster: first to report, and the one a follow-up would be measured against.
    Never touches a featured row.
    """
    ask = _asker()
    removed, kept_ids, merges = [], [], []
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT news_item_id, source_title, published_on, featured
                  FROM news_items ORDER BY published_on, news_item_id
                """
            )
            rows = cur.fetchall()

    for item_id, title, day, featured in rows:
        if item_id in removed:
            continue
        cluster = [
            (i, t, d)
            for i, t, d, f in rows
            if i in kept_ids and abs((d - day).days) <= 5
        ]
        if featured:
            kept_ids.append(item_id)
            continue
        if cluster and is_duplicate({"source_title": title}, cluster, ask):
            removed.append(item_id)
            merges.append((title, [t for _, t, _ in cluster]))
        else:
            kept_ids.append(item_id)

    if apply and removed:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM news_items WHERE news_item_id = ANY(%s) AND NOT featured",
                (removed,),
            )
    return {
        "pool": len(rows),
        "duplicates": len(removed),
        "merges": [
            {"dropped": d, "as_duplicate_of": c[-1] if c else None} for d, c in merges
        ],
        "remaining": len(rows) - (len(removed) if apply else 0),
        "applied": apply,
    }


def export(path: str = EXPORT_PATH) -> dict:
    """Write the featured items the site renders.

    Only featured rows, newest first, capped. The database refuses a featured
    row without both display fields, so nothing here can fall back to the
    source title and print a name.
    """
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT display_title, display_summary, url, outlet,
                       published_on, monthly_incident_id
                  FROM news_items
                 WHERE featured
                 ORDER BY published_on DESC, news_item_id DESC
                 LIMIT %s
                """,
                (FEATURED_LIMIT,),
            )
            items = [
                {
                    "title": r[0],
                    "summary": r[1],
                    "url": r[2],
                    "outlet": r[3],
                    "published_on": r[4].isoformat(),
                    "incident_id": r[5],
                }
                for r in cur.fetchall()
            ]
            cur.execute("SELECT count(*) FROM news_items")
            pool = cur.fetchone()[0]

    unwritten = [i for i in items if not i["title"] or not i["summary"]]
    if unwritten:
        raise ValueError(
            f"{len(unwritten)} featured item(s) have no hand-written title or "
            "summary. Refusing to export: the fallback would publish the "
            "source headline, which may name someone."
        )

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"items": items}, f, indent=1)
    return {"path": os.path.abspath(path), "featured": len(items), "pool": pool}


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true",
                        help=f"search the last {BACKFILL_MONTHS} months")
    parser.add_argument("--since", help="search from this date (YYYY-MM-DD) to today")
    parser.add_argument("--apply", action="store_true", help="write; otherwise dry")
    parser.add_argument("--export", action="store_true",
                        help="write the JSON the site reads")
    parser.add_argument("--dedupe-pool", action="store_true",
                        help="collapse same-event stories already stored")
    parser.add_argument("--today", help="override today's date, for testing")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()

    if args.backfill or args.since:
        start = (
            date.fromisoformat(args.since)
            if args.since
            else (today.replace(day=1) - timedelta(days=30 * BACKFILL_MONTHS))
        )
        print(json.dumps(gather(start, today, apply=args.apply), indent=2, default=str))
        if not args.apply:
            print("\nDry run. Re-run with --apply to store.")

    if args.dedupe_pool:
        print(json.dumps(dedupe_pool(apply=args.apply), indent=2))
        if not args.apply:
            print("\nDry run. Re-run with --apply to delete.")

    if args.export:
        print(json.dumps(export(), indent=2))


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
