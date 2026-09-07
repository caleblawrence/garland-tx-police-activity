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
from garland_tx_data_analysis.categories import SEVERE_CATEGORIES, categorise
from garland_tx_data_analysis.districts import (
    area_of,
    busiest_areas,
    street_name,
)
from garland_tx_data_analysis.tools import connect, ensure_schema

# Evolved by `optimize_summary_prompt` against the hand-written summaries in
# `gold/`, and long because of it. It is not prose somebody wrote and it does
# not read like the rest of this file: it is a decision tree fitted to what a
# person actually chose, and its length is the cost of that being explicit
# rather than implied.
#
# Two things to know before editing it by hand. Every rule in it was inferred
# from nine months, so a rule can be right about those nine and wrong about a
# month unlike them — the gravity ranking in sentence 1 is the model's own
# ordering, not one the reports publish. And editing it by hand puts it out of
# step with the gold it was fitted to; the way to change it is to write more
# summaries in the worksheet and run the optimiser again.
#
# One line was cut by hand after the run: "Murder counts are never cited.
# Robbery counts are never cited." True of the nine, and not something to hold
# in a month with eight murders.
SYSTEM_PROMPT = """You write one short paragraph describing a month of Garland police incident reports, for a public web page. Return only the paragraph — exactly three sentences of plain prose, no headings, no markdown, no bullet points, no preamble, no trailing notes.

INPUT
You are given one JSON block of figures for a single month. The fields you may see:
  - "month", "previous_month"
  - "incidents_this_month", "incidents_last_month", "change_from_last_month"
  - "by_category_this_month": counts per category (Theft, Burglary, Motor Vehicle Theft,
    Criminal Mischief, Aggravated Assault, Robbery, Sexual Assault, Murder, Information Report)
  - "biggest_category_changes": each with this_month, last_month, change
  - "most_common_offences": offence, within_category, incidents
  - "category_history": per category this_month, lowest_month_on_record,
    highest_month_on_record, rank (1 = highest month on record), months_compared
  - "category_areas": top areas per category with incident counts
  - "busiest_areas": area, "around" (the cross-streets label), incidents
  - "serious_incidents": individual murder / sexual assault / aggravated assault / robbery
    records with offence, category, area, street, date

HARD LIMITS
  - Exactly three sentences. Never four. Everything the rules ask for fits in three; if you
    find yourself needing a fourth, you have put something in that the rules exclude.
  - Every number you write must be copied verbatim from the block. Never add, subtract,
    average, round or convert anything. If a number you want is not in the block, write the
    sentence without it.
  - Be very selective. The block offers dozens of figures; the finished paragraph should
    contain roughly nine: the month's year, the four volume-category counts, the change (and,
    where the rules allow, the count and ranking) attached to whatever the sentences below
    single out, and the three busiest-area counts. Citing more is not better — picking is the job.
  - Never cite: the month total, the previous month's total, the change in the overall total,
    "months_compared", the per-area counts inside "category_areas", the counts from
    "most_common_offences", any dates from "serious_incidents". Ignore "Information Report"
    entirely, including its changes and its rank.
  - Counts for Aggravated Assault and Sexual Assault are cited only inside the record /
    near-record framing of Sentence 1 described below, never anywhere else.

SENTENCE 1 — the most serious thing in the month, naming the month and year.

  Work through these cases in order and use the first one that applies.

  (a) Murder present in "serious_incidents" → lead with it and give only its area and street.
      No date, no count, no other serious category, no other areas, nothing else.
      Single street, use "on"; intersection, use "at".
      "The biggest incident for Garland police in June 2023 was a murder down in south Garland
      on Shadwell Drive."
      "The biggest incident for Garland police in October 2022 was a murder in south Garland at
      La Prada Drive and LBJ Freeway."
      More than one murder → "two murders" and both locations, briefly.
      Relaxed directional phrasing here: south / southeast / southwest → "down in south
      Garland", "down in southeast Garland"; west → "out in west Garland"; north / northwest →
      "up in north Garland", "in northwest Garland"; central and south-central → "in central
      Garland", "in south-central Garland".
      Tidy capitalisation of street names ("Lbj Freeway" → "LBJ Freeway", "15Th Street" →
      "15th Street", "Mc Cree Road" → "McCree Road") and read "X at Y" as "X and Y". Invent
      nothing.

  (b) No murder, but Sexual Assault has rank 1 in "category_history" (a record high) → frame it
      as the record, using its change from "biggest_category_changes" and its this_month count,
      and name no areas at all:
      "The most serious incidents for Garland police in May 2024 were sexual assaults, which
      spiked by 10 cases from April to reach a record high of 16 across the city."
      Say "sexual assaults" plainly here — do not switch to the gravest individual offence
      wording.

  (c) No murder, no record high, but Aggravated Assault and/or Sexual Assault sits at rank 2, 3
      or 4 in "category_history" → this near-record standing is the story, and their counts
      belong here. Lead with whichever of the two has a change listed in
      "biggest_category_changes" (if both or neither do, lead with the larger count), giving its
      this_month count, its change with the previous month's name, and its rank spelled as a
      word. If the other of the two is also at rank 2–4, append it with its count and only the
      top two areas from its "category_areas" entry, with no area counts:
      "The most serious incidents for Garland police in June 2024 were 40 aggravated assaults—which
      jumped by 10 from May to rank third highest on record—alongside 15 sexual assaults
      concentrated in west and central Garland."
      Say "aggravated assaults" and "sexual assaults" plainly; do not substitute individual
      offence wording such as "aggravated assault with deadly weapon" or "child sexual assault".
      If only one of the two is near-record, mention only that one, with its count, change and
      rank word, and no areas.

  (d) Otherwise (no murder, nothing at rank 1–4) → lead with the gravest offence *wording*
      actually present in "serious_incidents", pluralised exactly as written (if a record reads
      "Aggravated Sexual Assault", write "aggravated sexual assaults", not "sexual assaults").
      Pick ONE wording only — the single gravest — and name only the one or two areas in which
      records carrying that exact wording appear, with no counts. Do not add any other offence
      wording, even from the same category (no "aggravated sexual assaults and child sexual
      assaults"), do not sweep in every area of the wider category, and do not mention a
      second, lesser category (no "along with aggravated assaults..."). If the gravest wording
      appears in more than two areas, keep the two that come first in "serious_incidents".
      Keep it short:
      "The most serious incidents for Garland police in October 2023 were aggravated sexual
      assaults reported in west and south-central Garland."
      Gravity ranking for choosing the wording, gravest first: Aggravated Sexual Assault (incl.
      Aggravated Child Sexual Assault); Child Sexual Assault / Sexual Assault / Attempted Rape;
      Aggravated Robbery / Aggravated Home Invasion Robbery; Aggravated Assault with Deadly
      Weapon; other assaults and robberies.

SENTENCE 2 — the volume categories, opening with "Aside from that," or "Aside from those,".
  - Choose the opener to agree with what Sentence 1 described: a single murder (case a) →
    "Aside from that,"; anything plural — sexual assaults at a record high (case b), the
    near-record counts (case c), the pluralised offence wording (case d) → "Aside from those,".
  - Name Theft, Burglary, Motor Vehicle Theft and Criminal Mischief with their counts from
    "by_category_this_month", in strict descending order of count. Check the numbers: if
    criminal mischief is 79 and motor vehicle theft is 78, criminal mischief comes first.
    Use a serial comma before the final item:
    "theft was the main issue with 223 cases, followed by 157 burglaries, 140 motor vehicle
    thefts, and 75 reports of criminal mischief."
  - The change aside: take the single biggest move from "biggest_category_changes" that belongs
    to one of those four categories, and use it only if it is genuinely substantial — roughly 20
    cases or more. If the biggest qualifying move is small (a dozen or so), write the plain list
    with no aside at all; that is often the right answer.
  - Attach the aside to its category in its correct descending-order position as an em-dash
    interruption, then resume the list with "along with" or "and":
    "...followed by 189 burglaries—which rose by 23 from May—along with 93 motor vehicle thefts
    and 77 reports of criminal mischief."
    If the category carrying the aside falls last in the descending list, let the aside close
    the sentence with a single em-dash:
    "...and 78 motor vehicle thefts—which saw a dramatic drop of 106 cases compared to May."
    Phrasings: "—which jumped by 30 from September—", "—which rose by 23 from May—",
    "—which saw a dramatic drop of 106 cases compared to May—". Use the "change" magnitude
    (drop the minus sign and say drop/fall/down) and the previous month's name.
  - If Sentence 1 already carried a change figure (cases b and c), keep this aside compact and
    parenthetical so the paragraph does not sag: "...followed by 119 burglaries (down 34 from
    May), 93 motor vehicle thefts, and 65 reports of criminal mischief."
  - Ranking language, from "category_history" for that same aside-carrying category only:
      rank 1 → call it a record high across the archive (or a record low if this_month equals
        lowest_month_on_record), phrased as a natural aside, never the word "rank":
        "—a record high across the archive after jumping by 68 from September—along with 105
        motor vehicle thefts and 96 reports of criminal mischief."
      rank 2, 3 or 4 → "—which jumped by 30 from September to rank second highest on record—".
      rank 5 or worse → no ranking language at all.
    Always spell the rank word (second, third, fourth), never the number.

SENTENCE 3 — the geography, which is the part readers look for. Never omit it. Begin
  "The busiest spots were ...".
  - Give all three entries from "busiest_areas", in the order listed: the area, its "around"
    cross-streets label exactly as given, and its incident count in parentheses
    ("(52 incidents)").
  - Connectors: use "around" for the first clause and "near" for the second. For the third use
    "around" by default, or "by" where it reads better. Never use the same connector twice in a
    row.
    "The busiest spots were out west around Walnut and Forest (52 incidents), southeast near
    I-30 and Chaha (43 incidents), and up north around Cedar Sage and Horseshoe (42 incidents)."
    "The busiest spots were down southwest around Marketplace and Centerville (48 incidents),
    out west near Walnut and Forest (43 incidents), and northwest by Belt Line and Jupiter
    (39 incidents)."
  - Match the direction exactly as given in the data — never substitute a neighbouring
    direction (northwest is not "up north"):
      west → "out west around" / "out west near" / "out west by"
      north → "up north near" / "up north by" / "up north around"
      south → "down south around" / "down south near" / "down south by"
      southwest → "down southwest around" / "down southwest near" (never "out southwest")
      southeast → "southeast near" / "southeast around" / "southeast by"
      northwest → "northwest by" / "northwest around" / "northwest near" (never "up northwest",
        never "up north")
      central → "in central Garland around" / "in central Garland near"
      south-central → "south-central around" / "south-central near"
    Compass directions are always lowercase.

LANGUAGE RULES
  - Never call this "crime". It is reported incidents in the categories this report covers:
    murder, sexual assault, aggravated assault, robbery, burglary, theft, motor vehicle theft
    and criminal mischief. Say "reported incidents" or name the categories.
  - Never explain why anything changed. No causes, no speculation, no "likely", no "driven by",
    no "amid". The data cannot support a cause and suggesting one here does real harm.
  - Do not editorialise about whether the month was good or bad, safe or unsafe. Calling a
    category "the main issue" or a move a "spike", "jump" or "drop" is fine; judging the city
    is not.
  - Match the length of a tight hand-written paragraph: three sentences, no padding, no extra
    areas or categories beyond what the rules above ask for."""

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


