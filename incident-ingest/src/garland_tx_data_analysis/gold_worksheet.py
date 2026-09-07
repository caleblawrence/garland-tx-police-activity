#!/usr/bin/env python
"""The worksheet of hand-written summaries the optimiser is aimed at.

These are the most expensive artefact in this project: a person reads a month's
figures and writes the paragraph they actually want, and no amount of compute
substitutes for that. Everything here exists to make sure they are never lost
again — an earlier version of this was a throwaway script that rewrote the
worksheet in place, and it destroyed a hand-written summary twice.

So: regeneration MERGES. Answers already written are read back out of the
worksheet before it is rebuilt, and they are also kept in `gold/` as JSON, which
is version-controlled where the worksheet is not. Either source alone can
restore the other.

    uv run python -m garland_tx_data_analysis.gold_worksheet            # rebuild, keeping answers
    uv run python -m garland_tx_data_analysis.gold_worksheet --extract  # answers -> gold/ only
"""

import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from garland_tx_data_analysis.monthly_summary import stats_for
from garland_tx_data_analysis.tools import WORK_DIR, connect, ensure_schema

ROOT = Path(__file__).resolve().parent.parent.parent
WORKSHEET = ROOT / WORK_DIR / "gold_worksheet.md"
GOLD = ROOT / "gold" / "monthly_summaries.json"

HEADING = re.compile(r"^# .*\(`(\d{4}-\d{2})`\)\s*$", re.M)
ANSWER = re.compile(r"^## Your version\s*$", re.M)

# Every month is offered; a blank answer simply is not gold yet.
MONTHS_OFFERED = 16


def read_answers(path: Path) -> dict[str, str]:
    """The summaries already written into a worksheet, by month.

    Tolerates a worksheet from an older layout: it keys off the month heading
    and the answer marker, both of which have been stable.
    """
    if not path.exists():
        return {}
    text = path.read_text()
    answers: dict[str, str] = {}
    for match in HEADING.finditer(text):
        month = match.group(1)
        section = text[match.end():]
        nxt = HEADING.search(section)
        section = section[: nxt.start()] if nxt else section
        marker = ANSWER.search(section)
        if not marker:
            continue
        written = section[marker.end():]
        # Cut at the horizontal rule that separates months. Splitting on the
        # string "---" is not enough: a blank answer strips down to the rule
        # itself, which then reads as an answer and overwrites a real one.
        lines: list[str] = []
        for line in written.splitlines():
            if line.strip().strip("-") == "" and "-" in line:
                break
            lines.append(line)
        written = " ".join(" ".join(lines).split())
        # Prose or nothing. A stray fragment is not a hand-written summary, and
        # treating one as gold is how the last one got lost.
        if len(written) >= 40:
            answers[month] = written
    return answers


def load_gold() -> dict[str, str]:
    return json.loads(GOLD.read_text()) if GOLD.exists() else {}


def save_gold(answers: dict[str, str]) -> None:
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    GOLD.write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")


