import os
import pytest
from tests.mock_server import start_mock_server


@pytest.fixture(scope='session')
def mock_server():
    server, thread, base_url = start_mock_server()
    yield base_url
    try:
        server.shutdown()
    except Exception:
        pass
