FROM python:3.11-slim

# Keep output unbuffered and avoid pip cache
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app:/app/src

RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  curl \
  gcc \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser
WORKDIR /app

# Install dependencies first to leverage Docker cache
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Copy application source
COPY . /app
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Default to running the package entrypoint
CMD ["python", "-m", "src.main"]
