# --- Base Stage: A lean Python environment ---
FROM python:3.11-slim AS base

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# --- Builder Stage: Install dependencies ---
FROM base AS builder

# Install system dependencies required for building some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends build-essential

# Upgrade pip
RUN pip install --upgrade pip

# Copy only the requirements file to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# --- Final Stage: The production image ---
FROM base AS final

# Copy the installed dependencies from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy the application code from the src directory
COPY src/ .

# Command to run the application
# This assumes main.py is the entry point of your bot
CMD ["python", "main.py"]
