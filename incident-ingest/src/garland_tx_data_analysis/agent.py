"""The weekly-report deep agent.

A LangGraph deep agent (via the `deepagents` harness) that fetches Garland's
weekly incident PDF, extracts it, labels any offence code it has not seen
before, stores the week, and reports what it did.

The tools do the deterministic work, and where a decision has a right answer
they make it rather than reporting it upward: the parse either reconciles
against the report's own `District Total: N` lines or the store refuses the
week, and a code that already has a label keeps it. What is left for the model
is the part with no right answer — naming a new offence code, and writing an
account of the run that a person can read.

There used to be an `extraction-auditor` subagent here, asked to read a failed
parse and return a verdict the main agent would honour. The verdict was
already determined by the arithmetic that woke it, and the diagnosis it wrote
was prose about eight lines of source that are now simply printed. See
`_district_block_lines` and `_reconciliation_gate` in tools.py.
"""

import os
from pathlib import Path

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend

from garland_tx_data_analysis.tools import (
    download_weekly_report,
    parse_incidents,
    read_report_text,
    store_incidents,
    unlabelled_incident_types,
)

PDF_URL = (
    "https://www.garlandtx.gov/DocumentCenter/View/802/"
    "Previous-Week-Selected-Incident-Report-PDF?bidId="
)
WORK_DIR = "work"
PDF_PATH = f"{WORK_DIR}/police_incidents.pdf"
INCIDENTS_JSON_PATH = f"{WORK_DIR}/extracted_incidents.json"
ENRICHED_JSON_PATH = f"{WORK_DIR}/enriched_incidents.json"

# The main agent plans, audits and decides, so it runs on the strongest model.
# Relabelling offence codes is mechanical string work, and this project has
# always used Haiku for it — keep that.
MODEL = os.getenv("GARLAND_MODEL", "anthropic:claude-opus-5")
LABEL_MODEL = os.getenv("GARLAND_LABEL_MODEL", "anthropic:claude-haiku-4-5")


OFFENCE_LABELLER = SubAgent(
    name="offence-labeller",
    description=(
        "Names offence codes that have never been seen before. Delegate ONLY "
        "the codes `unlabelled_incident_types` reports as needing a label — "
        "never the full list of types in a report — and it returns the mapping "
        "as JSON. Codes that already have a label keep it, so sending one here "
        "again cannot change it and only spends tokens."
    ),
    system_prompt="""You turn Garland police offence codes into plain English.

You are given offence codes this project has never labelled before — usually a
handful, sometimes one. Return a JSON object mapping every one of those
strings, verbatim as the key, to a concise human-readable label.

The label you choose is permanent. It is stored and reused for that code in
every future week, and it appears in the map's legend, so it must read well
next to labels already chosen: "Vehicle Burglary", "Motor Vehicle Theft",
"Shoplifting", "Vandalism", "Attempted Building Burglary".

Examples of the transformation:
  "THEFT-MOTOR VEHICLE-$2,500 L/T $30,000"  -> "Motor Vehicle Theft"
  "BURGLARY-VEH"                            -> "Vehicle Burglary"
  "CRIMINAL MISCHIEF $100 L/T $750"         -> "Vandalism"
  "ASSAULT-AGG-D/W"                         -> "Aggravated Assault"

Rules:
  - Cover EVERY code you are given. A missing key falls back to the verbose
    code and shows up that way on a public map.
  - Keys must be byte-identical to the input strings, dollar amounts and all.
  - Labels describe the offence, not its severity tier or dollar threshold.
  - `(CRIM ATT)` means criminal attempt: the offence was attempted, not
    completed. Keep that — "Attempted Building Burglary", never "Building
    Burglary". Dropping it tells a reader something happened that did not.
  - Keep labels title-case and under about five words.

Return only the JSON object.""",
    model=LABEL_MODEL,
)


