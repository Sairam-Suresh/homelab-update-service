"""Configuration and settings management for Homelab Updater Service."""

from pathlib import Path
from typing import Dict, Optional
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.exceptions import DeviceNotFoundError, HomelabUpdaterError


class DeviceConfig(BaseModel):
    """Configuration for an SSH target device."""
    name: str = ""
    host: str
    user: str
    port: int = 22
    ssh_key_path: Optional[str] = None
    connect_timeout: int = 15
    strict_host_key_checking: str = "accept-new"


class Settings(BaseSettings):
    """Global service settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    auth_token: str = Field(default="", validation_alias="HOMELAB_UPDATER_SECRET")
    allowed_signers_file: str = Field(default="/etc/homelab-updater/allowed_signers", validation_alias="ALLOWED_SIGNERS_FILE")
    devices_file: str = Field(default="/etc/homelab-updater/devices.yaml", validation_alias="DEVICES_FILE")
    staging_dir: str = Field(default="/tmp/homelab-staging", validation_alias="STAGING_DIR")
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=7777, validation_alias="PORT")
    default_ssh_key_path: str = Field(default="/etc/homelab-updater/keys/id_ed25519", validation_alias="DEFAULT_SSH_KEY_PATH")
    require_commit_signature: bool = Field(default=True, validation_alias="REQUIRE_COMMIT_SIGNATURE")
    git_timeout_seconds: int = Field(default=120, validation_alias="GIT_TIMEOUT_SECONDS")
    ssh_timeout_seconds: int = Field(default=300, validation_alias="SSH_TIMEOUT_SECONDS")


def load_devices_config(devices_path: Path | str, default_ssh_key: Optional[str] = None) -> Dict[str, DeviceConfig]:
    """Load and parse devices.yaml file into DeviceConfig mappings."""
    path = Path(devices_path)
    if not path.is_file():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise HomelabUpdaterError(f"Failed to parse devices file at {path}: {exc}") from exc

    raw_devices = data.get("devices", {})
    devices: Dict[str, DeviceConfig] = {}

    for name, cfg in raw_devices.items():
        if not isinstance(cfg, dict):
            continue
        ssh_key = cfg.get("ssh_key_path") or default_ssh_key
        devices[name] = DeviceConfig(
            name=name,
            host=cfg["host"],
            user=cfg["user"],
            port=cfg.get("port", 22),
            ssh_key_path=ssh_key,
            connect_timeout=cfg.get("connect_timeout", 15),
            strict_host_key_checking=cfg.get("strict_host_key_checking", "accept-new"),
        )

    return devices


def get_device(device_name: str, devices_path: Path | str, default_ssh_key: Optional[str] = None) -> DeviceConfig:
    """Retrieve a device configuration by name or raise DeviceNotFoundError."""
    devices = load_devices_config(devices_path, default_ssh_key=default_ssh_key)
    if device_name not in devices:
        available = ", ".join(devices.keys()) if devices else "none"
        raise DeviceNotFoundError(
            f"Device '{device_name}' not found in {devices_path}. Configured devices: {available}"
        )
    return devices[device_name]
