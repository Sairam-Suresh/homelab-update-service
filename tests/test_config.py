"""Unit tests for config module."""

import pytest
from pathlib import Path
from src.config import Settings, load_devices_config, get_device
from src.exceptions import DeviceNotFoundError


def test_load_devices_config(tmp_path: Path):
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text(
        """
devices:
  tower:
    host: "192.168.0.10"
    user: "homelab"
    port: 2222
    ssh_key_path: "/keys/tower_id"
  nas:
    host: "192.168.0.20"
    user: "admin"
"""
    )

    devices = load_devices_config(devices_file, default_ssh_key="/keys/default_id")
    assert len(devices) == 2
    assert "tower" in devices
    assert devices["tower"].host == "192.168.0.10"
    assert devices["tower"].user == "homelab"
    assert devices["tower"].port == 2222
    assert devices["tower"].ssh_key_path == "/keys/tower_id"

    # Default key fallback check
    assert devices["nas"].ssh_key_path == "/keys/default_id"
    assert devices["nas"].port == 22


def test_get_device_success(tmp_path: Path):
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text(
        """
devices:
  mini-pc:
    host: "10.0.0.5"
    user: "coder"
"""
    )
    dev = get_device("mini-pc", devices_file)
    assert dev.name == "mini-pc"
    assert dev.host == "10.0.0.5"


def test_get_device_not_found(tmp_path: Path):
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")
    with pytest.raises(DeviceNotFoundError) as exc_info:
        get_device("non-existent", devices_file)
    assert "Device 'non-existent' not found" in str(exc_info.value)
