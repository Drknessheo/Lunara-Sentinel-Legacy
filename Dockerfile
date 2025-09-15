FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Install supervisor and scientific computing libraries from Debian repositories
# This is faster and more reliable than building them with pip.
RUN apt-get update && \
	apt-get install -y --no-install-recommends supervisor python3-numpy python3-pandas && \
	rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt .

# Upgrade pip and install build tools. While numpy/pandas are handled by apt,
# other libraries might need this.
RUN pip install --upgrade pip setuptools wheel

# Install remaining requirements
RUN pip install -r requirements.txt

# Copy the rest of the application code
COPY . .

# Place the supervisor config in the correct directory
RUN mkdir -p /etc/supervisor/conf.d/ && \
    cp supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
