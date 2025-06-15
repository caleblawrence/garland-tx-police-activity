# garland-tx-police-activity

This program downloads the previous week incident reports PDF from the Garland TX website and extracts text from the PDF file. It then goes through the text and pulls out the reports from the districts of interest and exports that data to a JSON file.

The `.env` file needs to look something like this:

```
DISTRICTS_OF_INTEREST=41,42,43,44
```

### Running project

```bash
pipenv run python main.py
```

### Install a new package

```bash
pipenv install requests
```
