"""Group offence codes into the categories the report itself claims to cover.

Every page of every report carries the same line:

    Murder (incl Trafc), Sexual Assault, Aggravated Assault, Robbery,
    Burglary, Theft, Motor Vehicle Theft, Criminal Mischief

That is the publisher's own taxonomy, which is why it is used here rather than
one invented for this project: it is what the data claims to be, and a reader
can check it against the source.

Why this exists at all: the archive holds 189 distinct offence codes, and only
the 68 the weekly pipeline has met carry a plain-English label. A picklist of
189 entries mixing `Vehicle Burglary` with `THEFT-ALL OTHER-TWO OR MORE
PREVIOUS CONVICTIONS L/T $2500` is not a filter anyone can use, and the two
spellings look like duplicates because semantically they nearly are.

Categories are deliberately coarse. `BURGLARY-VEH` is filed under Burglary
because the report files it there, even though UCR would call it larceny — the
point is to agree with the source, not to reclassify it.
"""

import re

MURDER = "Murder"
SEXUAL_ASSAULT = "Sexual Assault"
AGGRAVATED_ASSAULT = "Aggravated Assault"
ROBBERY = "Robbery"
BURGLARY = "Burglary"
THEFT = "Theft"
MOTOR_VEHICLE_THEFT = "Motor Vehicle Theft"
CRIMINAL_MISCHIEF = "Criminal Mischief"
INFORMATION_REPORT = "Information Report"
OTHER = "Other"

# The eight the report names, then the two this data actually also contains.
# Order is the order they are offered in; the report's own order is kept for
# the eight so the page reads like the source it came from.
CATEGORIES = [
    MURDER,
    SEXUAL_ASSAULT,
    AGGRAVATED_ASSAULT,
    ROBBERY,
    BURGLARY,
    THEFT,
    MOTOR_VEHICLE_THEFT,
    CRIMINAL_MISCHIEF,
    INFORMATION_REPORT,
    OTHER,
]

# Stripped before matching: another agency's case is still the same offence.
OTHER_AGENCY_PREFIX = re.compile(r"^OTHER AGENCY-\s*")

# Checked in order — the first match wins, so the specific cases come first.
# `THEFT-MOTOR VEHICLE` must beat the general `THEFT`, and the vehicle-parts
# codes must not: stealing a catalytic converter is theft, stealing the car is
# motor vehicle theft.
RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"MURDER|HOMICIDE"), MURDER),
    (re.compile(r"^CRIMINAL SOLICITATION-MINOR.*SEX"), SEXUAL_ASSAULT),
    (re.compile(r"^SEXUAL ASSLT|SEX ASSLT"), SEXUAL_ASSAULT),
    (re.compile(r"^ASSAULT-AGG|^INJURY TO A|^ARSON.*BODILY INJURY"), AGGRAVATED_ASSAULT),
    (re.compile(r"^ROBBERY"), ROBBERY),
    (re.compile(r"^UNAUTHORIZED USE|^THEFT-MOTOR VEHICLE"), MOTOR_VEHICLE_THEFT),
    (re.compile(r"^BURGLARY|BURGLARY OF VEHICLE"), BURGLARY),
    (re.compile(r"^CRIMINAL MISCHIEF|^CRIM MISCHIEF"), CRIMINAL_MISCHIEF),
    (re.compile(r"^INFO-"), INFORMATION_REPORT),
    (re.compile(r"^THEFT|RETAIL THEFT"), THEFT),
]


def categorise(code: str) -> str:
    """The report's own category for one offence code.

    Falls back to `Other` rather than guessing. Ten codes and 32 rows out of
    30,973 land there today; a new code appearing in `Other` is the signal to
    add a rule, and is visible on the page rather than silently absorbed into
    a category it does not belong to.
    """
    normalised = OTHER_AGENCY_PREFIX.sub("", (code or "").upper().strip())
    for pattern, category in RULES:
        if pattern.search(normalised):
            return category
    return OTHER


# Severity, for a brief that opens with the worst thing that happened rather
# than the most numerous. The order is the report's own — every page lists the
# categories in this sequence — so it is the publisher's ranking rather than
# one invented here, on the same principle as the categories themselves.
#
# It stops at Robbery. Below that the categories are property offences, and a
# brief that opened "the most serious offence was a burglary" every quiet month
# would be a crime blotter rather than a summary of one.
SEVERE_CATEGORIES = [MURDER, SEXUAL_ASSAULT, AGGRAVATED_ASSAULT, ROBBERY]

# Sexual assault is ranked but never described. The city withholds the address
# of every one of these — that withholding is a decision about a victim's
# safety, not a gap — and the offence labels carry detail ("Aggravated Child
# Sexual Assault") that in a month with one report, in a named part of a city
# of this size, works to identify. So the brief may say the category and the
# count and nothing else. Same reasoning as ADR 0002: the page names roles,
# never people, and a detail that narrows to one person is a name in effect.
NEVER_DESCRIBED = {SEXUAL_ASSAULT}
