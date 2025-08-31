import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# Configuration via environment variables
MODE = os.getenv("MODE", "success").lower()  # success | fail | delay
DEFAULT_DELAY_MS = int(os.getenv("DELAY_MS", "0") or "0")
PORT = int(os.getenv("PORT", "0") or "0")


class MockHandler(BaseHTTPRequestHandler):
    def _send(self, code=200, body=b"ok"):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def resolve_mode(self):
        # query param overrides env MODE
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        qp_mode = (qs.get("mode", [None])[0] or "").strip().lower()
        if qp_mode in {"success", "fail", "delay"}:
            return qp_mode
        return MODE

    def resolve_delay(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            return int(qs.get("delay", [str(DEFAULT_DELAY_MS)])[0] or DEFAULT_DELAY_MS)
        except Exception:
            return DEFAULT_DELAY_MS

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/ping", "/health"):
            self._send(200, b"OK")
            return
        # default
        self._send(404, b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        mode = self.resolve_mode()

        # support delay in ms either from env (DEFAULT_DELAY_MS) or ?delay=
        if mode == "delay":
            delay_ms = self.resolve_delay()
            try:
                time.sleep(int(delay_ms) / 1000.0)
            except Exception:
                pass

        if parsed.path == "/webhook":
            # default to success or fail based on mode
            if mode == "success":
                self._send(200, b"ok")
            elif mode == "fail":
                self._send(500, b"fail")
            else:
                # delay already applied
                self._send(200, b"ok")
            return

        # fallback: 404
        self._send(404, b"not found")


def start_mock_server(port: int = 0):
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    return server, thread, base_url


def main():
    run_port = PORT or 0
    server, thread, base_url = start_mock_server(run_port)
    print(
        f"Mock server running at {base_url} (MODE={MODE}, DEFAULT_DELAY_MS={DEFAULT_DELAY_MS})"
    )
    try:
        # block until interrupted
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
