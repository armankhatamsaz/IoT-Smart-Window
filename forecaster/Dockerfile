FROM python:3.11-slim

# Stream Python logs straight to the container output (no buffering).
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The start command is provided per service in docker-compose.yml.
