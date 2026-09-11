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
    assert response.json() == {"status": "ok", "service": "homelab-updater", "version": "0.1.0"}


def test_version_endpoint(client: TestClient):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "0.1.0"}


def test_cli_version(capsys: pytest.CaptureFixture):
    from src.app import main
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "homelab-updater 0.1.0" in captured.out or "homelab-updater 0.1.0" in captured.err


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
    assert data["only_copy_script"] is False


@patch("subprocess.run")
@patch("src.app.verify_commit_signature")
@patch("src.app.load_deploy_manifest")
@patch("src.app.get_device")
@patch("src.app.copy_script_to_device")
@patch("src.app.execute_bootstrap")
def test_deploy_only_copy_script_from_manifest(
    mock_exec_bootstrap: MagicMock,
    mock_copy_script: MagicMock,
    mock_get_dev: MagicMock,
    mock_load_manifest: MagicMock,
    mock_verify_sig: MagicMock,
    mock_sub_run: MagicMock,
    client: TestClient,
    tmp_path: Path,
):
    settings.staging_dir = str(tmp_path / "staging")
    settings.auth_token = "valid-token"

    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        res.stdout = ""
        res.stderr = ""
        if "clone" in cmd:
            repo_path = Path(cmd[-1])
            service_path = repo_path / "services" / "my-service"
            service_path.mkdir(parents=True, exist_ok=True)
            (service_path / "deploy.yaml").write_text("device: pi\ntarget_dir: /opt/updater")
            (service_path / "update-pod.sh").write_text("#!/bin/bash\necho update")
        return res

    mock_sub_run.side_effect = fake_subprocess_run
    mock_verify_sig.return_value = {
        "verified": True,
        "status": "VALID",
        "signer": "pi-deployer@homelab.local",
        "key_id": "SHA256:1234",
        "details": "Good signature",
    }
    mock_load_manifest.return_value = DeployManifest(
        device="pi",
        target_dir="/opt/homelab/services/updater",
        bootstrap_script="update-pod.sh",
        only_copy_script=True,
    )
    mock_get_dev.return_value = DeviceConfig(
        name="pi",
        host="192.168.1.50",
        user="pi",
    )
    mock_copy_script.return_value = "Script 'update-pod.sh' copied successfully."
    mock_exec_bootstrap.return_value = "Pod restarted successfully"

    response = client.post(
        "/api/v1/deploy",
        headers={"X-Homelab-Token": "valid-token"},
        json={
            "repo_url": "https://github.com/user/homelab.git",
            "commit_sha": "abcdef1234567890",
            "repo_relative_path": "services/my-service",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["only_copy_script"] is True
    assert data["script_executed"] == "update-pod.sh"
    assert "Script 'update-pod.sh' copied" in data["sync_summary"]
    mock_copy_script.assert_called_once()


@patch("subprocess.run")
@patch("src.app.verify_commit_signature")
@patch("src.app.load_deploy_manifest")
@patch("src.app.get_device")
@patch("src.app.copy_script_to_device")
@patch("src.app.execute_bootstrap")
def test_deploy_script_override_from_request(
    mock_exec_bootstrap: MagicMock,
    mock_copy_script: MagicMock,
    mock_get_dev: MagicMock,
    mock_load_manifest: MagicMock,
    mock_verify_sig: MagicMock,
    mock_sub_run: MagicMock,
    client: TestClient,
    tmp_path: Path,
):
    settings.staging_dir = str(tmp_path / "staging")
    settings.auth_token = "valid-token"

    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        res.stdout = ""
        res.stderr = ""
        if "clone" in cmd:
            repo_path = Path(cmd[-1])
            repo_path.mkdir(parents=True, exist_ok=True)
            (repo_path / "deploy.yaml").write_text("device: pi\ntarget_dir: /opt/updater")
            (repo_path / "custom-update.sh").write_text("#!/bin/bash\necho custom")
        return res

    mock_sub_run.side_effect = fake_subprocess_run
    mock_verify_sig.return_value = {
        "verified": True,
        "status": "VALID",
        "signer": "pi-deployer@homelab.local",
        "key_id": "SHA256:1234",
        "details": "Good signature",
    }
    mock_load_manifest.return_value = DeployManifest(
        device="pi",
        target_dir="/opt/homelab/services/updater",
        bootstrap_script="bootstrap.sh",
        only_copy_script=False,
    )
    mock_get_dev.return_value = DeviceConfig(
        name="pi",
        host="192.168.1.50",
        user="pi",
    )
    mock_copy_script.return_value = "Script 'custom-update.sh' copied successfully."
    mock_exec_bootstrap.return_value = "Custom update ran"

    response = client.post(
        "/api/v1/deploy",
        headers={"X-Homelab-Token": "valid-token"},
        json={
            "repo_url": "https://github.com/user/homelab.git",
            "commit_sha": "abcdef1234567890",
            "script": "custom-update.sh",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["only_copy_script"] is True
    assert data["script_executed"] == "custom-update.sh"
    mock_copy_script.assert_called_once()
    assert mock_exec_bootstrap.call_args[1]["script_name"] == "custom-update.sh"


@patch("subprocess.run")
@patch("src.app.verify_commit_signature")
@patch("src.app.load_deploy_manifest")
@patch("src.app.get_device")
def test_deploy_script_not_found(
    mock_get_dev: MagicMock,
    mock_load_manifest: MagicMock,
    mock_verify_sig: MagicMock,
    mock_sub_run: MagicMock,
    client: TestClient,
    tmp_path: Path,
):
    settings.staging_dir = str(tmp_path / "staging")
    settings.auth_token = "valid-token"

    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        if "clone" in cmd:
            repo_path = Path(cmd[-1])
            repo_path.mkdir(parents=True, exist_ok=True)
            (repo_path / "deploy.yaml").write_text("device: pi\ntarget_dir: /opt/updater")
        return res

    mock_sub_run.side_effect = fake_subprocess_run
    mock_verify_sig.return_value = {"verified": True, "status": "VALID", "signer": "user"}
    mock_load_manifest.return_value = DeployManifest(
        device="pi",
        target_dir="/opt/homelab/services/updater",
        bootstrap_script="nonexistent.sh",
        only_copy_script=True,
    )
    mock_get_dev.return_value = DeviceConfig(name="pi", host="192.168.1.50", user="pi")

    response = client.post(
        "/api/v1/deploy",
        headers={"X-Homelab-Token": "valid-token"},
        json={
            "repo_url": "https://github.com/user/homelab.git",
            "commit_sha": "abcdef1234567890",
        },
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@patch("subprocess.run")
@patch("src.app.verify_commit_signature")
@patch("src.app.load_deploy_manifest")
@patch("src.app.get_device")
def test_deploy_script_directory_traversal(
    mock_get_dev: MagicMock,
    mock_load_manifest: MagicMock,
    mock_verify_sig: MagicMock,
    mock_sub_run: MagicMock,
    client: TestClient,
    tmp_path: Path,
):
    settings.staging_dir = str(tmp_path / "staging")
    settings.auth_token = "valid-token"

    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        if "clone" in cmd:
            repo_path = Path(cmd[-1])
            repo_path.mkdir(parents=True, exist_ok=True)
            (repo_path / "deploy.yaml").write_text("device: pi\ntarget_dir: /opt/updater")
        return res

    mock_sub_run.side_effect = fake_subprocess_run
    mock_verify_sig.return_value = {"verified": True, "status": "VALID", "signer": "user"}
    mock_load_manifest.return_value = DeployManifest(
        device="pi",
        target_dir="/opt/homelab/services/updater",
        bootstrap_script="bootstrap.sh",
    )
    mock_get_dev.return_value = DeviceConfig(name="pi", host="192.168.1.50", user="pi")

    response = client.post(
        "/api/v1/deploy",
        headers={"X-Homelab-Token": "valid-token"},
        json={
            "repo_url": "https://github.com/user/homelab.git",
            "commit_sha": "abcdef1234567890",
            "script": "../../../etc/passwd",
        },
    )

    assert response.status_code == 400
    assert "directory traversal" in response.json()["detail"]
