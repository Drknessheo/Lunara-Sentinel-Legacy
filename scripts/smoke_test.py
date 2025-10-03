#!/usr/bin/env python3
"""Quick smoke test: health endpoint + signed promotion webhook.

Usage examples:
  python scripts/smoke_test.py --base-url http://localhost:8080 --webhook-url https://httpbin.org/post
  (webhook secret will be read from PROMOTION_WEBHOOK_SECRET env var if set)
"""
import os
import sys
import argparse
import json
import time
import hmac
import hashlib

try:
    import requests
except Exception:
    print("requests is required. Install via pip install -r requirements.txt")
    raise


def sign_payload(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode('utf-8'), payload_bytes, hashlib.sha256).hexdigest()


def run_health_check(url: str, timeout: float):
    try:
        r = requests.get(url, timeout=timeout)
        return True, r.status_code, r.text
    except Exception as e:
        return False, None, str(e)


def run_webhook_test(url: str, secret: str | None, timeout: float):
    payload = {"type": "promotion_test", "ts": int(time.time()), "nonce": os.urandom(8).hex()}
    body = json.dumps(payload).encode('utf-8')
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Signature"] = sign_payload(secret, body)

    try:
        r = requests.post(url, data=body, headers=headers, timeout=timeout)
        return True, r.status_code, r.text
    except Exception as e:
        return False, None, str(e)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Smoke test health + promotion webhook")
    parser.add_argument('--base-url', default=os.environ.get('SMOKE_BASE_URL', 'http://localhost:8080'),
                        help='Base URL where the bot/health server is reachable')
    parser.add_argument('--health-path', default=os.environ.get('SMOKE_HEALTH_PATH', '/health'),
                        help='Health endpoint path (default /health)')
    parser.add_argument('--webhook-url', default=os.environ.get('PROMOTION_WEBHOOK_URL', 'https://httpbin.org/post'),
                        help='Promotion webhook URL to POST a test payload to')
    parser.add_argument('--webhook-secret', default=os.environ.get('PROMOTION_WEBHOOK_SECRET'),
                        help='Optional HMAC secret to sign the test webhook')
    parser.add_argument('--timeout', type=float, default=5.0, help='Request timeout (seconds)')
    args = parser.parse_args(argv)

    health_url = args.base_url.rstrip('/') + args.health_path
    print(f"Health check: {health_url}")
    ok, status, text = run_health_check(health_url, args.timeout)
    if ok and status == 200:
        print(f"  OK: status={status}")
    else:
        print(f"  FAIL: status={status} info={text[:400]}")

    print(f"Webhook test: {args.webhook_url}")
    ok2, wstatus, wtext = run_webhook_test(args.webhook_url, args.webhook_secret, args.timeout)
    if ok2 and wstatus and wstatus < 400:
        print(f"  OK: status={wstatus}")
    else:
        print(f"  FAIL: status={wstatus} info={wtext[:400]}")

    if not (ok and status == 200 and ok2 and wstatus and wstatus < 400):
        print('\nSmoke test summary: FAIL')
        # exit non-zero to allow CI to catch failures
        sys.exit(2)
    print('\nSmoke test summary: PASS')


if __name__ == '__main__':
    main()
