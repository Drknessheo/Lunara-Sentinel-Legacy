#!/bin/sh

echo "Starting main bot..."
python src/main.py &

echo "Starting health check app..."
python src/health_check_app.py