import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
import json
import os

base_url = "https://www.garlandtx.gov"
url = "https://www.garlandtx.gov/396/Crime-Statistics-Maps"
html_response = requests.get(url)

# Get districts of interest from environment variable
# Example: export DISTRICTS_OF_INTEREST="41,42,43,44"
districts_env = os.getenv("DISTRICTS_OF_INTEREST")
if not districts_env:
    raise ValueError("DISTRICTS_OF_INTEREST environment variable not set. Please set it as a comma-separated list, e.g., '41,42,43,44'.")
try:
    districts_of_interest = [int(x.strip()) for x in districts_env.split(",") if x.strip()]
except Exception as e:
    raise ValueError(f"Error parsing DISTRICTS_OF_INTEREST: {e}")

soup = BeautifulSoup(html_response.text, "html.parser")

text_to_find = "Previous Week Selected Incident Report (PDF)"

a_tags = soup.find_all(lambda tag: tag.name == "a" and "aria-label" in tag.attrs)

previous_week_link = None
for a_tag in a_tags:
    if text_to_find in a_tag.text:
        previous_week_link = a_tag['href']
        break

print("Downloading pdf from", base_url + previous_week_link + "...")


pdf_response = requests.get(base_url + previous_week_link)
with open("previous_week_incident_report.pdf", "wb") as file:
    file.write(pdf_response.content)

reader = PdfReader("previous_week_incident_report.pdf")
extracted_text = ""

for page_num in range(len(reader.pages)):
    page = reader.pages[page_num]
    extracted_text_on_page = page.extract_text()
    extracted_text += extracted_text_on_page + "\n"

print("Extracted Text from PDF.")

# Delete the PDF after processing
if os.path.exists("previous_week_incident_report.pdf"):
    os.remove("previous_week_incident_report.pdf")
    print("Deleted previous_week_incident_report.pdf after processing.")

split_text = extracted_text.split("\n")

print("Processing text to extract incidents for districts:", ", ".join(map(str, districts_of_interest)) + ".")
districts = {}
for district in districts_of_interest:
    district_number = str(district)
    districts[district_number] = []

    end_of_district = False
    for line in split_text:
        text_to_search_for = "DISTRICT " + str(district)
        if text_to_search_for in line:
            # go through each line until we get to another district
            next_line_index = split_text.index(line) + 1
            while not end_of_district and next_line_index < len(split_text):
                next_line = split_text[next_line_index]
                if "DISTRICT" in next_line:
                    end_of_district = True
                else:
                    line_split = next_line.strip().split(" ")
                    # each line looks like this:   41 00002025R010238 BURGLARY-BLDG06/01/2025 6XX KEEN DR
                    # trim off first two items in the line_split
                    cleaned_line = " ".join(line_split[2:]).strip()
                    # if the cleaned line is more than one word, add it to the district
                    if len(cleaned_line.split()) > 1:
                        districts[district_number].append(cleaned_line)
                next_line_index += 1

# Print the number of incidents in each district
for district_number, incidents in districts.items():
    print(f"District {district_number}: {len(incidents)} incidents")
# export to json 
with open("districts_incidents.json", "w") as json_file:
    json.dump(districts, json_file, indent=4)
print("Districts incidents exported to districts_incidents.json")


