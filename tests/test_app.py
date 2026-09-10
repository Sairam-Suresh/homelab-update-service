"""Unit and API integration tests for FastAPI application."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.app import app, settings
from src.config import DeviceConfig
from src.deploy_manifest import DeployManifest


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_healthz(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "homelab-updater"}


def test_deploy_unauthorized(client: TestClient):
    # Set a secret token
    original_secret = settings.auth_token
    try:
        settings.auth_token = "super-secret-token"
        response = client.post(
            "/api/v1/deploy",
            json={
                "repo_url": "https://github.com/user/homelab.git",
                "commit_sha": "abcdef123456",
                "repo_relative_path": "services/my-app",
            },
        )
        assert response.status_code == 401
    finally:
        settings.auth_token = original_secret


@patch("subprocess.run")
@patch("src.app.verify_commit_signature")
@patch("src.app.load_deploy_manifest")
@patch("src.app.get_device")
@patch("src.app.sync_files_to_device")
@patch("src.app.execute_bootstrap")
def test_deploy_success(
    mock_exec_bootstrap: MagicMock,
    mock_sync: MagicMock,
    mock_get_dev: MagicMock,
    mock_load_manifest: MagicMock,
    mock_verify_sig: MagicMock,
    mock_sub_run: MagicMock,
    client: TestClient,
    tmp_path: Path,
):
    # Setup test staging
    settings.staging_dir = str(tmp_path / "staging")
    settings.auth_token = "valid-token"

    # Mock subprocess for git clone and checkout
    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        res.stdout = ""
        res.stderr = ""
        # Create service dir inside the cloned repo path
        if "clone" in cmd:
            repo_path = Path(cmd[-1])
            service_path = repo_path / "services" / "my-app"
            service_path.mkdir(parents=True, exist_ok=True)
            (service_path / "deploy.yaml").write_text("device: tower\ntarget_dir: /opt/app")
        return res

    mock_sub_run.side_effect = fake_subprocess_run

    mock_verify_sig.return_value = {
        "verified": True,
        "status": "VALID",
        "signer": "maintainer@homelab.local",
        "key_id": "SHA256:abcd",
        "details": "Good signature",
    }

    mock_load_manifest.return_value = DeployManifest(
        device="tower",
        target_dir="/opt/homelab/services/my-app",
        bootstrap_script="bootstrap.sh",
    )

    mock_get_dev.return_value = DeviceConfig(
        name="tower",
        host="192.168.0.10",
        user="coder",
    )

    mock_sync.return_value = "rsync complete: 15 files transferred"
    mock_exec_bootstrap.return_value = "Docker compose up -d succeeded"

    response = client.post(
        "/api/v1/deploy",
        headers={"X-Homelab-Token": "valid-token"},
        json={
            "repo_url": "https://github.com/user/homelab.git",
            "commit_sha": "abcdef1234567890",
            "repo_relative_path": "services/my-app",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["device"] == "tower"
    assert data["target_dir"] == "/opt/homelab/services/my-app"
    assert data["signature"]["signer"] == "maintainer@homelab.local"
    assert "rsync complete" in data["sync_summary"]
    assert "Docker compose up -d" in data["bootstrap_summary"]
