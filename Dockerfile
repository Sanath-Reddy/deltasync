FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential rsync iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml setup.py README.md ./
COPY deltasync deltasync
COPY bench bench
RUN pip install --no-cache-dir -e ".[bench]"

ENV DELTASYNC_TOKEN=demo-token
