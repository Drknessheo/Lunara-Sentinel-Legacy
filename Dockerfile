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

COPY . .

ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

EXPOSE 8080

HEALTHCHECK --interval=15m --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:8080/healthz || exit 1

CMD ["sh", "-c", "python -m src.main & python src/health_check_app.py"]