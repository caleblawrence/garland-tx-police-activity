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
    {
      "date": "06/02/2025",
      "incident": "THEFT-ALL OTHER-$2,500 L/T $30,000",
      "location": "32XX HERRMANN DR"
    },
    {
      "date": "06/03/2025",
      "incident": "BURGLARY-VEH",
      "location": "20XX WESTCHESTER DR"
    }
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
