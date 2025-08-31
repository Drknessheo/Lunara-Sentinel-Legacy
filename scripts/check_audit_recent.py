import json
import os
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src = os.path.join(root, "src")
if src not in sys.path:
    sys.path.insert(0, src)
import redis

import config

url = os.environ.get("REDIS_URL") or getattr(config, "REDIS_URL", None)
print("Using REDIS_URL:", bool(url))
if not url:
    print("No REDIS_URL, aborting")
    sys.exit(1)

r = redis.from_url(url, decode_responses=True)
items = r.lrange("autosuggest_audit", 0, 4)
print("Recent audit entries:")
for it in items:
    try:
        o = json.loads(it)
        print(
            "-",
            o.get("timestamp"),
            o.get("admin_id"),
            o.get("result"),
            o.get("created_trades"),
        )
    except Exception:
        print("-", it)
