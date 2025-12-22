import pytest
from src.garland_tx_data_analysis.tools.custom_tool import PDFIncidentExtractorTool

def test_pdf_incident_extractor_tool():
    """
    Test case for the PDFIncidentExtractorTool.

    This test initializes the PDFIncidentExtractorTool and runs it with a specific
    PDF file. It then asserts that the output is a list, which is the expected
    format of the extracted incidents. This serves as a basic validation of the
    tool's functionality.
    """
    # Arrange
    tool = PDFIncidentExtractorTool()
    pdf_path = "/Users/caleblawrence/Projects/garland-tx-police-activity/agent-solution/garland_tx_data_analysis/police_incidents_report.pdf"

    # Act
    result = tool._run(pdf_path=pdf_path)

    # Assert
    assert isinstance(result, list), "The result should be a list of incidents."
    print("Extracted Incidents:", result)

