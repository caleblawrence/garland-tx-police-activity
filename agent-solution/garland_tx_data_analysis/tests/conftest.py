import pytest


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
