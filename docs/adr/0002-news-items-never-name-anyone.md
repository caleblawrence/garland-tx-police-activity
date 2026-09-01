# News items never name anyone

Every incident this project publishes is anonymous by construction: block-level
addresses, no names, and `ADDRESS CONFIDENTIAL` wherever the city withheld a
location. News articles are the opposite — they name suspects and victims,
usually in the headline. Rather than inherit that, a featured news item refers to
people only by role: "a man was charged", never who. Titles that name someone are
rewritten by hand before they are published.

## Considered options

- **Verbatim headlines and article summaries.** Reads like a real news feed. It
  also puts arrest-is-not-conviction into a searchable public product, where a
  name attached to a charge that is later dropped outlives the correction.
- **Skipping stories that are about a person.** Would lose arrests, charges and
  verdicts — the follow-up coverage that answers "did anyone get caught", which
  the incident data can never answer on its own.

## Consequences

Displayed titles and summaries are written by hand rather than taken from the
source, which is only tractable because at most five items are ever featured.
The candidate pool holds raw headlines with names intact, so it must stay
invisible to readers: it can never become a "more coverage" list without
breaking this decision.