SYSTEM_PROMPT = f"""You publish Garland TX's weekly police incident report.

Each run fetches the city's latest weekly PDF, extracts the incidents, checks
the extraction, labels the offence codes, and stores the week for the map that
renders it.

Two kinds of path, and mixing them up wastes a turn:

  - The pipeline tools below take paths relative to the project, so the
    parse output is `{INCIDENTS_JSON_PATH}`.
  - Your file tools (`ls`, `read_file`, `write_file`, `glob`, `grep`) are
    rooted at the run directory `{WORK_DIR}/`. The same file is
    `/extracted_incidents.json` to them. Nothing outside that directory is
    reachable, by design.

The tools available to you:
  - `download_weekly_report` — fetch the PDF. It verifies the response really
    is a PDF and raises if not.
  - `parse_incidents` — parse the PDF to JSON. Returns a summary including a
    `reconciliation` block checking the parse against the report's own
    district totals, and a `period_check` block saying whether this week is
    already in the database.
  - `read_report_text` — read the PDF's raw text when you need to see the
    source yourself.
  - `unlabelled_incident_types` — which offence codes in this week's report
    have never been given a label. Usually none.
  - `store_incidents` — label, append to the Postgres database, and write the
    enriched JSON the map reads. It takes no connection details; it reads them
    from the environment itself. It refuses a week whose parse did not
    reconcile against the report's district totals.

The run, and the standing constraints on it:

1. Download the report from {PDF_URL} to `{PDF_PATH}`.

2. Parse it to `{INCIDENTS_JSON_PATH}`, then read `period_check.status` in the
   summary. It has already compared this week against the database; you cannot
   check that yourself, so take what it says.

     - `new` — go on to step 3.
     - `partially-stored` — an earlier run stored part of this week and stopped.
       Go on; storing tops up the remainder.
     - `already-stored` — this whole week is in the database already. STOP. The
       download almost certainly served a stale file, and re-publishing last
       week as this week is the failure this pipeline is most prone to. Report
       it and do not store.
     - `unknown` — the database could not be reached. That is not the same as
       nothing being stored. Say so in your report, and expect step 5 to fail.

3. Look at `reconciliation.audit_required` in that summary.

   If it is FALSE, every numbered district matches the total the report
   declares for it. Do not re-check the arithmetic; it has been done. Go to
   step 4.

   If it is TRUE, rows were dropped. STOP — do not go on to store the week.
   Each entry in `reconciliation.discrepancies` carries the raw `source_lines`
   of the district that came up short; read them and report what the block
   actually shows, quoting the lines. That is usually enough to see the cause:
   an offence name wrapped across lines, a date the parser did not match, a
   header it did not recognise.

   You do not have to enforce this. `store_incidents` refuses a week whose
   parse did not reconcile, and will refuse it if you try. Your job is to
   explain what went wrong, not to decide whether it matters.

4. Call `unlabelled_incident_types` on the parse output. Most weeks it returns
   an empty `needing_labels`: every code in the report has been named before,
   there is nothing to delegate, and you go straight to step 5.

   When it is not empty, delegate exactly that list to `offence-labeller`.
   Never send the full `unique_incident_types` list instead — a code that
   already has a label keeps it, so the rest of the list cannot be changed and
   sending it only spends tokens. Never write the labels yourself, and never
   re-type incident rows into your own reasoning: a report runs to 100+ rows
   and the files on disk are the source of truth.

5. Store with `store_incidents`, passing the mapping for the new codes (omit it
   if there were none) and writing the enriched JSON at `{ENRICHED_JSON_PATH}`.
   Check its return value: it warns when an incident type had no label, and
   that warning means the map will show a raw offence code to the public.

6. Write a short run report to `/run-report.md` covering: the report period,
   how many incidents were stored and how many were new to the database,
   whether the parse reconciled, any offence code named for the first time,
   and anything a person should act on. Then summarise it in your reply.

Report what actually happened. If a step failed or you skipped one, say so
plainly — this pipeline's characteristic failure is looking successful while
publishing nothing, or publishing last week's data over again."""


def build_backend() -> FilesystemBackend:
    """Give the agent's file tools the real run directory — and only that.

    Without a backend, deepagents defaults to an in-memory state store: `ls`
    finds nothing, and a written file never reaches disk. The run report was
    silently lost, and nothing could read the parse output back.

    Rooted at `work/` rather than the project, with `virtual_mode=True` so
    traversal (`..`, `~`) and outside-absolute paths are blocked. The agent has
    web_fetch, and `.env` holds the Anthropic key and the Postgres URL — it
    has no business being reachable. Note the tools see this directory as their
    root, so `work/extracted_incidents.json` on disk is `/extracted_incidents.json`
    to them.
    """
    work = Path(WORK_DIR).resolve()
    work.mkdir(parents=True, exist_ok=True)
    return FilesystemBackend(root_dir=work, virtual_mode=True)


def build_agent():
    """Build the weekly-report deep agent."""
    return create_deep_agent(
        model=MODEL,
        tools=[
            download_weekly_report,
            parse_incidents,
            read_report_text,
            store_incidents,
            unlabelled_incident_types,
        ],
        system_prompt=SYSTEM_PROMPT,
        subagents=[OFFENCE_LABELLER],
        backend=build_backend(),
    )
