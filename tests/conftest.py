import os

import pytest

# Ensure the mock server module reads MODE from environment during import.
# Set a sensible default before importing the module so its module-level
# MODE constant is initialized correctly.
os.environ.setdefault("MODE", "fail")
from tests.mock_server import start_mock_server


@pytest.fixture(scope="session")
def mock_server():
    server, thread, base_url = start_mock_server()
    yield base_url
    try:
        server.shutdown()
    except Exception:
        pass
