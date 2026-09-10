"""Unit tests for ssh_executor module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.config import DeviceConfig
from src.deploy_manifest import DeployManifest
from src.exceptions import DeploymentExecutionError
from src.ssh_executor import (
    build_ssh_base_command,
    execute_bootstrap,
    run_remote_command,
    sync_files_to_device,
)


@pytest.fixture
def sample_device(tmp_path: Path) -> DeviceConfig:
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy key")
    return DeviceConfig(
        name="tower",
        host="192.168.1.100",
        user="deployer",
        port=2222,
        ssh_key_path=str(key_file),
    )


@pytest.fixture
def sample_manifest() -> DeployManifest:
    return DeployManifest(
        device="tower",
        target_dir="/opt/homelab/services/app",
        bootstrap_script="bootstrap.sh",
        environment={"APP_ENV": "production"},
        post_deploy_command="echo 'deployed'",
    )


def test_build_ssh_base_command(sample_device: DeviceConfig):
    cmd = build_ssh_base_command(sample_device)
    assert "ssh" in cmd
    assert "-p" in cmd
    assert "2222" in cmd
    assert "-i" in cmd
    assert sample_device.ssh_key_path in cmd


@patch("subprocess.run")
def test_run_remote_command_success(mock_run: MagicMock, sample_device: DeviceConfig):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Remote success"
    mock_proc.stderr = ""
    mock_run.return_value = mock_proc

    output = run_remote_command(sample_device, "whoami")
    assert "Remote success" in output


@patch("subprocess.run")
def test_run_remote_command_failure(mock_run: MagicMock, sample_device: DeviceConfig):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Permission denied"
    mock_run.return_value = mock_proc

    with pytest.raises(DeploymentExecutionError) as exc:
        run_remote_command(sample_device, "docker ps")
    assert "Remote command failed" in str(exc.value)


@patch("src.ssh_executor.run_remote_command")
@patch("subprocess.run")
def test_sync_files_to_device(
    mock_sub_run: MagicMock,
    mock_remote_cmd: MagicMock,
    sample_device: DeviceConfig,
    sample_manifest: DeployManifest,
    tmp_path: Path,
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "docker-compose.yml").write_text("version: '3'")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "sent 120 bytes"
    mock_proc.stderr = ""
    mock_sub_run.return_value = mock_proc

    output = sync_files_to_device(source_dir, sample_device, sample_manifest)
    assert "sent 120 bytes" in output
    mock_remote_cmd.assert_called_once()  # mkdir -p call


@patch("src.ssh_executor.run_remote_command")
def test_execute_bootstrap(
    mock_remote_cmd: MagicMock,
    sample_device: DeviceConfig,
    sample_manifest: DeployManifest,
):
    mock_remote_cmd.side_effect = ["Bootstrap done", "Post done"]

    output = execute_bootstrap(sample_device, sample_manifest)
    assert output is not None
    assert "Bootstrap done" in output
    assert "Post done" in output
    assert mock_remote_cmd.call_count == 2
