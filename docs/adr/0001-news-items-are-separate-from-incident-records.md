# News items are separate from incident records

Garland publishes crime data in three shapes we have already had to keep apart:
a weekly report, a monthly archive, and the FBI's aggregate figures. News
coverage is a fourth, and joining it to the incident records would repeat a
mistake we have avoided three times: the two describe overlapping events at
different grains, with no shared identifier, so a duplicate is indistinguishable
from two real things. News items therefore live in their own table, may carry an
optional link to an archive incident, and never merge with one.

## Considered options

- **A join view over the archive.** Only works when a record exists. The stories
  worth featuring — the March 2025 explosive devices near Wynne Park, the August
  2026 hot-car death — have no record and never will, because no offence in the
  reports' eight categories was charged.
- **A single "notable event" entity** holding news, records, or both. Rejected
  because it makes one table mean three things, and because the archive already
  displays every record on the same page: a record nobody covered is not missing
  from the page, it is simply below the block.

## Consequences

The block's remit is wider than the archive's. It carries anything about Garland
policing or public safety, including events the reports structurally cannot
contain, which is the reason the feature exists.
