"""Score a monthly summary against the rules the prompt already states.

`monthly_summary.verify` answers the question that matters most — does this
paragraph contain a number nothing in the stats block accounts for — and a
summary that fails it is discarded rather than shown. That check stays
load-bearing here; this module adds the rest of the prompt's own rules so a
candidate prompt can be scored without a person reading 53 paragraphs.

Nothing below is new policy. Every check is a rule already written in
`monthly_summary.SYSTEM_PROMPT` or in that module's docstring. This is those
rules made executable, which is the whole reason a prompt optimiser can be
pointed at them.

Two bands, and the gap between them is the point:

  - a summary breaking a publish-blocker scores under 0.25, because it would
    never be shown. The score still varies inside that band, so a paragraph
    that broke one rule is distinguishable from one that broke all three.
  - a publishable summary scores from 0.5 up, on how well it covers the month.

No unpublishable candidate can outrank a publishable one, and there is still a
gradient inside each band for the optimiser to climb.

The feedback strings matter as much as the score. GEPA's reflection step reads
them, so each one names the rule, quotes what broke it, and says what to do
instead — the way a note in review would.
"""

import re
from dataclasses import dataclass, field

from garland_tx_data_analysis.monthly_summary import verify

# The eight the reports cover. `Information Report` and `Other` are excluded:
# they are this project's bookkeeping, not something a paragraph should name.
REPORTED_CATEGORIES = (
    "murder",
    "sexual assault",
    "aggravated assault",
    "robbery",
    "burglary",
    "theft",
    "motor vehicle theft",
    "criminal mischief",
)

# "Never call this 'crime'." `criminal mischief` is a category name and must
# survive, so the word is matched on its own rather than as a prefix.
CRIME_WORD = re.compile(r"\bcrimes?\b", re.I)

# "Never explain why anything changed. No causes, no speculation, no 'likely',
# no 'driven by'." Speculation is included because a hedge is a cause with a
# disclaimer attached, and reads on the page as the same claim.
CAUSAL = re.compile(
    r"\b(?:because|due to|driven by|likely|caused by|as a result|attributed to"
    r"|owing to|stems? from|stemming from|thanks to|explains?|why"
    r"|reflects?|reflecting|suggests?|suggesting|indicates?|indicating"
    r"|possibly|perhaps|may have|appears to|seems? to)\b",
    re.I,
)

# "Do not editorialise about whether a month was good or bad, safe or unsafe."
# The dramatising verbs are here too: `surged` asserts a character to a change
# that a count of reported incidents cannot carry.
EDITORIAL = re.compile(
    r"\b(?:good|bad|safe|unsafe|dangerous|concerning|alarming|worrying"
    r"|troubling|encouraging|reassuring|worryingly|dramatic(?:ally)?"
    r"|surge[ds]?|spike[ds]?|plummet(?:ed|ing)?|soar(?:ed|ing)?)\b",
    re.I,
)

# "Plain sentences. No headings, no bullets, no markdown."
MARKDOWN = re.compile(r"(?:^|\n)\s*(?:[-*+]\s|#{1,6}\s)|\*\*|__|`")

SENTENCE = re.compile(r"[^.!?]+[.!?]")

# Enough to show the month moved, without demanding a particular phrasing.
COMPARISON = re.compile(
    r"\b(?:more|fewer|less|up|down|higher|lower|increase[ds]?|decrease[ds]?"
    r"|rose|fell|compared|than)\b",
    re.I,
)


@dataclass
class Score:
    """One summary's verdict: a number for GEPA, and prose for its reflection."""

    score: float
    publishable: bool
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    feedback: str = ""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.findall(text) if s.strip()]


def _mentions_total(text: str, stats: dict) -> bool:
    total = stats.get("incidents_this_month")
    if total is None:
        return False
    return str(total) in text or f"{total:,}" in text


def _mentions_comparison(text: str, stats: dict) -> bool:
    """Whether the paragraph puts the month next to the one before it."""
    prior = stats.get("incidents_last_month")
    change = stats.get("change_from_last_month")
    for value in (prior, change, abs(change) if change is not None else None):
        if value is not None and (str(value) in text or f"{value:,}" in text):
            return True
    previous = (stats.get("previous_month") or "").split(" ")[0]
    return bool(previous and previous.lower() in text.lower()
                and COMPARISON.search(text))


