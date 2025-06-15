import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
import json
import os
import re

def get_districts_of_interest():
    districts_env = os.getenv("DISTRICTS_OF_INTEREST")
    if not districts_env:
        raise ValueError("DISTRICTS_OF_INTEREST environment variable not set. Please set it as a comma-separated list, e.g., '41,42,43,44'.")
    try:
        return [int(x.strip()) for x in districts_env.split(",") if x.strip()]
    except Exception as e:
        raise ValueError(f"Error parsing DISTRICTS_OF_INTEREST: {e}")

def get_previous_week_pdf_url(soup):
    text_to_find = "Previous Week Selected Incident Report (PDF)"
    a_tags = soup.find_all(lambda tag: tag.name == "a" and "aria-label" in tag.attrs)
    for a_tag in a_tags:
        if text_to_find in a_tag.text:
            return a_tag['href']
    raise ValueError("Could not find previous week PDF link.")

def download_pdf(url, filename):
    pdf_response = requests.get(url)
    with open(filename, "wb") as file:
        file.write(pdf_response.content)

def extract_text_from_pdf(filename):
    reader = PdfReader(filename)
    extracted_text = ""
    for page_num in range(len(reader.pages)):
        page = reader.pages[page_num]
        extracted_text_on_page = page.extract_text()
        extracted_text += extracted_text_on_page + "\n"
    return extracted_text

def parse_district_incidents(split_text, districts_of_interest):
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

def export_to_json(data, filename):
    with open(filename, "w") as json_file:
        json.dump(data, json_file, indent=4)

def main():
    base_url = "https://www.garlandtx.gov"
    url = "https://www.garlandtx.gov/396/Crime-Statistics-Maps"
    html_response = requests.get(url)
    soup = BeautifulSoup(html_response.text, "html.parser")
    districts_of_interest = get_districts_of_interest()
    previous_week_link = get_previous_week_pdf_url(soup)
    print("Downloading pdf from", base_url + previous_week_link + "...")
    pdf_filename = "previous_week_incident_report.pdf"
    download_pdf(base_url + previous_week_link, pdf_filename)
    extracted_text = extract_text_from_pdf(pdf_filename)
    print("Extracted Text from PDF.")
    if os.path.exists(pdf_filename):
        os.remove(pdf_filename)
        print(f"Deleted {pdf_filename} after processing.")
    split_text = extracted_text.split("\n")
    print("Processing text to extract incidents for districts:", ", ".join(map(str, districts_of_interest)) + ".")
    districts = parse_district_incidents(split_text, districts_of_interest)

    for district_number, incidents in districts.items():
        print(f"District {district_number}: {len(incidents)} incidents")
    export_to_json(districts, "districts_incidents.json")
    print("Districts incidents exported to districts_incidents.json")

if __name__ == "__main__":
    main()


