# Stage 1 — the weekly-report deep agent

Fetches Garland's weekly incident PDF, extracts it, audits the extraction
against the report's own numbers, labels the offence codes, and stores the week
for the map to render.

Built on [deepagents](https://github.com/langchain-ai/deepagents), the LangGraph
agent harness — so the agent gets a filesystem, shell, and subagent delegation
on top of the four tools defined here.

## Why an agent at all

The parsing is a regex over PDF text; that part is ordinary Python and lives in
`tools.py`. What isn't ordinary is deciding whether a given week's parse is
good enough to publish.

The PDF gives us a way to check. Every district block ends with a line reading
`District Total: N`, so the parse can be reconciled against the source rather
than trusted:

```
 DISTRICT 22
 2 22002026R036021 BURGLARY-VEH07/16/2026 23XX APOLLO RD
 2 23302026R036022 BURGLARY-VEH07/16/2026 23XX APOLLO RD
 2 08002026R036050 THEFT-ALL OTHER-$100 L/T $75007/16/2026 54XX NAAMAN FOREST BLVD
District Total: 3
```

`parse_incidents` reports that reconciliation instead of asserting success, and
where a district doesn't add up it returns that district's raw source lines
alongside the shortfall. `store_incidents` then refuses the week outright: a
numbered district short of its declared total means rows were dropped, and a
wrong week on a public map is worse than a late one.

The refusal is not advice the agent can weigh. It is arithmetic, so it is
enforced in the tool — there is no argument to be had with it and no tool
argument that turns it off.

For the 07/12/2026 report: 102 incidents declared, 98 stored, every numbered
district reconciling exactly. The missing 4 sit under the report's first block,
whose header is a bare `DISTRICT` with no number — they can't be attributed, so
they're dropped. That was always true; it just used to be invisible.

## The other way a week goes wrong

The download can quietly serve last week's file, and then everything downstream
succeeds: the parse is clean, the totals reconcile, and the map is republished
with a week it already showed. The report period is what gives it away, so
`parse_incidents` looks the period up and returns a `period_check`:

| status | meaning |
|---|---|
| `new` | no rows for this period — carry on |
| `partially-stored` | an earlier run stored part of this week and stopped; storing tops it up |
| `already-stored` | the whole week is already there — stop, the download is probably stale |
| `unknown` | the database could not be reached, which is **not** the same as nothing stored |

The distinction between the middle two matters: a stale download and a run that
died halfway both leave rows for this period behind, and one has to stop the run
while the other has to be allowed to finish.

This used to be step 2 of the agent's prompt, phrased as "if the period matches
a week already in the database, say so". The agent had no tool that could read
the database, so it could only skip the step or guess.

## Layout

```
src/garland_tx_data_analysis/
  agent.py    the deep agent, its subagent, and their prompts
  tools.py    download · parse · read raw text · which codes need a label · store
  main.py     entrypoint; streams the run so you can watch it work
tests/        41 tests over the tools
```

**Tools** — `download_weekly_report` (browser UA, verifies it really got a PDF),
`parse_incidents` (parse, reconcile, and check whether this week is already
stored), `read_report_text` (raw page text, so the
you can look at the source), `unlabelled_incident_types` (which offence
codes have never been named), `store_incidents` (label, append to Postgres,
write the map's JSON).

**Subagent** — `offence-labeller` maps `THEFT-MOTOR VEHICLE-$2,500 L/T $30,000`
to `Motor Vehicle Theft`, and is only asked about codes that have never been
labelled, which most weeks means it is not woken at all.

There used to be a second one. `extraction-auditor` read a failed parse and
returned a verdict of `trustworthy`, `trustworthy-with-losses` or
`untrustworthy` for the main agent to honour. But the condition that woke it —
a numbered district failing to reconcile — already determined that verdict, and
the diagnosis it wrote was prose about a handful of source lines that are now
simply printed. The halt moved into `store_incidents`, where it cannot be
skipped.

## Running it

Needs Python 3.11–3.14, an Anthropic API key, and a Postgres URL in `.env`:

```bash
cat > .env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://...        # Neon: main branch
TEST_DATABASE_URL=postgresql://...   # Neon: `test` branch, truncated by the suite
EOF
```

```bash
uv sync
uv run run_agent
```

The agent writes `extracted_incidents.json`, `enriched_incidents.json`, the
`incidents` rows, and a `run-report.md` describing what it did.

## Storage

History lives in Neon Postgres. `store_incidents` creates the table on first
write, so there is no separate migration step:

```sql
CREATE TABLE incidents (
    incident_id       bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    report_period     text,          -- NULL only on pre-Postgres imported rows
    district          text,
    occurred_on       date NOT NULL,
    incident          text NOT NULL,
    location          text NOT NULL,
    short_description text,
    inserted_at       timestamptz NOT NULL DEFAULT now()
);
```

Alongside it, one label per offence code:

```sql
CREATE TABLE incident_labels (
    incident          text PRIMARY KEY,
    short_description text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);
```

**A code is named once and keeps that name.** Labels used to be re-derived
every run, and 68 codes had accumulated 94 labels between them: the same code
read as `Vandalism` one week and `Criminal Mischief ($100-$750)` the next, so
the map's legend never settled. `store_incidents` now inserts with `ON CONFLICT
DO NOTHING` and applies whatever the table says, so a label supplied for a code
that already has one is ignored and reported. Stability is a constraint, not an
instruction in a prompt — the drift happened while the prompt said not to.

The model is still what names a code nobody has seen before. It just is not
asked twice.

**There is deliberately no unique constraint over the natural key**
(`report_period, district, occurred_on, incident, location`). A single week
legitimately contains several same-block, same-day, same-offence rows — 7 in the
07/12/2026 report — and a constraint would silently swallow them. Deduplication
instead compares per-key *counts*, inserting only the surplus over what the
database already holds, which is what makes a re-run add nothing.

The connection string is read from `DATABASE_URL` by the tool itself and is
never a tool argument: arguments are echoed into the model's context and the run
transcript, and the URL carries a password.

### Where a run puts things

Everything the agent downloads or writes goes to `work/`, which is gitignored:

```
work/police_incidents.pdf        the week's report, as downloaded
work/extracted_incidents.json    parser output
work/enriched_incidents.json     labelled — stage 2 reads this
work/run-report.md               what the run did, written by the agent
```

The site deploys from `incident-geo-analysis/dist/`, which *is* committed, so
none of `work/` needs to be in git.

The history before Postgres lived in a TinyDB file, `incidents.db`. Its 442
rows — spanning 2025-12-28 to 2026-07-18 — were imported once and now live in
Neon; 344 of them predate the `report_period` field and carry NULL for it. Both
that file and the script that imported it are in git history:

```bash
git show 06211cd:incident-ingest/incidents.db
git show 06211cd:incident-ingest/src/garland_tx_data_analysis/backfill.py
```

## What the history actually covers

A gap in this data looks exactly like a quiet week. `report_weeks` records
every week the pipeline has ingested, written in the same transaction as the
incidents, so coverage can never claim a week the database does not hold:

```bash
uv run python -m garland_tx_data_analysis.report_coverage
uv run python -m garland_tx_data_analysis.report_coverage 2026-01-01 2026-08-31
```

As of the 08/16/2026 run, the honest answer is **5 of 36 weeks**:

```
have    12/14/2025 - 12/20/2025   115 incidents
have    12/28/2025 - 01/03/2026    89 incidents
have    01/04/2026 - 01/10/2026    97 incidents
have    07/12/2026 - 07/18/2026    98 incidents
have    08/16/2026 - 08/22/2026    92 incidents
MISSING 31 further weeks
plus 211 rows that predate the report-period field and belong to no week
```

February through April 2026 are not quiet months. They are months nobody ran
the pipeline. That distinction has to survive into anything that summarises a
month or claims a trend, which is why the coverage statement travels with the
numbers rather than being left for a reader to assume.

## Models

| Variable | Default | Used by |
|---|---|---|
| `GARLAND_MODEL` | `anthropic:claude-haiku-4-5` | the main agent |
| `GARLAND_LABEL_MODEL` | `anthropic:claude-haiku-4-5` | the offence labeller |

Both Haiku. The main agent ran on Opus when it planned, audited and decided;
it no longer does any of those. The parse reconciles itself, the store refuses
a week that doesn't add up, the period check is a query, and a labelled code
keeps its label. What's left is calling four tools in order and writing a
readable account of the run.

If runs start dropping steps, move it back up rather than adding more prose to
the prompt:

```bash
GARLAND_MODEL=anthropic:claude-sonnet-5 uv run run_agent
```

## Tests

```bash
uv run pytest tests/
```

They cover the tools, not the agent: the download's stale-PDF guard, the
district-total reconciliation, wrapped rows, the unnumbered-header accounting,
label stability across runs, the already-stored period check, and the store's
idempotency across re-runs.

`DATABASE_URL` is removed from the environment for every test that does not ask
for the `db` fixture, so a test cannot reach the live database by forgetting to
declare that it wants one.

The six storage tests run against the Neon `test` branch and **truncate it**
between tests. They skip when `TEST_DATABASE_URL` is unset, so the suite is
green on a fresh clone with no database, and they refuse to run at all if
`TEST_DATABASE_URL` matches `DATABASE_URL`.
