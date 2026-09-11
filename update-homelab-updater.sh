#!/usr/bin/env bash
set -euo pipefail

# 1. Pull new image of this service
IMAGE="${HOMELAB_UPDATER_IMAGE:-ghcr.io/sairam-suresh/homelab-update-service:latest}"
echo "==> 1. Pulling new image: ${IMAGE}..."
podman pull "${IMAGE}"

# 2. cd into /opt/serve/homelab and restart homelab_updater using podman-compose
echo "==> 2. Changing directory to /opt/serve/homelab..."
cd /opt/serve/homelab

echo "==> Restarting with podman-compose down and up..."
podman-compose down
podman-compose up -d

echo "==> Current status:"
podman-compose ps
echo "==> Update completed successfully!"
