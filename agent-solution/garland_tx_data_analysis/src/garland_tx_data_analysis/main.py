#!/usr/bin/env python
import warnings
from garland_tx_data_analysis.crew import crew

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

def run():
    """
    Run the crew.
    """
    try:
        crew.kickoff()
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}")
