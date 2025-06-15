# garland-tx-police-activity

This program downloads the previous week incident reports PDF from the Garland TX website and extracts text from the PDF file. It then goes through the text and pulls out the reports from the districts of interest and exports that data to a JSON file.

The `.env` file needs to look something like this:

```
DISTRICTS_OF_INTEREST=41,42,43,44
```

Example output:

```json
{
  "41": [
    "THEFT-ALL OTHER-$2,500 L/T $30,00006/02/2025 32XX HERRMANN DR",
    "BURGLARY-VEH06/03/2025 20XX WESTCHESTER DR",
    "INFO-BURGLARY06/05/2025 27XX S GARLAND AVE",
    "BURGLARY-VEH06/06/2025 38XX REGENCY CREST DR"
  ],
  "42": [
    "BURGLARY-BLDG06/01/2025 6XX KEEN DR",
    "THEFT-ALL OTHER-$750 L/T $2,50006/01/2025 19XX E CENTERVILLE RD",
    "CRIMINAL MISCHIEF $2,500 L/T $30,00006/03/2025 30XX S FIRST ST",
    "INFO-THEFT06/06/2025 10XX E CENTERVILLE RD",
    "ASSAULT-AGG-D/W06/07/2025 7XX HARDY DR"
  ]
}
```

### Running project

```bash
pipenv run python main.py
```

### Install a new package

```bash
pipenv install requests
```
