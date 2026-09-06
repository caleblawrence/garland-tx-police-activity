"""Where a police district is, in words a reader who lives here would use.

Nobody knows where district 31 is. The number is the department's, it appears
on every report, and it means nothing to the person the page is written for.
This turns it into "north Garland, around Garland Road and Belt Line".

The mapping is a static table for the same reason `categories.py` is: there is
exactly one place that decides, and the model is not it. A district's location
has a right answer, so the pipeline settles it and puts the answer in the
figures block; the summary copies the phrase the way it copies a number. The
alternative — handing the model 26 rows and asking it to translate — is a
lookup it can get wrong with nothing checking it.

## How this was derived, and how to redo it

Areas are the area-weighted centroid of each district's polygon in
`incident-geo-analysis/src/police-districts.geojson` (the city's own ArcGIS
boundaries), bucketed against the centre of the districts' bounding box:
beyond a third of the way out is north/south and east/west; between a fifth
and a third, with no east/west component, is north-central or south-central;
anything nearer the middle is central. That middle band exists because district
42 sits at -0.28 and reads as south-central to someone who lives there — which
is what a reader called it before this file did. Landmarks are the two most frequent street names across
all 30,973 archived incidents in that district — the roads incidents actually
happen on, not the ones printed largest on a map.

It is a table rather than a computation because the Python side does not
depend on the Node side's build output, and because the buckets wanted a human
pass: `41` computes as west and reads as southwest on the department's own
map, and is left as computed. If the city redraws its districts,
regenerate rather than patch, and re-read the whole table.

## What is deliberately not here

Sectors. The reports carry a sector per district (20, 30, 40, 50) and it is the
department's own grouping, but it is a patrol grouping and not a geographic
one — sector 40 spans west, central, southwest and south. "Sector 40" is as
meaningless to a reader as "District 41", so it buys nothing.
"""

import re

# district -> (area, the two streets its incidents most often sit on)
DISTRICTS: dict[str, tuple[str, str]] = {
    "21": ("northwest", "Jupiter and Arapaho"),
    "22": ("northwest", "Naaman Forest and Garland Road"),
    "23": ("northwest", "Belt Line and Jupiter"),
    "24": ("west", "Walnut and Forest"),
    "25": ("west", "Buckingham and Walnut"),
    "26": ("west", "Walnut and Forest"),
    "27": ("west", "Jupiter and Shiloh"),
    "31": ("north", "Garland Road and Belt Line"),
    "32": ("north", "Cedar Sage and Horseshoe"),
    "33": ("northeast", "Forestbrook and Pueblo"),
    "34": ("central", "Fifth and First"),
    "35": ("central", "Castle and Lavon"),
    "36": ("central", "Miller and Edgefield"),
    "37": ("central", "First and Miller"),
    "41": ("west", "Shiloh and Garland Road"),
    "42": ("south-central", "Centerville and Miller"),
    "43": ("southwest", "LBJ Freeway and Northwest Highway"),
    "44": ("south", "Northwest Highway and Saturn"),
    "45": ("south", "Broadway and Duck Creek"),
    "46": ("southwest", "Marketplace and Centerville"),
    "51": ("south", "Centerville and La Prada"),
    "52": ("south", "I-30 and Duck Creek"),
    "53": ("southeast", "Broadway and Duck Creek"),
    "54": ("southeast", "Bobtown and I-30"),
    "55": ("southeast", "I-30 and Broadway"),
    "56": ("southeast", "I-30 and Chaha"),
}

# The order areas are reported in when two are equally busy, so a tie does not
# reshuffle the page from month to month.
AREA_ORDER = [
    "north", "northeast", "northwest", "north-central", "central",
    "east", "west", "south-central", "south", "southeast", "southwest",
]


