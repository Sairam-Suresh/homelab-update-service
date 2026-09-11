"""Unit tests for ssh_executor module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.config import DeviceConfig
from src.deploy_manifest import DeployManifest
from src.exceptions import DeploymentExecutionError
from src.ssh_executor import (
    build_ssh_base_command,
    copy_script_to_device,
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


@patch("src.ssh_executor.run_remote_command")
@patch("subprocess.run")
def test_copy_script_to_device(
    mock_sub_run: MagicMock,
    mock_remote_cmd: MagicMock,
    sample_device: DeviceConfig,
    sample_manifest: DeployManifest,
    tmp_path: Path,
):
    script_file = tmp_path / "update-pod.sh"
    script_file.write_text("#!/bin/bash\necho update")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "sent 45 bytes"
    mock_proc.stderr = ""
    mock_sub_run.return_value = mock_proc

    output = copy_script_to_device(
        script_path=script_file,
        device=sample_device,
        manifest=sample_manifest,
        script_rel_name="update-pod.sh",
    )

    assert "update-pod.sh" in output
    assert "sent 45 bytes" in output
    # Check that mkdir -p and chmod +x were executed remotely
    assert mock_remote_cmd.call_count == 2
    mkdir_call = mock_remote_cmd.call_args_list[0][0][1]
    assert "mkdir -p" in mkdir_call
    chmod_call = mock_remote_cmd.call_args_list[1][0][1]
    assert "chmod +x" in chmod_call

    # Verify rsync was called without --delete
    rsync_args = mock_sub_run.call_args[0][0]
    assert "--delete" not in rsync_args
    assert str(script_file.resolve()) in rsync_args


def test_copy_script_to_device_missing_file(
    sample_device: DeviceConfig,
    sample_manifest: DeployManifest,
    tmp_path: Path,
):
    missing_script = tmp_path / "nonexistent.sh"
    with pytest.raises(DeploymentExecutionError) as exc:
        copy_script_to_device(
            script_path=missing_script,
            device=sample_device,
            manifest=sample_manifest,
            script_rel_name="nonexistent.sh",
        )
    assert "Staged script does not exist" in str(exc.value)


@patch("src.ssh_executor.run_remote_command")
def test_execute_bootstrap_script_override(
    mock_remote_cmd: MagicMock,
    sample_device: DeviceConfig,
    sample_manifest: DeployManifest,
):
    mock_remote_cmd.side_effect = ["Custom script done", "Post done"]

    output = execute_bootstrap(
        sample_device,
        sample_manifest,
        script_name="custom-update.sh"
    )
    assert output is not None
    assert "Custom script done" in output
    cmd_run = mock_remote_cmd.call_args_list[0][0][1]
    assert "custom-update.sh" in cmd_run
