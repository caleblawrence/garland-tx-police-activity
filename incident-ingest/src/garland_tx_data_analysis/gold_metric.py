"""Score a summary against a hand-written one for the same month.

The rule checks in `summary_metric` define the floor — nothing invented, no
cause suggested, the total is not a crime count. They cannot say which of two
publishable summaries is better, and by design they never will: that is taste,
and the way to capture taste is to write some and compare.

So this measures agreement with a person's choices, not similarity to their
prose. Deliberately not string overlap, which would reward copying and punish
a good paraphrase. What it compares:

  - which FIGURES were worth citing. The strongest signal by far. A month
    offers dozens and the gold picks eight; picking the same eight is most of
    writing the same summary.
  - which CATEGORIES were named.
  - which PLACES were named.
  - how LONG it ran, in sentences.

The gold text itself goes into the feedback verbatim, and that matters more
than the score. GEPA's reflection step reads feedback, so showing it "here is
what the model wrote, here is what a person wrote from the same figures" lets
it extract the rule directly — which is a far richer signal than any number.
"""

import re
from dataclasses import dataclass

from garland_tx_data_analysis.summary_metric import (
    REPORTED_CATEGORIES,
    SENTENCE,
    evaluate_summary,
)

NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

# Areas the summaries talk in, longest first so "south-central" is not eaten by
# "south" when both could match.
AREAS = sorted(
    ["north-central", "south-central", "northeast", "northwest", "southeast",
     "southwest", "central", "north", "south", "east", "west"],
    key=len, reverse=True,
)


@dataclass
class GoldScore:
    score: float
    publishable: bool
    feedback: str


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip("%") for n in NUMERAL.findall(text)}


def _categories(text: str) -> set[str]:
    low = text.lower()
    return {c for c in REPORTED_CATEGORIES if c in low}


def _areas(text: str) -> set[str]:
    low = text.lower()
    found, seen = set(), low
    for area in AREAS:
        if area in seen:
            found.add(area)
            seen = seen.replace(area, " ")
    return found


def _f1(got: set, want: set) -> float:
    """Agreement on a set of choices. 1.0 when they match, 0.0 when disjoint."""
    if not got and not want:
        return 1.0
    if not got or not want:
        return 0.0
    overlap = len(got & want)
    if not overlap:
        return 0.0
    precision, recall = overlap / len(got), overlap / len(want)
    return 2 * precision * recall / (precision + recall)


def _sentence_count(text: str) -> int:
    return len([s for s in SENTENCE.findall(text) if s.strip()])


def evaluate_against_gold(text: str, stats: dict, gold: str) -> GoldScore:
    """One candidate summary, against the hand-written one for that month."""
    floor = evaluate_summary(text, stats)
    if floor.hard:
        # A refusal is a refusal however well it matches the gold. Same band
        # gap as the rule metric: nothing unpublishable outranks anything
        # publishable, and the feedback still says what a person would have
        # written, because that is what the next attempt needs to see.
        return GoldScore(
            score=round(0.25 * floor.score, 4),
            publishable=False,
            feedback=floor.feedback + "\n\nWhat a person wrote from these same "
                     f"figures:\n  {gold}",
        )

    got_n, want_n = _numbers(text), _numbers(gold)
    got_c, want_c = _categories(text), _categories(gold)
    got_a, want_a = _areas(text), _areas(gold)
    n_got, n_want = _sentence_count(text), _sentence_count(gold)

    # Figures carry the most weight: choosing what is worth citing is most of
    # the judgment in one of these.
    parts = [
        (0.45, _f1(got_n, want_n)),
        (0.20, _f1(got_c, want_c)),
        (0.20, _f1(got_a, want_a)),
        (0.15, max(0.0, 1 - abs(n_got - n_want) / 3)),
    ]
    agreement = sum(weight * value for weight, value in parts)

    notes = []
    missed_n = want_n - got_n
    extra_n = got_n - want_n
    if missed_n:
        notes.append(f"Figures a person chose and this did not: "
                     f"{', '.join(sorted(missed_n, key=len))}.")
    if extra_n:
        notes.append(f"Figures this cited and a person did not: "
                     f"{', '.join(sorted(extra_n, key=len))}. Citing more is "
                     "not better; the month offers dozens and the point is to "
                     "pick.")
    if want_c - got_c:
        notes.append(f"Categories left out: {', '.join(sorted(want_c - got_c))}.")
    if want_a - got_a:
        notes.append(f"Places left out: {', '.join(sorted(want_a - got_a))}.")
    if n_got != n_want:
        notes.append(f"Ran to {n_got} sentences where a person wrote {n_want}.")

    feedback = (
        "What a person wrote from these same figures:\n"
        f"  {gold}\n\n"
        "What this wrote:\n"
        f"  {text}\n"
    )
    if notes:
        feedback += "\nWhere they differ:\n" + "\n".join(f"  - {n}" for n in notes)
    else:
        feedback += ("\nSame figures, same categories, same places, same "
                     "length as the hand-written one.")
    if floor.soft:
        feedback += "\n\nAlso:\n" + "\n".join(f"  - {s}" for s in floor.soft)

    return GoldScore(
        score=round(0.5 + 0.5 * agreement, 4),
        publishable=True,
        feedback=feedback,
    )