def pick_months() -> list[str]:
    """A spread across the archive, plus the edges that behave differently."""
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT report_month, stored_total, shortfall_rows
                     FROM monthly_reports ORDER BY report_month"""
            )
            rows = cur.fetchall()
    months = [r[0] for r in rows]
    totals = {r[0]: r[1] for r in rows}
    short = [r[0] for r in rows if r[2] > 0]
    picked = {months[0], months[-1],
              max(totals, key=totals.get), min(totals, key=totals.get)}
    if short:
        picked.add(short[0])
    picked |= {months[i] for i in range(0, len(months), max(1, len(months) // 12))}
    return sorted(picked)[:MONTHS_OFFERED]


def digest(s: dict) -> str:
    L = []
    si = s["serious_incidents"]
    L.append(f"  OFFENCES AGAINST A PERSON ({len(si)} this month, worst category first)")
    for i in si[:8]:
        where = i["area"] or "area unknown"
        street = f", {i['street']}" if i["street"] else "  [address withheld by the city]"
        L.append(f"      {i['date'] or '?':>12}  {i['offence']} — {where}{street}")
    if len(si) > 8:
        L.append(f"      ... and {len(si) - 8} more (all of them are in the JSON below)")
    L.append(f"  total ................ {s['incidents_this_month']}")
    if s["previous_month"]:
        L.append(f"  previous month ....... {s['incidents_last_month']} "
                 f"({s['previous_month']}), change {s['change_from_last_month']:+d}")
    L.append("  by category .......... " + ", ".join(
        f"{k} {v}" for k, v in s["by_category_this_month"].items()))
    L.append("  vs the whole archive .")
    for cat, h in sorted(s["category_history"].items(), key=lambda kv: -kv[1]["this_month"]):
        flag = ""
        if h["rank"] == 1:
            flag = f"   <- HIGHEST of the {h['months_compared']} months"
        elif h["rank"] == h["months_compared"]:
            flag = f"   <- LOWEST of the {h['months_compared']} months"
        L.append(f"      {cat:22} {h['this_month']:>4}   "
                 f"range {h['lowest_month_on_record']}-{h['highest_month_on_record']}   "
                 f"rank {h['rank']} of {h['months_compared']}{flag}")
    if s["biggest_category_changes"]:
        L.append("  biggest moves ........ " + ", ".join(
            f"{c['category']} {c['change']:+d} (to {c['this_month']})"
            for c in s["biggest_category_changes"]))
    else:
        L.append("  biggest moves ........ (none — no month before this one)")
    L.append("  common offences ...... " + ", ".join(
        f"{o['offence']} {o['incidents']} (a {o['within_category']})"
        for o in s["most_common_offences"]))
    L.append("  where each category concentrated")
    for cat, areas in sorted(s["category_areas"].items(),
                             key=lambda kv: -sum(a["incidents"] for a in kv[1]))[:6]:
        L.append(f"      {cat:22} " + ", ".join(
            f"{a['area']} {a['incidents']}" for a in areas))
    L.append("  busiest overall ...... " + "; ".join(
        f"{a['area']}, around {a['around']} — {a['incidents']}" for a in s["busiest_areas"]))
    return "\n".join(L)


HEADER = """# Gold summaries — worksheet

Write the summary you want under each `## Your version`. Edit nothing else.

Each month gives you a readable digest, then the exact JSON the model receives.
The digest is abridged; the JSON is the truth. If you want to write something
the JSON cannot support, say so — that is a missing field, not a bad summary,
and it is cheaper to find now than after fifteen of these.

Ground rules the gold has to obey too, or the optimiser learns to break them:
  - every number must appear in that month's JSON, copied, never calculated
  - every street must be one printed in the JSON. No others exist for a month,
    and a plausible street attached to a killing is the worst line this prints
  - where an address is withheld, give no place. That is the city's decision
  - never call the TOTAL a number of crimes — it includes information reports,
    which record that something was reported, not that an offence happened.
    The word is fine where it is accurate: burglary is a crime
  - never a district number
  - no causes, no speculation, no editorialising

Nothing dictates shape. Open with whatever you think leads, in whatever order,
in as many sentences as it takes. The shape is the whole reason to write these:
it is what an optimiser has no way to guess.

Answers already written are preserved when this file is rebuilt, and kept in
`gold/monthly_summaries.json` as well. Leave a month blank to skip it.
"""


def build(answers: dict[str, str]) -> str:
    out = [HEADER]
    for month in pick_months():
        s = stats_for(month)
        out += [f"\n---\n\n# {s['month']}  (`{month}`)\n",
                "## Digest\n```", digest(s), "```\n",
                "## The exact JSON the model is given\n```json",
                json.dumps(s, indent=1), "```\n",
                "## Your version\n", answers.get(month, ""), "\n\n"]
    return "\n".join(out)


def main(argv: list[str]) -> None:
    # Both stores are read and the union is kept. The worksheet wins where it
    # has something, because that is where editing happens — but it can only
    # ever add or replace with real prose, never blank an answer the JSON still
    # holds. Losing one of these costs a person half an hour.
    answers = load_gold()
    answers.update(read_answers(WORKSHEET))
    save_gold(answers)
    written = sorted(answers)
    print(f"{len(written)} hand-written summaries kept: {', '.join(written) or '(none yet)'}")
    print(f"gold -> {GOLD}")

    if "--extract" in argv:
        return
    WORKSHEET.parent.mkdir(parents=True, exist_ok=True)
    WORKSHEET.write_text(build(answers))
    print(f"worksheet -> {WORKSHEET}")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
