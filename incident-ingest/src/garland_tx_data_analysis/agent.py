"""The weekly-report deep agent.

A LangGraph deep agent (via the `deepagents` harness) that fetches Garland's
weekly incident PDF, extracts it, audits its own extraction against the
report's own district totals, labels the offence codes, and stores the week.

The shape is deliberately not a fixed three-step chain. The tools do the
deterministic work; the agent decides how to sequence it, when the parse is
trustworthy, and what to do when it is not. The auditor subagent exists
because the PDF hands us a way to check the parse — every district block ends
with `District Total: N` — and the interesting failure of this pipeline has
always been silently losing rows rather than crashing.
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


EXTRACTION_AUDITOR = SubAgent(
    name="extraction-auditor",
    description=(
        "Diagnoses a parse that failed to reconcile against the report's own "
        "district totals. Use this ONLY when `reconciliation.audit_required` "
        "is true — when it is false the arithmetic already checked out and "
        "this agent has nothing to find. Reads the source PDF to work out "
        "which rows went missing and why, and returns a verdict on whether "
        "the extraction is trustworthy."
    ),
    system_prompt="""You audit extractions of the Garland weekly incident PDF.

The report declares its own ground truth: every district block ends with a
`District Total: N` line. The `parse_incidents` tool reconciles what it parsed
against those declarations and reports the result under `reconciliation`.

Your job is to decide whether the extraction can be trusted, and to explain
precisely what was lost if it cannot.

How to work:

1. Read the `reconciliation` block. `discrepancies` lists any district whose
   parsed row count disagrees with the total the PDF declares for it.
2. For every discrepancy, use `read_report_text` to read the actual district
   block in the source and work out what the parser missed — a row spanning
   two lines, a date format it did not recognise, a header it did not match.
   Do not speculate about the cause without reading the source.
3. `unnumbered_district_rows` counts rows the report filed under a `DISTRICT`
   header carrying no number. Those rows cannot be attributed to a district
   and are dropped by design. This is a known defect in the source PDF, not a
   parser bug — report the count, and do not treat it as a discrepancy.

Return a verdict of exactly one of `trustworthy`, `trustworthy-with-losses`,
or `untrustworthy`, followed by:
  - declared vs stored totals,
  - each district that failed to reconcile and what the source actually shows
    there,
  - the unnumbered-district row count.

Use `untrustworthy` when a numbered district fails to reconcile: that means
the parser is dropping rows it should have caught. Use
`trustworthy-with-losses` when the only shortfall is the unnumbered-district
rows. Be concrete and quantitative; the caller decides whether to publish
based on what you report, so do not soften a real loss.""",
    tools=[read_report_text],
    model=MODEL,
)


OFFENCE_LABELLER = SubAgent(
    name="offence-labeller",
    description=(
        "Turns the PDF's verbose offence codes into short human-readable "
        "labels. Delegate the list of unique incident types to this agent and "
        "it returns the complete mapping as JSON."
    ),
    system_prompt="""You turn Garland police offence codes into plain English.

You are given the exact list of unique incident types from one week's report.
Return a JSON object mapping every one of those strings, verbatim as the key,
to a concise human-readable label.

Examples of the transformation:
  "THEFT-MOTOR VEHICLE-$2,500 L/T $30,000"  -> "Motor Vehicle Theft"
  "BURGLARY-VEH"                            -> "Vehicle Burglary"
  "CRIMINAL MISCHIEF $100 L/T $750"         -> "Vandalism"
  "ASSAULT-AGG-D/W"                         -> "Aggravated Assault"

Rules:
  - Cover EVERY type you are given. A missing key falls back to the verbose
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
    district totals.
  - `read_report_text` — read the PDF's raw text when you need to see the
    source yourself.
  - `store_incidents` — label, append to the Postgres database, and write the
    enriched JSON the map reads. It takes no connection details; it reads them
    from the environment itself.

The run, and the standing constraints on it:

1. Download the report from {PDF_URL} to `{PDF_PATH}`.

2. Parse it to `{INCIDENTS_JSON_PATH}`. Note the report period in the summary:
   if it matches a week already in the database, the download probably served
   a stale file, and you should say so rather than quietly re-ingesting it.

3. Look at `reconciliation.audit_required` in that summary.

   If it is FALSE, the parse already reconciles against every district total
   the report declares. There is nothing to investigate — go straight to step
   4. Do not delegate to the auditor, and do not re-check the arithmetic
   yourself; it has been done.

   If it is TRUE, delegate the summary to `extraction-auditor` and wait for its
   verdict. Do not audit it yourself — the point of the separate agent is that
   it reads the source PDF rather than taking the summary's word for it.

   If that verdict is `untrustworthy`, STOP. Do not store the week. Report what
   the auditor found. A wrong week on a public map is worse than a late one.

4. Delegate the summary's `unique_incident_types` to `offence-labeller` and
   get the full mapping back. Never write the labels yourself and never
   re-type incident rows into your own reasoning: a report runs to 100+ rows
   and the files on disk are the source of truth.

5. Store with `store_incidents`, passing the mapping and writing the enriched
   JSON at `{ENRICHED_JSON_PATH}`. Check its return value: it warns when an
   incident type had no label, and that warning means the map will show a raw
   offence code to the public.

6. Write a short run report to `/run-report.md` covering: the report period,
   how many incidents were stored and how many were new to the database, the
   auditor's verdict, and anything a person should act on. Then summarise it
   in your reply.

Report what actually happened. If a step failed or you skipped one, say so
plainly — this pipeline's characteristic failure is looking successful while
publishing nothing, or publishing last week's data over again."""


def build_backend() -> FilesystemBackend:
    """Give the agent's file tools the real run directory — and only that.

    Without a backend, deepagents defaults to an in-memory state store: `ls`
    finds nothing, and a written file never reaches disk. The auditor could
    not read the parse output and the run report was silently lost.

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
        ],
        system_prompt=SYSTEM_PROMPT,
        subagents=[EXTRACTION_AUDITOR, OFFENCE_LABELLER],
        backend=build_backend(),
    )
