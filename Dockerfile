FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Install supervisor
RUN apt-get update && \
	apt-get install -y --no-install-recommends supervisor && \
	rm -rf /var/lib/apt/lists/*

# Copy dependency files and install first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the rest of the application code
COPY . .

# Place the supervisor config in the correct directory
RUN mkdir -p /etc/supervisor/conf.d/ && \
    cp supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
