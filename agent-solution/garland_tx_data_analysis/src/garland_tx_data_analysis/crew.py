from crewai import Agent, Crew, Process, Task
from garland_tx_data_analysis.tools.custom_tool import (
    FileDownloadTool,
    PDFIncidentExtractorTool,
    TinyDBWriterTool,
)

PDF_URL = (
    "https://www.garlandtx.gov/DocumentCenter/View/802/"
    "Previous-Week-Selected-Incident-Report-PDF?bidId="
)
PDF_PATH = "police_incidents.pdf"
INCIDENTS_JSON_PATH = "extracted_incidents.json"
ENRICHED_JSON_PATH = "enriched_incidents.json"
DB_PATH = "incidents.db"

download_tool = FileDownloadTool()
pdf_extraction_tool = PDFIncidentExtractorTool()
tinydb_writer_tool = TinyDBWriterTool()

pdf_downloader = Agent(
    role="PDF Downloader Agent",
    goal="Download the weekly police incidents PDF from the public data website.",
    backstory="Reliable file-fetcher. Calls the download tool once and reports the saved path.",
    tools=[download_tool],
    verbose=True,
)

incident_extractor = Agent(
    role="Incident Extractor Agent",
    goal="Extract every incident from the PDF and persist the full list to disk.",
    backstory=(
        "Parses Garland TX weekly incident PDFs into structured records. "
        "Always delegates parsing to the extraction tool — never transcribes "
        "incidents by hand, since a single PDF can contain 100+ records and "
        "the tool is the source of truth."
    ),
    tools=[pdf_extraction_tool],
    verbose=True,
)

data_formatter = Agent(
    role="Data Formatter Agent",
    goal=(
        "Produce a short_description mapping for the unique incident types and "
        "store all incidents in TinyDB without dropping any records."
    ),
    backstory=(
        "Turns verbose police incident codes (e.g. 'THEFT-MOTOR VEHICLE-$2,500 L/T $30,000') "
        "into concise, human-friendly labels (e.g. 'Motor Vehicle Theft'). "
        "Hands the mapping plus the JSON path to the writer tool — never "
        "re-types incident rows itself."
    ),
    tools=[tinydb_writer_tool],
    verbose=True,
)


download_pdf_task = Task(
    description=(
        f"Download the weekly police incidents PDF from {PDF_URL} and save it "
        f"to '{PDF_PATH}'. Use the download_tool exactly once."
    ),
    expected_output=f"The local file path of the saved PDF (should be '{PDF_PATH}').",
    agent=pdf_downloader,
)

extract_incidents_task = Task(
    description=(
        f"Call pdf_extraction_tool with pdf_path='{PDF_PATH}' and "
        f"output_json_path='{INCIDENTS_JSON_PATH}'. "
        "The tool extracts every incident and writes them to the JSON file. "
        "Do NOT list or transcribe individual incidents in your response — the "
        "next agent will read them from disk. "
        "Return the tool's JSON summary verbatim so the next agent can see the "
        "file path, total count, and the list of unique incident types."
    ),
    expected_output=(
        "The JSON summary returned by pdf_extraction_tool, containing "
        "'json_path', 'total_incidents', 'per_district_counts', and "
        "'unique_incident_types'."
    ),
    agent=incident_extractor,
)

format_data_task = Task(
    description=(
        "From the previous task's summary you have the JSON file path and the "
        "list of unique_incident_types. "
        "For each unique incident type, produce a concise human-friendly label "
        "(e.g. 'THEFT-MOTOR VEHICLE-$2,500 L/T $30,000' -> 'Motor Vehicle Theft', "
        "'BURGLARY-VEH' -> 'Vehicle Burglary', "
        "'CRIMINAL MISCHIEF $100 L/T $750' -> 'Vandalism'). "
        f"Then call tinydb_writer_tool with json_path='{INCIDENTS_JSON_PATH}', "
        f"db_path='{DB_PATH}', enriched_json_path='{ENRICHED_JSON_PATH}', "
        "and short_description_map set to the mapping you produced. "
        "Cover EVERY unique incident type from the previous summary — missing keys "
        "fall back to the verbose name, which is undesirable. "
        "Do NOT re-list individual incident rows in your reasoning. The writer "
        "tool reads them from the JSON file directly."
    ),
    expected_output=(
        "The writer tool's confirmation string showing how many incidents were "
        "read from the JSON and how many were inserted into the database."
    ),
    agent=data_formatter,
)


crew = Crew(
    agents=[pdf_downloader, incident_extractor, data_formatter],
    tasks=[download_pdf_task, extract_incidents_task, format_data_task],
    process=Process.sequential,
    verbose=True,
)
