# Garland TX police activity

Publishes what Garland's police department reports about crime in the city:
a weekly map of the latest week, and a browsable archive of every monthly
report back to February 2022.

## Language

### The source reports

**Weekly report**:
The city's "Previous Week Selected Incident Report" PDF. Always the latest
week, served from one fixed URL that cannot reach backwards.
_Avoid_: weekly feed, current report

**Monthly report**:
One of the city's Crime Watch Report PDFs, published monthly and archived back
to February 2022. Covers the same offence categories as the weekly report at a
coarser grain.
_Avoid_: archive report, Crime Watch

**Report period**:
The span of days a report covers, written as `MM/DD/YYYY - MM/DD/YYYY`. Used
as the key for a week: it groups a report's rows, detects a re-ingest, and
answers whether a download was stale.
_Avoid_: date range, week

**Selected offence categories**:
The eight kinds of crime the reports cover — murder, sexual assault,
aggravated assault, robbery, burglary, theft, motor vehicle theft, criminal
mischief. Named on every page of every report. Anything outside them is absent
from the source, not merely unrecorded.

### Trusting a parse

**Reconciliation**:
Checking the rows parsed out of a district block against the `District Total:
N` the report declares for that block. A district that does not reconcile means
rows were lost, and the week is refused rather than published short.
_Avoid_: validation, audit

**Shortfall**:
Incidents a report declares in its own district totals but which are absent
from its PDF text entirely. Not a parsing failure — the rows are not there to
find. Twelve monthly reports are short by 23 rows in total.
_Avoid_: missing rows, gap

**Unnumbered district**:
A `DISTRICT` header carrying no number. Its rows cannot be attributed to a
district and are dropped by design, counted and reported rather than silently
lost.

### Naming an offence

**Offence code**:
The verbose string the report uses for a crime, e.g.
`THEFT-MOTOR VEHICLE-$2,500 L/T $30,000`. Stable, ugly, and the join key for a
label.
_Avoid_: incident type, crime type

**Label**:
The short human-readable name shown for an offence code, e.g.
`Motor Vehicle Theft`. Decided once per code and never re-decided, so the map's
legend does not change from week to week.
_Avoid_: short description, display name

**Category**:
One of the eight groupings the reports themselves claim to cover, plus
`Information Report`. Coarser than a label: three dollar tiers of criminal
mischief share one category and one label.

### News

**News item**:
A published article about policing or public safety in Garland, tracked
independently of the city's reports. Its remit is deliberately wider than the
selected offence categories: a child dying in a hot car is neither a crime the
reports cover nor a record they will ever hold, and is exactly the kind of
thing this exists to surface.

One article is one news item. Follow-up coverage of an incident already
reported — an arrest, a charge, a verdict — is a new item rather than an update
to the first, so one incident may appear several times as it develops.
_Avoid_: story, article, news event

**Candidate pool**:
Every news item gathered but not featured. Invisible to readers: it is the set
to pin from, not a second tier of the page. Nothing reaches a reader unread,
which is what makes vague attribution a promise rather than a hope.
_Avoid_: backlog, inbox, drafts

**Featured**:
A news item promoted by hand to the block at the top of the incident browser.
Nothing decides this automatically — not recency, and emphatically not
severity, which would mean the page asserting which deaths matter more. At most
five are shown. Its displayed title and summary are written by hand at the time
it is featured, not derived from the article.
_Avoid_: pinned, highlighted, top story

**Vague attribution**:
The rule that people appear in a news item as roles, never as names — "a man
was charged", not who. Applies to the summary and to the title, which is
rewritten when the published headline names someone.
_Avoid_: anonymisation, redaction

**Record link**:
The optional connection from a news item to an incident in the archive. Its
absence has two different meanings — the report covering that period is not
published yet, or no report will ever contain it — and they are not
interchangeable. Made by hand, never inferred: the reports carry no identifier
a story could be matched on, and a link to the wrong incident is worse than no
link at all.
_Avoid_: match, join

### Coverage

**Coverage**:
Which report periods the database actually holds. A month absent from coverage
is a month nobody fetched, not a month without crime — the distinction any
summary or trend has to survive.
