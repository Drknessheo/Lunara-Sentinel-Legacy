import logging

from flask import Flask, request
from prometheus_flask_exporter import PrometheusMetrics

# If running as a package, this will work, otherwise, it might fail.
# We will assume it's run as a package (`python -m src.health_check_app`)
try:
    from .security import verify_hmac
except ImportError:
    # Fallback for running as a script, though this is not the intended way
    from security import verify_hmac

app = Flask(__name__)
metrics = PrometheusMetrics(app)

logging.basicConfig(level=logging.INFO)


@app.route("/healthz")
def healthz():
    """
    Health check endpoint.
    """
    return "OK", 200


@app.route("/webhook", methods=["POST"])
@verify_hmac
def webhook():
    """
    Handles incoming webhooks.
    """
    logging.info(f"Received webhook: {request.json}")
    return "OK", 200


if __name__ == "__main__":
    # This is for local testing. In production, gunicorn is used.
    app.run(host="0.0.0.0", port=8080)
