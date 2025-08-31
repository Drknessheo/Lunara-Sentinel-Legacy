import json
import os
import time
from http.server import BaseHTTPRequestHandler

import pytest

try:
    import redis
    import requests
except Exception:
    redis = None
    requests = None


class FailHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Always return 500 to simulate failure
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b"fail")


@pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="REDIS_URL not set")
def test_enqueue_on_webhook_failure(mock_server):
    if not redis:
        pytest.skip("redis python package not installed")

    r = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
    # clear any existing keys used by test
    r.delete("promotion_webhook_retry")
    # call the dispatch function via HTTP post to emulate the code path
    # use mock_server fixture base URL; mode defaults to fail
    url = f"{mock_server}/webhook"
    payload = {"event": "promotion", "audit_id": 9999}

    # clear metrics and ensure promotion_webhook_stats is empty
    r.delete("promotion_webhook_stats")

    # Call the library function to perform send (it should enqueue on failure)
    from src.main import send_promotion_webhook

    success, status, body = send_promotion_webhook(payload, webhook_url=url, timeout=2)
    assert not success

    # allow a moment for Redis
    time.sleep(0.5)
    items = r.lrange("promotion_webhook_retry", 0, -1)
    assert items and len(items) == 1
    obj = json.loads(items[0])
    assert obj["payload"]["audit_id"] == 9999
    assert obj["attempts"] == 0

    # After enqueue, metrics helper should have incremented 'pending'
    # The enqueue function increments pending by 1, so confirm it
    stats = r.hgetall("promotion_webhook_stats")
    assert stats.get("pending") == "1"

    # Simulate worker success: decrease pending and increment total_sent
    from src.main import update_retry_metrics

    update_retry_metrics("pending", -1)
    update_retry_metrics("total_sent", 1)
    stats = r.hgetall("promotion_webhook_stats")
    assert stats.get("pending") == "0"
    assert stats.get("total_sent") == "1"

    # Simulate permanent failure: increment failed and set last_failed_ts
    update_retry_metrics("failed", 1)
    stats = r.hgetall("promotion_webhook_stats")
    assert stats.get("failed") == "1"
    assert "last_failed_ts" in stats
