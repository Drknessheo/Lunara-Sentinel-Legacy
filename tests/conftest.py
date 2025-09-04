import os

import pytest

# Ensure a REDIS_URL is available during tests so modules that lazily read
# it at import-time won't raise. Prefer an explicit env var, otherwise use
# a local redis URL; tests may monkeypatch redis.from_url to a fakeredis.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

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


@pytest.fixture
def mock_redis(monkeypatch):
    """
    Global mock_redis fixture for tests: monkeypatch redis.from_url to return a fakeredis instance.
    """
    try:
        import fakeredis

        fake_client = fakeredis.FakeRedis(decode_responses=True)
        monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: fake_client)
        return fake_client
    except Exception:
        pytest.skip("fakeredis not installed, skipping redis-dependent tests")
