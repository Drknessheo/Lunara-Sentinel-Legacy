FROM python:3.10-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home appuser
USER appuser

# Copy requirements first for caching
COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy app code
COPY --chown=appuser:appuser . .

# Make startup script executable
# This needs to be run as root before switching to appuser
USER root
RUN chmod +x startup.sh
USER appuser

# Optional: Set environment variable for logging level
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

# Expose the health check port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:8080/healthz || exit 1

# Run with fallback logging
CMD ["./startup.sh"]