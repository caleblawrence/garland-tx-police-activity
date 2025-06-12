import requests

url = "https://www.garlandtx.gov/396/Crime-Statistics-Maps"
response = requests.get(url)

print(response.text)
