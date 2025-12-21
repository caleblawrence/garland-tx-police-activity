import json
import requests
import PyPDF2
from crewai.tools import BaseTool
from typing import Type, List
from pydantic import BaseModel, Field


class FileDownloadToolInput(BaseModel):
    """Input schema for FileDownloadTool."""
    url: str = Field(..., description="The URL of the file to download.")
    save_path: str = Field(..., description="The local path to save the downloaded file.")

class FileDownloadTool(BaseTool):
    name: str = "download_tool"
    description: str = "Downloads a file from a URL and saves it locally."
    args_schema: Type[BaseModel] = FileDownloadToolInput

    def _run(self, url: str, save_path: str) -> str:
        try:
            response = requests.get(url)
            response.raise_for_status()  # Raise an exception for bad status codes
            with open(save_path, 'wb') as f:
                f.write(response.content)
            return f"File downloaded successfully and saved at {save_path}"
        except requests.exceptions.RequestException as e:
            return f"Error downloading file: {e}"

class PDFIncidentExtractorToolInput(BaseModel):
    """Input schema for PDFIncidentExtractorTool."""
    pdf_path: str = Field(..., description="The local path to the PDF file.")

class PDFIncidentExtractorTool(BaseTool):
    name: str = "pdf_extraction_tool"
    description: str = "Extracts incident data from a PDF file."
    args_schema: Type[BaseModel] = PDFIncidentExtractorToolInput

    def _run(self, pdf_path: str) -> List[dict]:
        incidents = []
        try:
            with open(pdf_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    # This is a placeholder for actual incident extraction logic.
                    # You would need to parse the 'text' to identify and extract incident details.
                    # For now, we'll just return a dummy list of incidents.
                    lines = text.split('\\n')
                    for line in lines:
                        if "WBI" in line: # Example line check
                             incidents.append({"incident": line, "raw_description": "WBI"})
            return incidents
        except Exception as e:
            return f"Error extracting data from PDF: {e}"

class IncidentFormattingToolInput(BaseModel):
    """Input schema for IncidentFormattingTool."""
    incidents: List[dict] = Field(..., description="A list of incident dictionaries to format.")
    output_path: str = Field(..., description="The path to save the formatted JSON file.")

class IncidentFormattingTool(BaseTool):
    name: str = "incident_formatting_tool"
    description: str = "Formats incident data into JSON and adds human-friendly descriptions."
    args_schema: Type[BaseModel] = IncidentFormattingToolInput

    def _run(self, incidents: List[dict], output_path: str) -> str:
        crime_map = {
            "WBI": "Willfully Causing Bodily Injury"
            # Add other mappings here
        }
        
        formatted_incidents = []
        for incident in incidents:
            raw_desc = incident.get("raw_description")
            incident['human_friendly_description'] = crime_map.get(raw_desc, "Unknown")
            formatted_incidents.append(incident)

        try:
            with open(output_path, 'w') as f:
                json.dump(formatted_incidents, f, indent=2)
            return f"Incidents formatted and saved to {output_path}"
        except IOError as e:
            return f"Error saving formatted incidents: {e}"
