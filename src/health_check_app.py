from flask import Flask, jsonify, redirect
from datetime import datetime
import logging

app = Flask(__name__)

# Disable Werkzeug's default logger for successful requests
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING) # Only log errors

@app.route("/")
def home():
    return "✅ Lunessa Bot is running. Join us on Telegram."

@app.route("/telegram")
def telegram():
    return redirect("https://t.me/YOUR_CHANNEL_NAME") # Replace with your actual channel

@app.route("/healthz")
def healthz():
    # This endpoint is for Render's health check.
    # It returns a simple 200 OK without logging to keep logs clean.
    return "", 200
