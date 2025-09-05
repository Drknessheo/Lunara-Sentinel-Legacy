# ---- Base Stage ----
# Use an official Python runtime as a parent image
FROM python:3.11-slim-buster as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# ---- Builder Stage ----
# This stage is used to install python dependencies
FROM base as builder

# Install dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt


# ---- Final Stage ----
# This is the final image that will be deployed
FROM base

# Install supervisor from system packages
RUN apt-get update && apt-get install -y supervisor

# Copy the pre-built wheels from the builder stage
COPY --from=builder /app/wheels /wheels

# Install the python dependencies from the wheels
RUN pip install --no-cache /wheels/*

# Copy the application code
COPY . .

# Copy the supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose the port the app runs on
EXPOSE 8080

# Run supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
