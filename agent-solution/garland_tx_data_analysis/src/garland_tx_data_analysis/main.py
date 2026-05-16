#!/usr/bin/env python
import warnings
from dotenv import load_dotenv

# Override any stale/empty values set in the parent shell with what's in .env.
# Without override=True, an exported-but-empty ANTHROPIC_API_KEY in the user's
# shell silently masks the value in .env and the crew fails to authenticate.
load_dotenv(override=True)

from garland_tx_data_analysis.crew import crew

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """Run the crew."""
    try:
        crew.kickoff()
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}")
