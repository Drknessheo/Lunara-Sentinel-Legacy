from flask import Flask, jsonify, redirect
from datetime import datetime
import logging
import os

app = Flask(__name__)

# Disable Werkzeug's default logger for successful requests
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)  # Only log errors


@app.route("/")
def home():
    return "✅ Lunessa Bot is running. Join us on Telegram."


@app.route("/telegram")
def telegram():
    return redirect("https://t.me/YOUR_CHANNEL_NAME")  # Replace with your actual channel


@app.route("/healthz")
def healthz():
    # This endpoint is for Render's health check.
    # It returns a simple 200 OK without logging to keep logs clean.
    return "", 200


if __name__ == '__main__':
    # Bind to 0.0.0.0 so the container is reachable from the outside network.
    port = int(os.environ.get('PORT', 8080))
    # threaded=True keeps the app responsive while the bot runs in the background
    app.run(host='0.0.0.0', port=port, threaded=True)
