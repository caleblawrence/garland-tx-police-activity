from crewai import Agent, Crew, Process, Task
from garland_tx_data_analysis.tools.custom_tool import FileDownloadTool, PDFIncidentExtractorTool, IncidentFormattingTool

# Instantiate tools
download_tool = FileDownloadTool()
pdf_extraction_tool = PDFIncidentExtractorTool()
incident_formatting_tool = IncidentFormattingTool()

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
	goal='Convert extracted incident data into JSON format and add human-friendly descriptions.',
	backstory='A meticulous agent with an eye for detail, it transforms raw data into a structured and enriched format with human-friendly incident names.',
	ools=[incident_formatting_tool],
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
	description="""Extract all incident data from the downloaded PDF file.
	The PDF contains a table of police incidents organized by district.
	Extract all incidents from all districts and pages in the PDF.""",
	expected_output='A list of incidents extracted from the PDF with date, incident type, location, and district information.',
	agent=incident_extractor
)

format_data_task = Task(
	description="""Convert the extracted incident data into JSON format and add human-friendly descriptions.
	For each incident, add a human-friendly description of the incident type:
	- Convert police codes like 'BURGLARY-VEH' to 'Vehicle Burglary'
	- Convert 'THEFT-ALL OTHER' to 'Theft'  
	- Convert 'UNAUTHORIZED USE MOTOR VEHICLE' to 'Vehicle Theft'
	- Convert 'CRIMINAL MISCHIEF' to 'Vandalism'
	- And similar conversions for other incident types to make them more readable
	
	Save the enhanced data to 'formatted_incidents.json'.""",
	expected_output='A JSON file named formatted_incidents.json containing all incidents with both original codes and human-friendly descriptions.',
	agent=data_formatter
)



# Create and export the crew
crew = Crew(
	agents=[pdf_downloader, incident_extractor, data_formatter],
	tasks=[download_pdf_task, extract_incidents_task, format_data_task],
	process=Process.sequential,
	verbose=True,
)
