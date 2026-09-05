"""What makes a monthly summary publishable, and what the optimiser rewards.

This metric decides what a prompt optimisation run is pulling towards. A
mistake here does not fail loudly — it quietly produces a prompt tuned for the
wrong thing — so the rules it claims to enforce are pinned down here.
"""

from garland_tx_data_analysis.summary_metric import evaluate_summary

STATS = {
    "month": "June 2026",
    "previous_month": "May 2026",
    "incidents_this_month": 442,
    "incidents_last_month": 417,
    "change_from_last_month": 25,
    "by_category_this_month": {
        "Theft": 199, "Burglary": 96, "Information Report": 22, "Robbery": 3,
    },
    "biggest_category_changes": [
        {"category": "Theft", "this_month": 199, "last_month": 172, "change": 27},
        {"category": "Robbery", "this_month": 3, "last_month": 14, "change": -11},
    ],
    "busiest_areas": [
        {"area": "north Garland", "around": "Garland Road and Belt Line",
         "incidents": 33},
    ],
}

CLEAN = (
    "Garland police reported 442 incidents in June 2026, up from 417 in May. "
    "Theft was the most common crime at 199, up 27, while robbery fell by 11 to 3. "
    "North Garland, around Garland Road and Belt Line, recorded the most at 33."
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
    result = evaluate_summary(
        CLEAN.replace("up from 417 in May", "a 7.2% rise on May"), STATS
    )
    assert not result.publishable
    assert result.score < 0.25
    assert "7.2" in result.feedback


def test_calling_the_total_a_crime_count_is_blocked_but_the_word_survives():
    """The total includes 22 information reports, so "442 crimes" is false.

    The rule is about the aggregate, not the word. Burglary is a crime and may
    be called one, and `Criminal Mischief` is a category name that has to
    survive the rule outright.
    """
    assert not evaluate_summary(
        CLEAN.replace("442 incidents", "442 crimes"), STATS
    ).publishable

    # The same claim without the number is the same claim.
    assert not evaluate_summary(
        CLEAN.replace("Theft was the most common crime at 199, up 27,",
                      "Crime rose overall,"), STATS
    ).publishable

    # ...but the word used accurately is fine, and so is the category name.
    assert evaluate_summary(CLEAN, STATS).publishable
    assert evaluate_summary(
        CLEAN.replace("while robbery fell by 11 to 3",
                      "while criminal mischief and robbery fell, robbery by 11 to 3"),
        STATS,
    ).publishable


def test_a_district_number_is_a_publish_blocker():
    """Districts are not in the figures, so any number written is unfounded."""
    result = evaluate_summary(
        CLEAN.replace("North Garland, around Garland Road and Belt Line,",
                      "District 31"), STATS
    )
    assert not result.publishable
    assert "district" in result.feedback.lower()


def test_suggesting_a_cause_is_a_publish_blocker_however_hedged():
    """A hedge is a cause with a disclaimer, and reads the same on the page."""
    for clause in [
        " The fall was driven by fewer thefts.",
        " The fall was likely seasonal.",
        " The decrease reflects increased patrols.",
        " Thefts fell, possibly because of a new initiative.",
    ]:
        assert not evaluate_summary(CLEAN + clause, STATS).publishable, clause


def test_describing_a_relationship_between_two_figures_is_not_explaining():
    """The line the prompt draws: relate the figures, never reason past them."""
    assert evaluate_summary(
        CLEAN.replace("while robbery fell by 11 to 3",
                      "and robbery fell by 11 to 3 over the same month"),
        STATS,
    ).publishable


def test_dramatising_a_change_costs_score():
    """Both words the model actually reached for, and scored full marks on."""
    for word in ["notably", "significantly"]:
        result = evaluate_summary(CLEAN.replace("up 27", f"up {word} by 27"), STATS)
        assert result.soft, word


def test_every_unpublishable_summary_scores_below_every_publishable_one():
    """The band gap is what stops the optimiser trading a rule for polish.

    A rejected summary that is otherwise beautifully written must still lose to
    a publishable one that is merely adequate.
    """
    rejected = evaluate_summary(CLEAN.replace("442 incidents", "442 crimes"), STATS)
    adequate = evaluate_summary(
        "There were 442 reported incidents in June 2026, up from 417 in May. "
        "Theft reached 199. North Garland recorded the most.", STATS)
    assert not rejected.publishable and adequate.publishable
    assert rejected.score < adequate.score


def test_form_and_coverage_lapses_cost_score_without_blocking_publication():
    thin = "There were 442 reported incidents. Nothing else is noted."
    result = evaluate_summary(thin, STATS)
    assert result.publishable
    assert 0.5 <= result.score < 1.0
    assert result.soft


def test_the_first_month_is_not_marked_down_for_a_comparison_it_cannot_make():
    first = dict(STATS, month="February 2022", previous_month=None,
                 incidents_last_month=None, change_from_last_month=None)
    text = ("Garland police reported 442 incidents in February 2022. Theft was "
            "the most common crime at 199, and robbery accounted for 3. "
            "North Garland, around Garland Road and Belt Line, recorded 33.")
    result = evaluate_summary(text, first)
    assert result.publishable and not result.soft


def test_an_empty_reply_scores_zero_rather_than_raising():
    """GEPA runs hundreds of generations; one bad reply must not end a run."""
    result = evaluate_summary("", STATS)
    assert result.score == 0.0 and not result.publishable


def test_feedback_names_the_rule_and_quotes_what_broke_it():
    """The reflection model reads this text, so it has to be actionable."""
    result = evaluate_summary(
        "Garland saw 442 crimes in June 2026, a 7.2% drop likely due to patrols.",
        STATS,
    )
    assert "7.2" in result.feedback
    assert "information reports" in result.feedback
    assert "reported incidents" in result.feedback
