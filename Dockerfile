# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps: ffmpeg (for ffprobe + video rendering) + build tools for demucs/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock ./

# Install project dependencies (no editable install, all locked versions)
RUN uv sync --frozen --no-dev

# Copy application source
COPY app/ ./app/

# Tracks storage directory
RUN mkdir -p /tracks

ENV TRACKS_ROOT_DIR=/tracks \
    PYTHONUNBUFFERED=1

# Run via uv so the virtualenv is used automatically
CMD ["uv", "run", "python", "-m", "app.main"]
