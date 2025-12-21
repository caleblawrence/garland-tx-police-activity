from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from garland_tx_data_analysis.tools.custom_tool import FileDownloadTool, PDFIncidentExtractorTool, IncidentFormattingTool

@CrewBase
class GarlandTxDataAnalysisCrew():
	"""GarlandTxDataAnalysis crew"""
	agents_config = 'config/agents.yaml'
	tasks_config = 'config/tasks.yaml'

	@agent
	def pdf_downloader(self) -> Agent:
		return Agent(
			config=self.agents_config['pdf_downloader'],
			tools=[FileDownloadTool()],
			verbose=True
		)

	@agent
	def incident_extractor(self) -> Agent:
		return Agent(
			config=self.agents_config['incident_extractor'],
			tools=[PDFIncidentExtractorTool()],
			verbose=True
		)

	@agent
	def data_formatter(self) -> Agent:
		return Agent(
			config=self.agents_config['data_formatter'],
			tools=[IncidentFormattingTool()],
			verbose=True
		)

	@task
	def download_pdf_task(self) -> Task:
		return Task(
			config=self.tasks_config['download_pdf_task'],
			agent=self.pdf_downloader()
		)

	@task
	def extract_incidents_task(self) -> Task:
		return Task(
			config=self.tasks_config['extract_incidents_task'],
			agent=self.incident_extractor()
		)

	@task
	def format_data_task(self) -> Task:
		return Task(
			config=self.tasks_config['format_data_task'],
			agent=self.data_formatter(),
			output_file='incidents.json'
		)

	@crew
	def crew(self) -> Crew:
		"""Creates the GarlandTxDataAnalysis crew"""
		return Crew(
			agents=self.agents, # Automatically created by the @agent decorator
			tasks=self.tasks, # Automatically created by the @task decorator
			process=Process.sequential,
			verbose=2,
		)
