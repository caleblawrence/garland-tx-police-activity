from crewai import Agent, Crew, Process, Task
from garland_tx_data_analysis.tools.custom_tool import FileDownloadTool, PDFIncidentExtractorTool

# Instantiate tools
download_tool = FileDownloadTool()
pdf_extraction_tool = PDFIncidentExtractorTool()

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
	Extract all incidents from all districts and pages in the PDF.
	Save the results directly to 'formatted_incidents.json'.
	
	For each incident, add a human-friendly description of the incident type:
	- Convert police codes like 'BURGLARY-VEH' to 'Vehicle Burglary'
	- Convert 'THEFT-ALL OTHER' to 'Theft'  
	- Convert 'UNAUTHORIZED USE MOTOR VEHICLE' to 'Vehicle Theft'
	- Convert 'CRIMINAL MISCHIEF' to 'Vandalism'
	- And similar conversions for other incident types to make them more readable
	
	Include both the original incident code and the human-friendly description in the JSON output.""",
	expected_output='A JSON file named formatted_incidents.json containing all incidents with both original codes and human-friendly descriptions.',
	agent=incident_extractor
)



# Create and export the crew
crew = Crew(
	agents=[pdf_downloader, incident_extractor],
	tasks=[download_pdf_task, extract_incidents_task],
	process=Process.sequential,
	verbose=True,
)
