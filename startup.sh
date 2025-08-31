#!/bin/sh

echo "Starting main bot..."
python -m src.main &

echo "Starting health check app..."
python src/health_check_app.py
