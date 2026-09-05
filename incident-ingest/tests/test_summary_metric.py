"""What makes a monthly summary publishable, and what the optimiser rewards.

This metric decides what a prompt optimisation run is pulling towards. A
mistake here does not fail loudly — it quietly produces a prompt tuned for the
wrong thing — so the rules it claims to enforce are pinned down here.
"""

from garland_tx_data_analysis.summary_metric import evaluate_summary

STATS = {
    "month": "June 2026",
    "previous_month": "May 2026",
    "incidents_this_month": 604,
    "incidents_last_month": 651,
    "change_from_last_month": -47,
    "by_category_this_month": {"Theft": 288, "Burglary": 96, "Robbery": 21},
    "biggest_category_changes": [
        {"category": "Theft", "this_month": 288, "last_month": 320, "change": -32}
    ],
    "busiest_districts": {"23": 71},
}

CLEAN = (
    "Garland police reported 604 incidents in June 2026, down from 651 in May. "
    "Theft accounted for 288 of them and burglary for 96. District 23 recorded "
    "71, more than any other district."
)


def test_a_clean_summary_scores_in_the_publishable_band():
    result = evaluate_summary(CLEAN, STATS)
    assert result.publishable
    assert result.score >= 0.5
    assert not result.hard and not result.soft


def test_a_number_not_in_the_figures_block_is_a_publish_blocker():
    """The check the pipeline already enforces, and the one that matters most.

    `7.2%` is a real calculation from the real figures and is still refused:
    the model may copy a number, never derive one.
    """
    invented = (
        "Garland police reported 604 incidents in June 2026, a 7.2% fall from "
        "May. Theft accounted for 288 and burglary for 96."
    )
    result = evaluate_summary(invented, STATS)
    assert not result.publishable
    assert result.score < 0.25
    assert "7.2" in result.feedback


def test_calling_it_crime_is_a_publish_blocker_but_criminal_mischief_is_not():
    """Eight categories is a narrower thing than crime, and the page says so.

    `Criminal Mischief` is one of those categories and has to survive the rule
    that bans the word it starts with.
    """
    assert not evaluate_summary(
        CLEAN.replace("incidents in June", "crimes in June"), STATS
    ).publishable

    named = (
        "Garland police reported 604 incidents in June 2026, down from 651 in "
        "May. Criminal mischief and theft made up most of them, with theft at 288."
    )
    assert evaluate_summary(named, STATS).publishable


def test_suggesting_a_cause_is_a_publish_blocker_however_hedged():
    """A hedge is a cause with a disclaimer, and reads the same on the page."""
    for clause in [
        "The fall was driven by fewer thefts.",
        "The fall was likely seasonal.",
        "The decrease reflects increased patrols.",
        "Thefts fell, possibly because of a new initiative.",
    ]:
        result = evaluate_summary(CLEAN + " " + clause, STATS)
        assert not result.publishable, clause


def test_every_unpublishable_summary_scores_below_every_publishable_one():
    """The band gap is what stops the optimiser trading a rule for polish.

    A rejected summary that is otherwise beautifully written must still lose to
    a publishable one that is merely adequate.
    """
    polished_but_rejected = (
        "Garland police reported 604 incidents in June 2026, down 7.2% from "
        "May. Theft accounted for 288 and burglary for 96. District 23 "
        "recorded 71, more than any other."
    )
    adequate = "There were 604 reported incidents in June 2026, down from 651 in May, including 288 thefts and 96 burglaries."

    rejected = evaluate_summary(polished_but_rejected, STATS)
    passed = evaluate_summary(adequate, STATS)
    assert not rejected.publishable
    assert rejected.score < passed.score


def test_form_and_coverage_lapses_cost_score_without_blocking_publication():
    """A publishable summary that covers the month badly still scores lower."""
    thin = "There were 604 reported incidents. Nothing else is noted."
    result = evaluate_summary(thin, STATS)
    assert result.publishable
    assert 0.5 <= result.score < 1.0
    assert result.soft


def test_the_first_month_is_not_marked_down_for_a_comparison_it_cannot_make():
    first = dict(STATS, month="February 2022", previous_month=None,
                 incidents_last_month=None, change_from_last_month=None)
    text = ("Garland police reported 604 incidents in February 2022. Theft "
            "accounted for 288 of them and burglary for 96.")
    result = evaluate_summary(text, first)
    assert result.publishable and not result.soft


def test_an_empty_reply_scores_zero_rather_than_raising():
    """GEPA runs hundreds of generations; one bad reply must not end a run."""
    result = evaluate_summary("", STATS)
    assert result.score == 0.0 and not result.publishable


def test_feedback_names_the_rule_and_quotes_what_broke_it():
    """The reflection model reads this text, so it has to be actionable."""
    result = evaluate_summary(
        "Garland saw 604 crimes in June 2026, a 7.2% drop likely due to patrols.",
        STATS,
    )
    assert "7.2" in result.feedback
    assert "crime" in result.feedback.lower()
    assert "reported incidents" in result.feedback
