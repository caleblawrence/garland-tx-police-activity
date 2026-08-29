import os

import pytest
from dotenv import load_dotenv

# Deliberately NOT override=True (which main.py does use, for its own reasons):
# a variable set explicitly in the shell must win over .env here, or you cannot
# point the suite at a different database — or verify that it skips without one.
load_dotenv(override=False)


@pytest.fixture(autouse=True)
def run_in_tmp_cwd(tmp_path, monkeypatch):
    """Run every test from a throwaway directory.

    Several tool arguments default to bare relative filenames
    (enriched_incidents.json, extracted_incidents.json). A test that forgets to
    override one writes into the project directory and silently replaces real
    pipeline output — that is exactly how the live enriched_incidents.json got
    overwritten with the December 2025 test fixture. Tests declare their own
    paths via tmp_path; anything they forget lands here instead of the repo.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def db(monkeypatch):
    """Point the tools at the Neon `test` branch, emptied before each test.

    Requires TEST_DATABASE_URL. Tests that need a database skip without it, so
    a fresh clone with no Postgres still runs the rest of the suite green.

    Both tables are emptied. `incident_labels` outlives a report by design —
    that is the whole point of it — so leaving it behind between tests makes
    them order-dependent: a label learned by one test silently satisfies the
    next one's assertion that a code has no label.
    """
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping Postgres-backed tests")

    # This fixture truncates whatever it is pointed at. Refuse to do that to the
    # database the pipeline actually publishes to.
    if test_url == os.getenv("DATABASE_URL"):
        pytest.fail(
            "TEST_DATABASE_URL matches DATABASE_URL. The test fixture truncates "
            "the incidents table — point it at the Neon `test` branch instead."
        )

    monkeypatch.setenv("DATABASE_URL", test_url)

    from garland_tx_data_analysis.tools import connect, ensure_schema

    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE incidents, incident_labels RESTART IDENTITY")
    yield


def fetch_incidents() -> list[dict]:
    """Every stored row, as dicts, for assertions."""
    from garland_tx_data_analysis.tools import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT incident_id, report_period, district, occurred_on,
                   incident, location, short_description
              FROM incidents
             ORDER BY incident_id
            """
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
