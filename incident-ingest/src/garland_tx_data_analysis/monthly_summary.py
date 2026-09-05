#!/usr/bin/env python
"""A short written summary of each month, checked before it can be published.

The split is the point. Every number is computed here, in SQL and Python. The
model is handed those numbers and writes sentences; it never sees a row, never
counts anything, and cannot introduce a figure of its own. Then every numeral
in what it wrote is checked back against the stats that produced it, and a
summary carrying an unverifiable number is discarded rather than shown.

That is not belt and braces. A model asked to describe a table will produce
plausible numbers, and this is a public page about crime — a wrong figure here
is worse than no summary at all.

Two things the prose is forbidden from doing, enforced in the prompt and
scored by `summary_metric`:

  - calling the total a number of crimes. The total includes information
    reports — a found-property report, an abandoned vehicle — which record a
    report rather than an established offence, so "442 crimes" is false by the
    22 of them June 2026 contained. "Incidents" is the honest word because it
    is the one broad enough to cover both. The word itself is fine where it is
    accurate: burglary is a crime.
  - explaining why anything changed. This data cannot support a cause, and an
    implied one on a public crime map does real harm.

Districts never reach the model. A reader does not know where district 31 is,
and the figures used to hand over `{"31": 33}` — a district number and a count
side by side as though they were the same kind of thing. `districts.py` turns
one into "north Garland, around Garland Road and Belt Line" before the model
sees it, on the same principle as every number here: the pipeline settles what
has a right answer, and the model only writes.

    uv run python -m garland_tx_data_analysis.monthly_summary --month 2026-06
    uv run python -m garland_tx_data_analysis.monthly_summary --all
"""

import argparse
import json
import re
import sys
from datetime import date

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from garland_tx_data_analysis.agent import LABEL_MODEL
from garland_tx_data_analysis.categories import categorise
from garland_tx_data_analysis.districts import busiest_areas
from garland_tx_data_analysis.tools import connect, ensure_schema

SYSTEM_PROMPT = """You write one short paragraph describing a month of Garland
police incident reports, for a public web page.

You are given a block of figures. Every number in what you write must be one of
those figures, copied exactly. Do not calculate anything — not a total, not a
difference, not a percentage. If a number you want is not in the block, write
the sentence without it.

Write 3 to 4 sentences. Cover:
  - how many incidents were reported, and how that compares with the month
    before,
  - the categories that moved most, in either direction,
  - where in the city the most were recorded, from `busiest_areas`.

If a fourth sentence earns its place, use it for one thing a reader would find
genuinely notable — an unusual offence, a category at its highest or lowest.
Three sentences that cover the month are better than four with a filler.

Naming the total:
  - The total counts reported incidents, not crimes. It includes information
    reports — a found-property report, an abandoned vehicle — which record that
    something was reported, not that an offence was established. Calling the
    total a number of crimes is factually wrong, not merely loose. Write
    "incidents" or "reported incidents".
  - "Crime" is fine where it is accurate. Murder, robbery and burglary are
    crimes and may be called that. The rule is about the total, not the word.

Where things happened:
  - Use `area` and `around` from `busiest_areas`, copied as given: "north
    Garland, around Garland Road and Belt Line".
  - Never write a district number. They are not in your figures, and they mean
    nothing to a reader who lives there.

Never explain why anything changed:
  - Stating a relationship between two figures is description, and is welcome:
    "theft rose by 27 while robbery fell by 11".
  - Asserting a reason that is not in the figures is explanation, and is
    forbidden — no "because", "due to", "driven by", "caused by", "as a result
    of", "reflects", and no hedged version either: no "likely", "possibly",
    "appears to", "may have". This data cannot support a cause, and suggesting
    one on a public page about policing does real harm.

Do not editorialise:
  - Not about whether a month was good or bad, safe or unsafe, dangerous,
    concerning, alarming, worrying, troubling, encouraging or reassuring.
  - Not by dramatising a change: no "surged", "spiked", "plummeted", "soared",
    "dramatic", "notable", "notably", "significant" or "significantly". A
    figure rose by 27, and that is the whole claim.

Plain sentences. No headings, no bullets, no markdown.

Return only the paragraph."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monthly_summaries (
    report_month text PRIMARY KEY,
    summary      text NOT NULL,
    stats        jsonb NOT NULL,
    model        text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now()
);
"""

