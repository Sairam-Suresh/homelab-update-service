#!/usr/bin/env bash
set -euo pipefail

# 1. Pull new image of this service
IMAGE="${HOMELAB_UPDATER_IMAGE:-ghcr.io/sairam-suresh/homelab-update-service:latest}"
echo "==> 1. Pulling new image: ${IMAGE}..."
podman pull "${IMAGE}"

# 2. cd into /opt/serve/homelab and restart homelab_updater using podman-compose
echo "==> 2. Changing directory to /opt/serve/homelab..."
cd /opt/serve/homelab

echo "==> Restarting homelab_updater container using podman-compose..."
if podman-compose up -d --force-recreate homelab_updater; then
    echo "==> Container homelab_updater successfully recreated and started with new image!"
else
    echo "==> Fallback: stopping, removing old container, and recreating..."
    podman-compose stop homelab_updater || true
    podman-compose rm -f homelab_updater || true
    podman-compose up -d homelab_updater
fi

echo "==> Current status:"
podman-compose ps || podman ps --filter name=homelab_updater || true
echo "==> homelab_updater update completed successfully!"
