FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
  python-telegram-bot \
  google-generativeai \
  python-dotenv \
  python-binance==1.0.15 \
  numpy \
  matplotlib \
  cryptography \
  filelock \
  pandas \
  pyarrow \
  apscheduler \
  Flask \
  redis

# Install supervisor to run multiple processes reliably
RUN apt-get update && apt-get install -y supervisor && rm -rf /var/lib/apt/lists/*

COPY . src/

ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

EXPOSE 8080

HEALTHCHECK --interval=15m --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:8080/healthz || exit 1

# Use supervisord to manage the health server and bot processes
COPY supervisord.conf /etc/supervisord.conf
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]
