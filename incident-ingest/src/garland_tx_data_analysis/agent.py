"""The weekly-report deep agent.

A LangGraph deep agent (via the `deepagents` harness) that fetches Garland's
weekly incident PDF, extracts it, labels any offence code it has not seen
before, stores the week, and reports what it did.

The tools do the deterministic work, and where a decision has a right answer
they make it rather than reporting it upward. The parse either reconciles
against the report's own `District Total: N` lines or `store_incidents`
refuses the week; a code that already has a label keeps it; whether a week is
already stored is a database query. None of that is the model's to decide.

What is left for the model is the part with no right answer: naming an offence
code nobody has seen before, and writing an account of the run that a person
can read. When a parse fails, the failing district's raw source lines come
back with it, so explaining the failure means quoting the source rather than
reasoning about it — see `_district_block_lines` and `_reconciliation_gate` in
tools.py.
"""

import os
from pathlib import Path

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend

from garland_tx_data_analysis.tools import (
    WORK_DIR,
    download_weekly_report,
    parse_incidents,
    read_report_text,
    store_incidents,
    unlabelled_incident_types,
)

# Both on Haiku. The main loop calls four tools in order, branches on two
# status strings the tools compute for it, and writes a readable account of
# what happened. Nothing in that needs a larger model: the judgment calls that
# would have — whether the parse can be trusted, whether the week is already
# stored — are settled in tools.py before the agent sees them.
#
# If runs start dropping steps, skipping the run report or storing before
# checking, raise this rather than adding more prose to the prompt. One
# variable, no code change:
#
#     GARLAND_MODEL=anthropic:claude-sonnet-5 uv run run_agent
MODEL = os.getenv("GARLAND_MODEL", "anthropic:claude-haiku-4-5")
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


SYSTEM_PROMPT = """You publish Garland TX's weekly police incident report.

Each run fetches the city's latest weekly PDF, extracts the incidents, checks
the extraction, labels any offence code that has never been seen, and stores
the week for the map that renders it.

Call the pipeline tools with no arguments unless you have a specific reason
not to. Every path and URL they need is fixed and already their default: the
city serves one report, and each run writes the same files. Your own file
tools (`ls`, `read_file`, `write_file`, `glob`, `grep`) see the run directory
as `/`, and nothing outside it is reachable.

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

1. Download the report.

2. Parse it, then read `period_check.status` in the summary. It has already
   compared this week against the database; you cannot check that yourself, so
   take what it says.

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

4. Call `unlabelled_incident_types`. Most weeks it returns an empty
   `needing_labels`: every code in the report has been named before, there is
   nothing to delegate, and you go straight to step 5.

   When it is not empty, delegate exactly that list to `offence-labeller`.
   Never send the full `unique_incident_types` list instead — a code that
   already has a label keeps it, so the rest of the list cannot be changed and
   sending it only spends tokens. Never write the labels yourself, and never
   re-type incident rows into your own reasoning: a report runs to 100+ rows
   and the files on disk are the source of truth.

5. Store with `store_incidents`, passing the mapping for the new codes as
   `short_description_map` (omit it if there were none). Check its return
   value: it warns when an incident type had no label, and that warning means
   the map would show a raw offence code to the public.

6. Write a short run report with `write_file` to `/run-report.md` — that path
   exactly, starting with a single slash. Cover: the report period, how many
   incidents were stored and how many were new to the database, whether the
   parse reconciled, any offence code named for the first time, and anything a
   person should act on. Then summarise it in your reply.

Report what actually happened. If a step failed or you skipped one, say so
plainly — this pipeline's characteristic failure is looking successful while
publishing nothing, or publishing last week's data over again."""


def build_backend() -> FilesystemBackend:
    """Give the agent's file tools the real run directory — and only that.

    A backend is required for them to touch disk at all: deepagents defaults to
    an in-memory state store, where `ls` finds nothing and a written file goes
    nowhere.

    Rooted at `work/` rather than the project, with `virtual_mode=True` so
    traversal (`..`, `~`) and outside-absolute paths are blocked. `.env` holds
    the Anthropic key and the Postgres URL, and `download_weekly_report` takes
    an arbitrary URL — so anything readable here is also sendable somewhere.
    Confining the agent to `work/` keeps the credentials out of reach.

    The file tools see this directory as their root, so
    `work/extracted_incidents.json` on disk is `/extracted_incidents.json` to
    them. The system prompt never has to explain that, because the pipeline
    tools default their own paths: the only filenames the agent types are ones
    it gives to its own file tools.
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