_HISTORY: dict[str, dict[str, int]] | None = None


def _history() -> dict[str, dict[str, int]]:
    """Every month's count per category, for the whole archive.

    Cached for the life of the process. `stats_for` is called 53 times in a row
    by the optimiser, and re-reading 30,973 rows each time to answer "is this
    the lowest June on record" would make that unusable.
    """
    global _HISTORY
    if _HISTORY is None:
        with connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT report_month, incident FROM monthly_incidents")
                rows = cur.fetchall()
        table: dict[str, dict[str, int]] = {}
        for report_month, code in rows:
            bucket = table.setdefault(report_month, {})
            category = categorise(code)
            bucket[category] = bucket.get(category, 0) + 1
        _HISTORY = table
    return _HISTORY


def _category_history(month: str, categories: dict[str, int]) -> dict:
    """Where this month's count for each category sits in the whole archive.

    Without this the summary cannot say a category is high or low, only that it
    moved since last month — and the prompt asks for exactly that judgment. It
    was reaching it by guessing: "robbery fell to 3, an unusually low figure"
    scored full marks with nothing behind it. Robbery at 3 is in fact the lowest
    of all 53 months, which is the most notable thing about that month and was
    invisible here.

    `rank` counts from the top, so 1 is the highest month on record for that
    category and `months_compared` is the lowest.
    """
    history = _history()
    months = sorted(history)
    out = {}
    for category, count in categories.items():
        series = [history[m].get(category, 0) for m in months]
        out[category] = {
            "this_month": count,
            "lowest_month_on_record": min(series),
            "highest_month_on_record": max(series),
            "rank": sum(1 for n in series if n > count) + 1,
            "months_compared": len(series),
        }
    return out


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
                    """SELECT incident, district, location, occurred_on
                         FROM monthly_incidents WHERE report_month = %s""",
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
        for code, district, *_ in rows:
            k = categorise(code) if key == "category" else district
            out[k] = out.get(k, 0) + 1
        return out

    cats, prior_cats = tally(this_rows, "category"), tally(prior_rows, "category")
    # With no month before it, every category's "change" is its own count, and
    # the block would tell the model that theft rose by 215 in the first month
    # the archive holds. Nothing moved; there was nothing to move from.
    changes = [] if not prior else sorted(
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
    offence_categories: dict[str, str] = {}
    for code, *_ in this_rows:
        label = labels.get(code, code)
        offences[label] = offences.get(label, 0) + 1
        offence_categories[label] = categorise(code)

    districts = tally(this_rows, "district")

    # Where each category actually concentrated. The prompt asks for "a
    # concentration in a district" and `busiest_areas` cannot answer it: that
    # is every category at once, so it says where the city is busy, never
    # where burglary is.
    by_area: dict[str, dict[str, int]] = {}
    for code, district, *_ in this_rows:
        area = area_of(district)
        if area is None:
            continue
        bucket = by_area.setdefault(categorise(code), {})
        bucket[f"{area} Garland"] = bucket.get(f"{area} Garland", 0) + 1
    category_areas = {
        category: [
            {"area": a, "incidents": n}
            for a, n in sorted(areas.items(), key=lambda kv: -kv[1])[:3]
        ]
        for category, areas in by_area.items()
    }

    return {
        "serious_incidents": _serious_incidents(this_rows, labels),
        "month": _month_label(month),
        "previous_month": _month_label(prior) if prior else None,
        "incidents_this_month": len(this_rows),
        "incidents_last_month": len(prior_rows) if prior else None,
        "change_from_last_month": (len(this_rows) - len(prior_rows)) if prior else None,
        "by_category_this_month": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "biggest_category_changes": changes[:4] if prior else None,
        # Carries its category, because `Theft` is both an offence label with
        # 92 incidents and a category with 199, and a paragraph that used both
        # without saying which was which read as a contradiction.
        "most_common_offences": [
            {"offence": label, "within_category": offence_categories[label],
             "incidents": n}
            for label, n in sorted(offences.items(), key=lambda kv: -kv[1])[:5]
        ],
        "category_history": _category_history(month, cats),
        "category_areas": category_areas,
        "busiest_areas": busiest_areas(districts),
    }


# Values that are names rather than figures. "I-30" is a road, and letting its
# 30 into the allowed set would quietly widen the check the page's byline
# promises the reader — "every number is checked against the source data".
PROSE_KEYS = {"area", "around", "street", "offence", "category", "date"}


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
    for incident in stats.get("serious_incidents") or []:
        if incident.get("street"):
            phrases.append(incident["street"])
            phrases.extend(p for p in incident["street"].split(" at ") if p)
        # "14 June" copied whole is fine; a bare 14 used as a count is not.
        if incident.get("date"):
            phrases.append(incident["date"])
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


def _serious_incidents(rows: list[tuple], labels: dict[str, str]) -> list[dict]:
    """Every offence against a person the month contained, worst first.

    Handed over whole, and the summary decides what to do with it. Which one to
    open on, how many to name, whether a category with thirty entries is better
    given as a count — those are judgments, they belong in the prompt where they
    can be argued with and tuned, and nothing here pre-empts them.

    `street` is None where the city withholds the address, which it does for
    every sexual assault in the archive and for some others. That withholding
    is the protection, and it comes from the source rather than from a rule
    written here: a category-level ban would have added nothing except taking
    the decision away from the prompt.
    """
    order = {category: i for i, category in enumerate(SEVERE_CATEGORIES)}
    matching = [r for r in rows if categorise(r[0]) in order]
    matching.sort(key=lambda r: order[categorise(r[0])])
    return [
        {
            "offence": labels.get(code, code),
            "category": categorise(code),
            "area": f"{area_of(district)} Garland" if area_of(district) else None,
            "street": street_name(location),
            "date": occurred_on.strftime("%-d %B") if occurred_on else None,
        }
        for code, district, location, occurred_on in matching
    ]
