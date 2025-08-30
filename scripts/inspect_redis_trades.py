import os
import sys
import json
import redis

# Ensure project src/ is on sys.path so we can import src.config
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src_path = os.path.join(root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import config

def get_redis_url():
    env_url = os.environ.get("REDIS_URL")
    config_url = getattr(config, "REDIS_URL", None)
    print(f"[ENV] REDIS_URL: {env_url}")
    print(f"[CONFIG] REDIS_URL: {config_url}")
    return env_url or config_url

def classify_value(value):
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return "JSON"
        elif isinstance(parsed, (int, float)):
            return "Numeric"
        else:
            return "Other JSON"
    except Exception:
        try:
            float_val = float(value)
            return "Numeric"
        except Exception:
            return "Raw"

def inspect_trades():
    redis_url = get_redis_url()
    if not redis_url:
        print("❌ No REDIS_URL found.")
        return

    r = redis.from_url(redis_url)
    keys = list(r.scan_iter("trade:*"))
    report = {"JSON": 0, "Numeric": 0, "Raw": 0, "Other JSON": 0, "Malformed": []}

    for key in keys:
        try:
            val = r.get(key)
            if val is None:
                continue
            val_str = val.decode("utf-8")
            category = classify_value(val_str)
            report[category] += 1
        except Exception as e:
            try:
                k = key.decode()
            except Exception:
                k = str(key)
            report["Malformed"].append((k, str(e)))

    print("\n🔍 Redis Trade Key Report:")
    for k, v in report.items():
        if k != "Malformed":
            print(f"  {k}: {v}")
    if report["Malformed"]:
        print("\n⚠️ Malformed Entries:")
        for key, err in report["Malformed"]:
            print(f"  {key}: {err}")

if __name__ == "__main__":
    inspect_trades()
