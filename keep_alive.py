import os
import time

import requests

URL = os.getenv("KEEP_ALIVE_URL", "https://lunessasignels.onrender.com/healthz")
DELAY = int(os.getenv("KEEP_ALIVE_DELAY_SEC", str(14 * 60)))

if __name__ == "__main__":
    while True:
        try:
            r = requests.get(URL, timeout=10)
            print(f"[KeepAlive] {r.status_code} {r.url}")
        except Exception as e:
            print(f"[KeepAlive] Failed: {e}")
        time.sleep(DELAY)
