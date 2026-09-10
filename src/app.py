"""FastAPI application for Homelab Updater Service."""

import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from src.config import Settings, get_device
from src.deploy_manifest import load_deploy_manifest
from src.exceptions import (
    AuthenticationError,
    DeploymentExecutionError,
    DeviceNotFoundError,
    ManifestError,
    SignatureVerificationError,
)
from src.ssh_executor import execute_bootstrap, sync_files_to_device
from src.verifier import verify_commit_signature

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("homelab_updater")

settings = Settings()
app = FastAPI(
    title="Homelab Updater Service",
    description="Daemon service to receive deployment webhooks and deploy verified updates across target homelab devices over SSH.",
    version="0.1.0",
)


class DeployRequest(BaseModel):
    """Payload model for deployment webhook."""
    repo_url: str = Field(description="Git repository clone URL (HTTPS or SSH)")
    commit_sha: str = Field(description="Exact git commit SHA to verify and deploy")
    repo_relative_path: str = Field(description="Relative path inside the repo corresponding to the service (e.g., 'services/s-workspaces-gateway')")
    git_branch: Optional[str] = Field(default=None, description="Optional git branch name for reference")


class DeployResponse(BaseModel):
    """Response returned upon successful deployment."""
    status: str
    message: str
    commit_sha: str
    repo_relative_path: str
    device: str
    target_dir: str
    signature: Dict[str, Any]
    sync_summary: str
    bootstrap_summary: Optional[str] = None


def verify_auth_token(
    x_homelab_token: Optional[str] = Header(default=None, alias="X-Homelab-Token"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    """Validate request authentication token if HOMELAB_UPDATER_SECRET is configured."""
    expected_secret = settings.auth_token.strip()
    if not expected_secret:
        # If no secret is configured, allow requests (warning logged at startup)
        return

    provided_token = None
    if x_homelab_token:
        provided_token = x_homelab_token.strip()
    elif authorization and authorization.startswith("Bearer "):
        provided_token = authorization.split("Bearer ", 1)[1].strip()

    if not provided_token or provided_token != expected_secret:
        logger.warning("Unauthorized deployment request received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Missing or invalid secret token.",
        )


@app.get("/healthz", tags=["System"])
def health_check() -> Dict[str, str]:
    """Liveness probe for reverse proxies (Caddy, Traefik, etc.)."""
    return {"status": "ok", "service": "homelab-updater"}


@app.post(
    "/api/v1/deploy",
    response_model=DeployResponse,
    dependencies=[Depends(verify_auth_token)],
    tags=["Deployment"],
)
def deploy_service(request: DeployRequest) -> DeployResponse:
    """
    Receive deployment webhook, verify cryptographic signature of git commit,
    read service deploy.yaml, and deploy to the configured target device over SSH.
    """
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    job_staging_dir = Path(settings.staging_dir).resolve() / job_id
    repo_checkout_dir = job_staging_dir / "repo"

    logger.info(
        "Starting deployment job %s for %s @ %s",
        job_id,
        request.repo_relative_path,
        request.commit_sha,
    )

    try:
        job_staging_dir.mkdir(parents=True, exist_ok=True)

        # 1. Clone repository into temporary staging area
        logger.info("Cloning repository %s into %s", request.repo_url, repo_checkout_dir)
        clone_cmd = [
            "git", "clone", "--depth", "50",
            request.repo_url, str(repo_checkout_dir)
        ]
        clone_proc = subprocess.run(
            clone_cmd,
            capture_output=True,
            text=True,
            timeout=settings.git_timeout_seconds,
            check=False,
        )
        if clone_proc.returncode != 0:
            # Try full clone if shallow fails
            clone_proc = subprocess.run(
                ["git", "clone", request.repo_url, str(repo_checkout_dir)],
                capture_output=True,
                text=True,
                timeout=settings.git_timeout_seconds,
                check=False,
            )
            if clone_proc.returncode != 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to clone git repository: {clone_proc.stderr.strip()}",
                )

        # 2. Checkout specified commit SHA
        checkout_proc = subprocess.run(
            ["git", "checkout", request.commit_sha],
            cwd=str(repo_checkout_dir),
            capture_output=True,
            text=True,
            timeout=settings.git_timeout_seconds,
            check=False,
        )
        if checkout_proc.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to checkout commit {request.commit_sha}: {checkout_proc.stderr.strip()}",
            )

        # 3. Cryptographic Signature Verification
        try:
            sig_info = verify_commit_signature(
                repo_dir=repo_checkout_dir,
                commit_sha=request.commit_sha,
                allowed_signers_file=settings.allowed_signers_file,
                require_signature=settings.require_commit_signature,
                timeout_seconds=settings.git_timeout_seconds,
            )
        except SignatureVerificationError as exc:
            logger.error("Signature verification failed for job %s: %s", job_id, exc)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cryptographic Signature Verification Rejected: {exc}",
            )

        # 4. Resolve service directory inside repo
        clean_rel_path = request.repo_relative_path.strip().strip("/")
        service_dir = (repo_checkout_dir / clean_rel_path).resolve()

        if not service_dir.is_dir() or not str(service_dir).startswith(str(repo_checkout_dir)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target path '{request.repo_relative_path}' does not exist or escapes repository bounds.",
            )

        # 5. Load and validate service deploy.yaml
        try:
            manifest = load_deploy_manifest(service_dir)
        except ManifestError as exc:
            logger.error("Failed to load deploy.yaml for job %s: %s", job_id, exc)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

        # 6. Lookup target device in devices.yaml
        try:
            device = get_device(
                device_name=manifest.device,
                devices_path=settings.devices_file,
                default_ssh_key=settings.default_ssh_key_path,
            )
        except DeviceNotFoundError as exc:
            logger.error("Target device not found for job %s: %s", job_id, exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

        # 7. Sync service directory to target device over SSH (rsync)
        try:
            sync_output = sync_files_to_device(
                source_dir=service_dir,
                device=device,
                manifest=manifest,
                timeout_seconds=settings.ssh_timeout_seconds,
            )
        except DeploymentExecutionError as exc:
            logger.error("Sync failed for job %s: %s", job_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"SSH rsync failed: {exc}",
            )

        # 8. Execute bootstrap script and post-deploy commands
        try:
            bootstrap_output = execute_bootstrap(
                device=device,
                manifest=manifest,
                timeout_seconds=settings.ssh_timeout_seconds,
            )
        except DeploymentExecutionError as exc:
            logger.error("Bootstrap execution failed for job %s: %s", job_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Bootstrap execution failed: {exc}",
            )

        logger.info("Job %s completed successfully for device '%s'", job_id, device.name)
        return DeployResponse(
            status="success",
            message=f"Service '{request.repo_relative_path}' successfully deployed to device '{device.name}'.",
            commit_sha=request.commit_sha,
            repo_relative_path=request.repo_relative_path,
            device=device.name,
            target_dir=manifest.target_dir,
            signature=sig_info,
            sync_summary=sync_output,
            bootstrap_summary=bootstrap_output,
        )

    finally:
        # Staging cleanup
        if job_staging_dir.exists():
            shutil.rmtree(job_staging_dir, ignore_errors=True)


def main() -> None:
    """CLI launcher for running the updater daemon."""
    logger.info("Starting Homelab Updater Service on %s:%s", settings.host, settings.port)
    uvicorn.run(
        "src.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