# Anything that looks like a figure a reader would take as fact.
NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def _month_label(month: str) -> str:
    y, m = month.split("-")
    return date(int(y), int(m), 1).strftime("%B %Y")


def stats_for(month: str) -> dict:
    """Every figure the summary is allowed to contain."""
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT report_month FROM monthly_reports ORDER BY report_month"
            )
            months = [r[0] for r in cur.fetchall()]
            if month not in months:
                raise ValueError(f"{month} has not been ingested")
            prior = months[months.index(month) - 1] if months.index(month) else None

            def rows_for(m):
                cur.execute(
                    """SELECT incident, district FROM monthly_incidents
                        WHERE report_month = %s""",
                    (m,),
                )
                return cur.fetchall()

            this_rows = rows_for(month)
            prior_rows = rows_for(prior) if prior else []

            cur.execute(
                """SELECT declared_total, stored_total, unattributed_rows,
                          shortfall_rows
                     FROM monthly_reports WHERE report_month = %s""",
                (month,),
            )
            declared, stored, unattributed, shortfall = cur.fetchone()

            cur.execute(
                """SELECT m.incident, l.short_description
                     FROM monthly_incidents m
                     LEFT JOIN incident_labels l USING (incident)
                    WHERE m.report_month = %s""",
                (month,),
            )
            labels = {code: (label or code) for code, label in cur.fetchall()}

    def tally(rows, key):
        out: dict[str, int] = {}
        for code, district in rows:
            k = categorise(code) if key == "category" else district
            out[k] = out.get(k, 0) + 1
        return out

    cats, prior_cats = tally(this_rows, "category"), tally(prior_rows, "category")
    changes = sorted(
        (
            {
                "category": c,
                "this_month": cats.get(c, 0),
                "last_month": prior_cats.get(c, 0),
                "change": cats.get(c, 0) - prior_cats.get(c, 0),
            }
            for c in set(cats) | set(prior_cats)
        ),
        key=lambda d: -abs(d["change"]),
    )

    offences: dict[str, int] = {}
    for code, _ in this_rows:
        label = labels.get(code, code)
        offences[label] = offences.get(label, 0) + 1

    districts = tally(this_rows, "district")

    return {
        "month": _month_label(month),
        "previous_month": _month_label(prior) if prior else None,
        "incidents_this_month": len(this_rows),
        "incidents_last_month": len(prior_rows) if prior else None,
        "change_from_last_month": (len(this_rows) - len(prior_rows)) if prior else None,
        "by_category_this_month": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "biggest_category_changes": changes[:4],
        "most_common_offences": dict(
            sorted(offences.items(), key=lambda kv: -kv[1])[:5]
        ),
        "busiest_areas": busiest_areas(districts),
        "rows_the_report_declared": declared,
        "rows_stored": stored,
        "rows_not_attributable_to_a_district": unattributed,
        "rows_missing_from_the_report_text": shortfall,
    }


# Values that are names rather than figures. "I-30" is a road, and letting its
# 30 into the allowed set would quietly widen the check the page's byline
# promises the reader — "every number is checked against the source data".
PROSE_KEYS = {"area", "around"}


def _allowed_numerals(stats: dict) -> set[str]:
    """Every number the prose may contain, in the spellings a writer would use."""
    allowed: set[str] = set()

    def walk(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int):
            allowed.update({str(value), f"{value:,}", str(abs(value)), f"{abs(value):,}"})
        elif isinstance(value, float):
            allowed.update({str(value), f"{value:.1f}"})
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(k)
                if k not in PROSE_KEYS:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            # District "23" and the year inside "June 2026" are both fair game.
            allowed.update(NUMERAL.findall(value))

    walk(stats)
    return {a.replace(",", "").rstrip("%") for a in allowed}


