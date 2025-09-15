FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Install supervisor
RUN apt-get update && \
	apt-get install -y --no-install-recommends supervisor && \
	rm -rf /var/lib/apt/lists/*

# Copy the entire repository early to avoid BuildKit per-file checksum errors
COPY . /app

# --- DEBUGGING STEP ---
# List the contents of the /app directory to see what was copied.
RUN ls -la /app

# Install requirements if present
RUN if [ -f /app/requirements.txt ]; then \
		pip install -r /app/requirements.txt; \
	else \
		echo "No requirements.txt found, skipping pip install"; \
	fi

# If a supervisord.conf exists at repo root, install it into supervisor's conf.d
RUN if [ -f /app/supervisord.conf ]; then \
		mkdir -p /etc/supervisor/conf.d && cp /app/supervisord.conf /etc/supervisor/conf.d/supervisord.conf; \
	else \
		echo "No supervisord.conf found at repo root; continuing without it"; \
	fi

EXPOSE 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
