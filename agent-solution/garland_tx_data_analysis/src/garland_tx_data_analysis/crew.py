from crewai import Agent, Crew, Process, Task
from garland_tx_data_analysis.tools.custom_tool import FileDownloadTool, PDFIncidentExtractorTool, TinyDBWriterTool

# Instantiate tools
download_tool = FileDownloadTool()
pdf_extraction_tool = PDFIncidentExtractorTool()
tinydb_writer_tool = TinyDBWriterTool()

# Create agents
pdf_downloader = Agent(
	role='PDF Downloader Agent',
	goal='Download the weekly police incidents PDF from the public data website.',
	backstory='An agent specialized in downloading files from the web, ensuring reliable and efficient data acquisition.',
	tools=[download_tool],
	verbose=True
)

incident_extractor = Agent(
	role='Incident Extractor Agent',
	goal='Extract all incident data from the downloaded PDF file.',
	backstory='An expert in parsing PDF documents, this agent can accurately extract text and tabular data from any PDF.',
	tools=[pdf_extraction_tool],
	verbose=True
)
data_formatter = Agent(
	role='Data Formatter Agent',
	goal='Convert extracted incident data and store it in TinyDB database.',
	backstory='A meticulous agent with an eye for detail, it transforms raw data into a structured format and stores it efficiently in a database.',
	tools=[tinydb_writer_tool],
	verbose=True
)


# Create tasks
download_pdf_task = Task(
	description="""Download the weekly police incidents PDF from the public data website.
	The URL for the PDF is 'https://www.garlandtx.gov/DocumentCenter/View/802/Previous-Week-Selected-Incident-Report-PDF?bidId='.""",
	expected_output='The file path of the downloaded PDF.',
	agent=pdf_downloader
)

extract_incidents_task = Task(
    description="""Process the PDF in batches of 30 incidents at a time.
    
    For each batch:
    1. Extract up to 30 incidents from the current section of the PDF
    2. Format the batch data as JSON
    3. Immediately trigger storage of this batch
    4. Continue to next section until entire PDF is processed
    
    Work sequentially through the PDF, ensuring no incidents are skipped.
    Track progress and report batch completion after each storage operation.""",
    expected_output='Sequential batch processing with confirmation after each batch is stored.',
    agent=incident_extractor
)

format_data_task = Task(
    description="""Store EVERY SINGLE incident provided - DO NOT filter, truncate, or remove any incidents.
    
    CRITICAL REQUIREMENTS:
    1. Store ALL incidents provided, including incomplete ones
    2. Do not make decisions about data quality - store everything as-is
    3. Append to 'incidents.db' without losing existing data
    4. Count and report the EXACT number of incidents provided vs stored
    
    If you receive 80 incidents, you must store exactly 80 incidents.
    If you receive 90 incidents, you must store exactly 90 incidents.
    NO FILTERING OR DATA LOSS IS ACCEPTABLE.""",
    expected_output='Exact count confirmation: "Received X incidents, successfully stored X incidents"',
    agent=data_formatter
)



# Create and export the crew
crew = Crew(
	agents=[pdf_downloader, incident_extractor, data_formatter],
	tasks=[download_pdf_task, extract_incidents_task, format_data_task],
	process=Process.sequential,
	verbose=True,
)