def _named_categories(text: str) -> list[str]:
    low = text.lower()
    return [c for c in REPORTED_CATEGORIES if c in low]


def evaluate_summary(text: str, stats: dict) -> Score:
    """Score one paragraph against the stats block it was written from."""
    text = " ".join((text or "").split())
    hard: list[str] = []
    soft: list[str] = []

    if not text:
        return Score(0.0, False, ["empty"], [], "The model returned nothing.")

    # --- publish-blockers -------------------------------------------------
    unverified = verify(text, stats)
    if unverified:
        hard.append(
            f"Invented numbers: {', '.join(sorted(set(unverified)))}. Every "
            "numeral must be copied from the figures block. Nothing may be "
            "totalled, differenced or turned into a percentage — if the "
            "number you want is not in the block, write the sentence without "
            "it. This alone makes the summary unpublishable; it is discarded "
            "rather than shown."
        )

    said_crime = CRIME_WORD.findall(text)
    if said_crime:
        hard.append(
            f"Called this \"{said_crime[0]}\". These are reported incidents in "
            "eight named categories, which is a narrower thing than crime. "
            "Write \"reported incidents\", or name the categories."
        )

    causal = CAUSAL.findall(text)
    if causal:
        hard.append(
            f"Explained or speculated about a change: \"{causal[0]}\". The "
            "data cannot support a cause and suggesting one on a public page "
            "about policing does real harm. State what the figures show and "
            "stop there — no causes, no hedged causes."
        )

    # --- form and coverage ------------------------------------------------
    sentences = _sentences(text)
    if not 2 <= len(sentences) <= 4:
        soft.append(
            f"Wrote {len(sentences)} sentence(s); the brief asks for 2 to 4."
        )

    if MARKDOWN.search(text):
        soft.append(
            "Used markdown or a bullet. This is rendered as a plain paragraph, "
            "so formatting characters appear literally on the page."
        )

    editorial = EDITORIAL.findall(text)
    if editorial:
        soft.append(
            f"Editorialised: \"{editorial[0]}\". Do not characterise a month "
            "as good, bad, safe or unsafe, and do not dramatise a change."
        )

    if not _mentions_total(text, stats):
        soft.append(
            f"Never gave the month's total ({stats.get('incidents_this_month')}). "
            "It is the first thing a reader wants."
        )

    # A first month has nothing to compare against, so this is not asked of it.
    if stats.get("previous_month") and not _mentions_comparison(text, stats):
        soft.append(
            "Never compared the month with the one before it. Say how the "
            "total moved, using figures from the block."
        )

    named = _named_categories(text)
    if len(named) < 2:
        soft.append(
            f"Named {len(named)} of the eight categories. Say which ones moved, "
            "by name, rather than describing movement in the abstract."
        )

    return _to_score(hard, soft, bool(stats.get("previous_month")))


# Soft checks that apply to a month with a predecessor. The first month in the
# archive is scored out of one fewer, so it is not marked down for a
# comparison it could not make.
_SOFT_CHECKS = 6
_HARD_CHECKS = 3


def _to_score(hard: list[str], soft: list[str], has_prior: bool) -> Score:
    total_soft = _SOFT_CHECKS if has_prior else _SOFT_CHECKS - 1
    soft_fraction = max(0.0, (total_soft - len(soft)) / total_soft)

    if hard:
        hard_fraction = (_HARD_CHECKS - len(hard)) / _HARD_CHECKS
        score = 0.25 * (0.5 * hard_fraction + 0.5 * soft_fraction)
    else:
        score = 0.5 + 0.5 * soft_fraction

    if hard:
        lead = ("This summary would be rejected and never shown. "
                "Publish-blockers:\n")
        body = "\n".join(f"  - {h}" for h in hard)
        tail = ("\nAlso weak:\n" + "\n".join(f"  - {s}" for s in soft)) if soft else ""
    elif soft:
        lead = "Publishable, but weaker than it should be:\n"
        body = "\n".join(f"  - {s}" for s in soft)
        tail = ""
    else:
        lead = ("Publishable and complete: every number checks out against the "
                "figures block, no cause is suggested, the word \"crime\" is "
                "avoided, and the month is covered in 2 to 4 plain sentences.")
        body = tail = ""

    return Score(
        score=round(score, 4),
        publishable=not hard,
        hard=hard,
        soft=soft,
        feedback=lead + body + tail,
    )
