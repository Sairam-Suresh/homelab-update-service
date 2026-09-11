# Homelab Updater Service

A secure, persistent Python 3 background daemon that automates continuous deployment across your homelab nodes. Designed to run as a continuous service behind a reverse proxy (like Caddy), it receives deployment webhook requests from GitHub Actions, cryptographically verifies git commit signatures against your authorized keys, stages the updated service directory, and deploys files and executes bootstrap scripts across your target servers over SSH.

---

## Architecture Overview

```
[ Developer ] --(git commit with SSH key)--> [ GitHub Monorepo ]
                                                     |
                                            (push triggers GHA)
                                                     v
                                         [ GitHub Actions Workflow ]
                                                     |
                                        (POST /api/v1/deploy)
                                                     v
                                      [ Reverse Proxy (e.g. Caddy) ]
                                                     |
                                                     v
                                      [ Homelab Updater Service ]
                                                     |
               +-------------------------------------+-------------------------------------+
               | (1. Verify Auth Secret)                                                   |
               | (2. Clone commit into staging)                                            |
               | (3. Cryptographically verify signature against allowed_signers)           |
               | (4. Read deploy.yaml inside service folder)                               |
               | (5. Lookup target device in devices.yaml)                                 |
               +-------------------------------------+-------------------------------------+
                                                     |
                                         (rsync + SSH execution)
                                                     v
                       +-----------------------------+-----------------------------+
                       |                                                           |
                       v                                                           v
              [ Target Node: Tower ]                                      [ Target Node: NAS ]
           /opt/homelab/services/<svc>                                 /opt/homelab/services/<svc>
          (run bootstrap.sh / compose)                                (run bootstrap.sh / compose)
```

---

## Key Features

- **Persistent Daemon**: Runs as an HTTP service listening behind your reverse proxy (Caddy, Traefik, Nginx).
- **Cryptographic Key Verification**: Enforces Git SSH commit signature verification (using standard `allowed_signers`) or GPG. Unsigned commits or commits by unauthorized keys are rejected immediately with `403 Forbidden`.
- **Flexible Path Structure**: Accepts any `repo_relative_path` (e.g., `services/s-workspaces-gateway`, `apps/plex`, `infrastructure/caddy`), adapting seamlessly to any repository organization.
- **Per-Service Manifest (`deploy.yaml`)**: Each service directory controls its own destination path, target device, bootstrap script, and environment variables.
- **Multi-Device Target Routing**: Maps device names (`tower`, `nas`, `local`) to SSH connection targets in a single mounted `devices.yaml`.
- **Atomic SSH Sync**: Uses `rsync` over SSH to update service folders reliably and cleanly.
- **Containerized**: Publishes to GitHub Container Registry (`ghcr.io`) for simple pull-and-run deployment.

---

## Quick Start: Running the Daemon

### 1. Prepare Host Directories

Create the configuration and key directories on your host:

```bash
mkdir -p /opt/homelab-updater/keys
```

### 2. Configure Authorized Signers (`allowed_signers`)

Create `/opt/homelab-updater/allowed_signers`:

```
your-email@example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIYourAuthorizedPublicKey Comment
```

Configure your local Git to sign commits with SSH:
```bash
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global gpg.format ssh
git config --global commit.gpgsign true
```

### 3. Configure Target Devices (`devices.yaml`)

Create `/opt/homelab-updater/devices.yaml`:

```yaml
devices:
  local:
    host: "172.17.0.1" # Host gateway IP or host.docker.internal
    user: "coder"
    port: 22
    ssh_key_path: "/etc/homelab-updater/keys/id_ed25519"
    strict_host_key_checking: "accept-new"

  tower:
    host: "192.168.1.100"
    user: "deployer"
    port: 22
    ssh_key_path: "/etc/homelab-updater/keys/id_ed25519"
    strict_host_key_checking: "accept-new"
```

Place your deployment SSH private key in `/opt/homelab-updater/keys/id_ed25519` and secure permissions:
```bash
chmod 600 /opt/homelab-updater/keys/id_ed25519
```

### 4. Run with Docker Compose

```yaml
version: "3.8"

services:
  homelab-updater:
    image: ghcr.io/<your-github-username>/homelab-update-service:latest
    container_name: homelab-updater
    restart: unless-stopped
    ports:
      - "7777:7777"
    environment:
      - HOMELAB_UPDATER_SECRET=your-random-secret-token-here
      - REQUIRE_COMMIT_SIGNATURE=true
    volumes:
      - /opt/homelab-updater/devices.yaml:/etc/homelab-updater/devices.yaml:ro
      - /opt/homelab-updater/allowed_signers:/etc/homelab-updater/allowed_signers:ro
      - /opt/homelab-updater/keys:/etc/homelab-updater/keys:ro
```

