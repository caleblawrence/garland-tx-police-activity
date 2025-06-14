import requests
from bs4 import BeautifulSoup

base_url = "https://www.garlandtx.gov"
url = "https://www.garlandtx.gov/396/Crime-Statistics-Maps"
html_response = requests.get(url)

soup = BeautifulSoup(html_response.text, "html.parser")

text_to_find = "Previous Week Selected Incident Report (PDF)"

a_tags = soup.find_all(lambda tag: tag.name == "a" and "aria-label" in tag.attrs)

previous_week_link = None
for a_tag in a_tags:
    if text_to_find in a_tag.text:
        previous_week_link = a_tag['href']
        break

print("Previous Week Selected Incident Report Link:", base_url + previous_week_link)


# TODO: download the PDF file