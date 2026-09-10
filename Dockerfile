# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies: git for clone & signature verification,
# openssh-client and rsync for target device deployment, curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    rsync \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Setup application directories and secure permissions for SSH keys
RUN mkdir -p /etc/homelab-updater/keys \
    /tmp/homelab-staging \
    /app

WORKDIR /app

# Copy dependency specifications first for layer caching
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the updater service using modern build toolchain
RUN pip install --no-cache-dir .

# Default runtime configuration
ENV HOST=0.0.0.0 \
    PORT=7777 \
    DEVICES_FILE=/etc/homelab-updater/devices.yaml \
    ALLOWED_SIGNERS_FILE=/etc/homelab-updater/allowed_signers \
    STAGING_DIR=/tmp/homelab-staging \
    REQUIRE_COMMIT_SIGNATURE=true

EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7777/healthz || exit 1

ENTRYPOINT ["homelab-updater"]