---

## Configuring a Service in your Homelab Repo

Inside any service folder (e.g. `services/s-workspaces-gateway/`):

### `deploy.yaml`
```yaml
# Destination device defined in devices.yaml
device: tower

# Absolute path on target system
target_dir: /opt/homelab/services/s-workspaces-gateway

# Bootstrap script relative to this service folder (defaults to bootstrap.sh)
bootstrap_script: bootstrap.sh

# Optional: Only copy the specific script instead of syncing the entire folder
# (Prevents overwriting host configs/volumes when updating container images)
only_copy_script: false

# Environment variables exported before bootstrap execution
environment:
  DEPLOY_ENV: production
```

### `bootstrap.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail

echo "==> Deploying service in $(pwd)"
docker compose pull || true
docker compose up -d --remove-orphans
echo "==> Service successfully started!"
```

Make it executable:
```bash
chmod +x bootstrap.sh
```

---

## Reverse Proxy Setup (Caddy)

Add to your `Caddyfile`:

```caddy
updater.homelab.yourdomain.com {
    reverse_proxy homelab-updater:7777
}
```

---

## GitHub Actions Integration

Add `.github/workflows/deploy.yml` to your homelab repository:
See [`examples/gha-webhook.yml.example`](examples/gha-webhook.yml.example) for a complete template with matrix change detection.

Add the following repository secrets to your GitHub repo:
- `HOMELAB_UPDATER_URL`: `https://updater.homelab.yourdomain.com/api/v1/deploy`
- `HOMELAB_UPDATER_SECRET`: Value matching `HOMELAB_UPDATER_SECRET` on your updater daemon.

---

## API Reference

### Health Check
```http
GET /healthz
```
Response:
```json
{
  "status": "ok",
  "service": "homelab-updater"
}
```

### Trigger Deployment
```http
POST /api/v1/deploy
Content-Type: application/json
X-Homelab-Token: <HOMELAB_UPDATER_SECRET>

{
  "repo_url": "https://github.com/myuser/homelab.git",
  "commit_sha": "f128bc16c27845f5a6b0c2ee5d9c02d1aa789912",
  "repo_relative_path": "services/s-workspaces-gateway",
  "git_branch": "main"
}
```

#### Payload Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `repo_url` | string | Yes | Git repository clone URL (HTTPS or SSH) |
| `commit_sha` | string | Yes | Exact git commit SHA to verify and deploy |
| `repo_relative_path` | string | No | Path to service folder (defaults to `""` for repository root) |
| `git_branch` | string | No | Git branch name for reference |
| `script` | string | No | Specific script to copy and run (overrides `bootstrap_script` in `deploy.yaml`) |
| `only_copy_script` | boolean | No | If `true`, only copies the target script instead of syncing the entire directory |

#### Triggering Container/Pod Image Updates (Script-Only Copy)

When a new container image is released (e.g. built in GitHub Actions) and you need to run commands on your target device (like a Raspberry Pi) to pull the new image and restart the container without syncing or wiping out directory contents:

```json
{
  "repo_url": "https://github.com/myuser/homelab-update-service.git",
  "commit_sha": "f128bc16c27845f5a6b0c2ee5d9c02d1aa789912",
  "repo_relative_path": ".",
  "script": "update-pod.sh",
  "only_copy_script": true
}
```

This ensures only `update-pod.sh` is transferred to `target_dir` (without `rsync --delete`), made executable, and run to pull images and restart containers/pods safely.

Response (`200 OK`):
```json
{
  "status": "success",
  "message": "Service 'services/s-workspaces-gateway' successfully deployed to device 'tower'.",
  "commit_sha": "f128bc16c27845f5a6b0c2ee5d9c02d1aa789912",
  "repo_relative_path": "services/s-workspaces-gateway",
  "device": "tower",
  "target_dir": "/opt/homelab/services/s-workspaces-gateway",
  "signature": {
    "verified": true,
    "status": "VALID",
    "signer": "maintainer@example.com",
    "key_id": "SHA256:abcd...",
    "details": "Good \"git\" signature"
  },
  "sync_summary": "rsync output...",
  "bootstrap_summary": "--- Bootstrap Output ---\nService successfully started!",
  "only_copy_script": false,
  "script_executed": "bootstrap.sh"
}
```