STREET_SUFFIX = {
    "ST": "Street", "RD": "Road", "DR": "Drive", "LN": "Lane", "AVE": "Avenue",
    "BLVD": "Boulevard", "PKWY": "Parkway", "CT": "Court", "CIR": "Circle",
    "WAY": "Way", "TRL": "Trail", "PL": "Place", "FRWY": "Freeway",
    "FWY": "Freeway", "EXPY": "Expressway", "HWY": "Highway",
}

CONFIDENTIAL = "ADDRESS CONFIDENTIAL"


def street_name(location: str) -> str | None:
    """A street from an address, with the house number taken off.

    The monthly reports carry exact addresses — `302 E VISTA DR`, not the
    `3XX` block the weekly report uses. Naming the house where somebody was
    killed is a different act from naming the street, and the brief has no use
    for the number, so it never leaves this function.

    Intersections arrive as `FOREST LN / S JUPITER ROAD` and come back as
    "Forest Lane at Jupiter Road". Both halves are real streets and a summary
    may name either, so they are kept as two rather than collapsed into one
    unparseable name — which is what made a correct "Jupiter Road" read as an
    invention.

    Returns None for a withheld address. The city withholds some deliberately,
    and a summary must say nothing rather than something vague-but-locating.
    """
    raw = (location or "").upper().strip()
    if not raw or CONFIDENTIAL in raw:
        return None
    parts = [_one_street(p) for p in re.split(r"\s*/\s*", raw)]
    named = [p for p in parts if p]
    return " at ".join(named) if named else None


def _one_street(raw: str) -> str | None:
    """One side of an address: no house number, no directional, suffix spelled."""
    words = re.sub(r"^[\dX]+\s+", "", raw.strip()).split()
    if not words:
        return None
    # A leading N/S/E/W distinguishes two halves of one road. Dropped, because
    # the brief always says the area as well, and "north Garland, on Garland
    # Road" is how somebody would say it aloud.
    if words[0] in {"N", "S", "E", "W", "NE", "NW", "SE", "SW"} and len(words) > 1:
        words = words[1:]
    if len(words) > 1 and words[-1].rstrip(".") in STREET_SUFFIX:
        words = words[:-1] + [STREET_SUFFIX[words[-1].rstrip(".")]]
    return " ".join(w if w in STREET_SUFFIX.values() else w.title() for w in words)


def area_of(district: str) -> str | None:
    """The part of the city a district is in, or None if we cannot say.

    Ten district values in the archive have no boundary — `1` through `9`,
    `12` and `13`, 22 rows across four years, almost certainly typos for a
    two-digit district. There is no honest area for those, and a guess would
    put an incident in the wrong half of the city, so they get None and drop
    out of the figures rather than being placed somewhere plausible.
    """
    entry = DISTRICTS.get(str(district or "").strip())
    return entry[0] if entry else None


def landmarks_of(district: str) -> str | None:
    """The two streets that district's incidents most often sit on."""
    entry = DISTRICTS.get(str(district or "").strip())
    return entry[1] if entry else None


def busiest_areas(districts: dict[str, int], top: int = 3) -> list[dict]:
    """The busiest districts, retold as parts of the city.

    Deliberately NOT a sum of incidents per area. Areas hold unequal numbers of
    districts — four in the southeast, two in the north — so adding counts
    within them ranks the biggest bucket rather than the busiest place, and the
    buckets are drawn by the threshold in this file. Summing made the southeast
    "busiest" on a month whose single busiest district was in the north.

    So this changes the vocabulary and nothing else: the same districts the
    figures always ranked, named the way a reader would name them. Where two of
    the top districts land in one area, the area appears once with the larger
    count, because saying "north Garland" twice tells a reader nothing.
    """
    ranked = sorted(districts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[dict] = []
    seen: set[str] = set()
    for district, count in ranked:
        area = area_of(district)
        if area is None or area in seen:
            continue
        seen.add(area)
        out.append(
            {
                "area": f"{area} Garland",
                "around": landmarks_of(district) or "",
                "incidents": count,
            }
        )
        if len(out) == top:
            break
    return out
