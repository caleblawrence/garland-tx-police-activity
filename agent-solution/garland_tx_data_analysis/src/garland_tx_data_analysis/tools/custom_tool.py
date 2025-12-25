import json
import requests
from pypdf import PdfReader
import re
import datetime
from crewai.tools import BaseTool
from typing import Type, List, Optional
from pydantic import BaseModel, Field

# No longer using pdfplumber since we're using the proven approach from main.py


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

    def extract_text_from_pdf(self, filename):
        reader = PdfReader(filename)
        extracted_text = ""
        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]
            extracted_text_on_page = page.extract_text()
            extracted_text += extracted_text_on_page + "\n"
        return extracted_text
    
    def parse_district_incidents(self, split_text, districts_of_interest):
        districts = {}
        for district in districts_of_interest:
            district_number = str(district)
            districts[district_number] = []
            end_of_district = False
            for line in split_text:
                text_to_search_for = "DISTRICT " + str(district)
                if text_to_search_for in line:
                    next_line_index = split_text.index(line) + 1
                    while not end_of_district and next_line_index < len(split_text):
                        next_line = split_text[next_line_index]
                        if "DISTRICT" in next_line:
                            end_of_district = True
                        else:
                            line_split = next_line.strip().split(" ")
                            cleaned_line = " ".join(line_split[2:]).strip()

                            # example text at this point: 
                            # "THEFT-ALL OTHER-$2,500 L/T $30,00006/02/2025 32XX HERRMANN DR"
                            if len(cleaned_line.split()) > 1:
                                date_match = re.search(r"\d{1,2}/\d{1,2}/\d{4}", cleaned_line)
                                if (not date_match):
                                    next_line_index += 1
                                    continue
                                line_split_2 = cleaned_line.split(date_match.group())

                                districts[district_number].append({
                                    "date": date_match.group(),
                                    "incident": line_split_2[0].strip(),
                                    "location": line_split_2[1].strip() if len(line_split_2) > 1 else ""
                                })
                        next_line_index += 1
        return districts

    def get_week_number_from_pdf_text(self, extracted_text):
        # Simple implementation - you can enhance this based on your PDF format
        week_match = re.search(r"week (\d+)", extracted_text.lower())
        if week_match:
            return int(week_match.group(1))
        return None

    def _run(self, pdf_path: str) -> str:
        extracted_text = self.extract_text_from_pdf(pdf_path)
        split_text = extracted_text.split("\n")

        all_districts = []
        for line in split_text:
            match = re.search(r"DISTRICT (\d+)", line)
            if match:
                all_districts.append(int(match.group(1)))
        districts_of_interest = sorted(list(set(all_districts)))

        districts = self.parse_district_incidents(split_text, districts_of_interest)

        combinedIncidents = []
        for district_number, incidents in districts.items():
            print(f"District {district_number}: {len(incidents)} incidents")
            for incident in incidents:
                # Add district info to each incident
                enhanced_incident = {
                    "date": incident["date"],
                    "incident": incident["incident"],
                    "location": incident["location"],
                    "district": district_number
                }
                combinedIncidents.append(enhanced_incident)
        
        # Save directly to JSON file
        output_path = "formatted_incidents.json"
        try:
            with open(output_path, 'w') as f:
                json.dump(combinedIncidents, f, indent=2)
            print(f"Saved {len(combinedIncidents)} incidents to {output_path}")
            return f"Successfully extracted and saved {len(combinedIncidents)} incidents to {output_path}"
        except IOError as e:
            return f"Error saving incidents to JSON: {e}"

class IncidentFormattingToolInput(BaseModel):
    """Input schema for IncidentFormattingTool."""
    incidents: List[dict] = Field(..., description="A list of incident dictionaries to format.")
    output_path: str = Field(default="formatted_incidents.json", description="The path to save the formatted JSON file.")

class IncidentFormattingTool(BaseTool):
    name: str = "incident_formatting_tool"
    description: str = "Formats incident data into JSON and adds human-friendly descriptions."
    args_schema: Type[BaseModel] = IncidentFormattingToolInput

    def _run(self, incidents: List[dict], output_path: str) -> str:
        try:
            with open(output_path, 'w') as f:
                json.dump(incidents, f, indent=2)
            return f"Incidents formatted and saved to {output_path}"
        except IOError as e:
            return f"Error saving formatted incidents: {e}"
