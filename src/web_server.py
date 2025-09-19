
from flask import Flask, jsonify
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def health_check():
    """
    A simple health-check endpoint that returns a 200 OK status.
    Render uses this to determine if the service is live.
    """
    log.info("Health check endpoint was hit.")
    return jsonify({"status": "ok"}), 200

def run_web_server():
    """
    Starts the Flask web server, listening on the port specified by Render.
    """
    # The port is dynamically set by the PORT environment variable.
    port = int(os.environ.get("PORT", 10000))
    
    # The host must be '0.0.0.0' to be accessible from outside the container.
    log.info(f"Starting Flask web server on host 0.0.0.0 and port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    log.info("Starting Flask web server directly...")
    run_web_server()
