import re
import os
from tinydb import TinyDB, Query

db = TinyDB('/Users/caleblawrence/Projects/garland-tx-police-activity/persistance/db.json')
processed_weeks_table = db.table('processed_weeks')

export_folder = '/Users/caleblawrence/Projects/garland-tx-police-activity/scrape-incidents/exported-incidents'

# loop through all JSON files in the export folder, if its not processed in the db yet
# process it and add it to the processed weeks table
for filename in os.listdir(export_folder):
    if filename.endswith('.json'):
        file_path = os.path.join(export_folder, filename)
        match = re.search(r'week_(\d+)', filename)
        if match:
            week_number = int(match.group(1))
            
            Week = Query()
            if not processed_weeks_table.search(Week.week == week_number):
                # TODO: add all the incidents to the incidents table
                processed_weeks_table.insert({'week': week_number, 'file_path': file_path})
                print(f"Added week {week_number} to processed_weeks table.")
            else:
                print(f"Week {week_number} is already in the processed_weeks table.")
        else:
            print(f"Could not find week number in file name: {filename}")


db = TinyDB('db.json')