def _without_supplied_names(summary: str, stats: dict) -> str:
    """The prose with the place names the block handed over removed.

    `busiest_areas` supplies "I-30 and Duck Creek" and the prompt tells the
    model to copy that phrasing as given. The 30 in I-30 is part of a road's
    name, not a figure, so counting it as one accuses the model of inventing a
    number it was instructed to write — and refuses the month for obeying.

    Only phrases the pipeline itself supplied are removed, so this cannot
    excuse a numeral the model chose.
    """
    phrases: list[str] = []
    for area in stats.get("busiest_areas") or []:
        for value in (area.get("around"), area.get("area")):
            if value:
                phrases.append(value)
                phrases.extend(part for part in value.split(" and ") if part)
    for phrase in sorted(phrases, key=len, reverse=True):
        summary = re.sub(re.escape(phrase), " ", summary, flags=re.I)
    return summary


def verify(summary: str, stats: dict) -> list[str]:
    """Numerals in the prose that no figure in the stats block accounts for."""
    summary = _without_supplied_names(summary, stats)
    allowed = _allowed_numerals(stats)
    unverified = []
    for token in NUMERAL.findall(summary):
        if token.replace(",", "").rstrip("%") not in allowed:
            unverified.append(token)
    return unverified


def generate(
    stats: dict,
    system_prompt: str = SYSTEM_PROMPT,
    model=None,
    model_name: str = LABEL_MODEL,
) -> str:
    """One paragraph from one stats block: the only place the model is asked.

    The prompt is a parameter so `optimize_summary_prompt` scores candidate
    prompts through this exact call rather than a copy of it. A copy would
    drift, and an optimiser tuning a prompt against a call the pipeline does
    not make is worse than no optimiser at all.
    """
    model = model or init_chat_model(model_name)
    reply = model.invoke(
        [("system", system_prompt), ("user", json.dumps(stats, indent=1))]
    )
    text = reply.content
    if isinstance(text, list):
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
    return " ".join(str(text).split())


def write_summary(month: str, model_name: str = LABEL_MODEL) -> dict:
    stats = stats_for(month)
    text = generate(stats, model_name=model_name)
    unverified = verify(text, stats)
    return {
        "month": month,
        "summary": text,
        "stats": stats,
        "unverified": unverified,
        "model": model_name,
    }


def store(result: dict) -> None:
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                INSERT INTO monthly_summaries (report_month, summary, stats, model)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (report_month) DO UPDATE
                   SET summary = EXCLUDED.summary, stats = EXCLUDED.stats,
                       model = EXCLUDED.model, generated_at = now()
                """,
                (result["month"], result["summary"], json.dumps(result["stats"]),
                 result["model"]),
            )


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="one month, as YYYY-MM")
    parser.add_argument("--all", action="store_true", help="every ingested month")
    parser.add_argument("--force", action="store_true", help="regenerate months already written")
    parser.add_argument("--apply", action="store_true", help="store the result")
    args = parser.parse_args(argv)

    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("SELECT report_month FROM monthly_reports ORDER BY report_month")
            months = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT report_month FROM monthly_summaries")
            done = {r[0] for r in cur.fetchall()}

    targets = [args.month] if args.month else months if args.all else months[-1:]
    rejected = 0
    for month in targets:
        if month in done and not args.force:
            print(f"  {month}  already written")
            continue
        result = write_summary(month)
        if result["unverified"]:
            rejected += 1
            print(f"  {month}  REJECTED — numbers not in the stats: "
                  f"{result['unverified']}\n      {result['summary']}")
            continue
        print(f"  {month}  {result['summary']}")
        if args.apply:
            store(result)
    if rejected:
        print(f"\n{rejected} summary(ies) rejected as unverifiable and not stored.")
    if not args.apply:
        print("\nNothing stored. Re-run with --apply.")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
