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

# Install dependencies (robust): copy source early and use a conditional fallback
# This avoids build failures when the build context or filename differs (e.g. requirements-dev.txt)
COPY . /app
RUN pip install --upgrade pip && \
  if [ -f /app/requirements.txt ]; then \
    pip install -r /app/requirements.txt; \
  elif [ -f /app/requirements-dev.txt ]; then \
    pip install -r /app/requirements-dev.txt; \
  else \
    echo "No requirements file found, skipping pip install"; \
  fi

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Default to running the package entrypoint
CMD ["python", "-m", "src.main"]
