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
    description="""Extract incident data from the downloaded PDF file by processing it in batches.
    Process the PDF page by page or district by district, extracting incidents in smaller chunks.
    
    For each batch of incidents (e.g., 20-30 incidents at a time):
    1. Extract the incidents from that section
    2. Immediately pass them to the data formatter for storage
    3. Continue to the next batch
    
    This approach prevents context overflow while ensuring all incidents are captured.""",
    expected_output='Confirmation that all incidents have been processed and stored in batches.',
    agent=incident_extractor
)

format_data_task = Task(
    description="""Store incident data in TinyDB database as it's received in batches.
    Accept batched incident data and append it to the 'incidents.db' file.
    
    Handle multiple calls to store different batches of incidents, ensuring:
    - No data loss between batches
    - Proper appending to existing database
    - Unique record handling if needed""",
    expected_output='Confirmation that each batch of incidents has been stored successfully.',
    agent=data_formatter
)



# Create and export the crew
crew = Crew(
	agents=[pdf_downloader, incident_extractor, data_formatter],
	tasks=[download_pdf_task, extract_incidents_task, format_data_task],
	process=Process.sequential,
	verbose=True,
)
