"""Service deployment manifest (deploy.yaml) parser and validator."""

from pathlib import Path
from typing import Dict, List, Optional
import yaml
from pydantic import BaseModel, Field, field_validator
from src.exceptions import ManifestError


class DeployManifest(BaseModel):
    """Schema for deploy.yaml located inside each service folder."""
    device: str = Field(description="Target device identifier defined in devices.yaml")
    target_dir: str = Field(description="Absolute path on the target system where files will be deployed")
    bootstrap_script: Optional[str] = Field(
        default="bootstrap.sh",
        description="Path to bootstrap script relative to service directory"
    )
    environment: Dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to export before running bootstrap script"
    )
    pre_deploy_command: Optional[str] = Field(
        default=None,
        description="Optional command to execute on target system before rsync"
    )
    post_deploy_command: Optional[str] = Field(
        default=None,
        description="Optional command to execute on target system after bootstrap"
    )
    rsync_exclude: List[str] = Field(
        default_factory=lambda: [".git*", "__pycache__", "*.pyc", ".DS_Store"],
        description="Patterns to exclude during rsync"
    )

    @field_validator("device")
    @classmethod
    def validate_device(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Field 'device' must not be empty.")
        return clean

    @field_validator("target_dir")
    @classmethod
    def validate_target_dir(cls, v: str) -> str:
        clean = v.strip()
        if not clean.startswith("/"):
            raise ValueError(f"Field 'target_dir' must be an absolute path starting with '/', got: {v}")
        if ".." in clean.split("/"):
            raise ValueError(f"Field 'target_dir' must not contain directory traversal '..', got: {v}")
        return clean

    @field_validator("bootstrap_script")
    @classmethod
    def validate_bootstrap_script(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            return None
        if ".." in clean.split("/"):
            raise ValueError(f"Field 'bootstrap_script' must not contain directory traversal '..', got: {v}")
        return clean


def load_deploy_manifest(service_dir: Path | str) -> DeployManifest:
    """
    Load and validate deploy.yaml from the given service directory.
    
    Args:
        service_dir: Path to the service directory.
        
    Returns:
        Validated DeployManifest model.
        
    Raises:
        ManifestError: If deploy.yaml is missing or contains invalid syntax/fields.
    """
    path = Path(service_dir).resolve()
    manifest_file = path / "deploy.yaml"
    if not manifest_file.is_file():
        # Fallback check for deploy.yml
        manifest_file = path / "deploy.yml"
        if not manifest_file.is_file():
            raise ManifestError(
                f"Missing deploy.yaml in service directory '{service_dir}'. "
                f"A deploy.yaml defining 'device' and 'target_dir' is required."
            )

    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ManifestError(f"YAML parsing error in {manifest_file}: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise ManifestError(f"deploy.yaml in {service_dir} must be a dictionary, got: {type(raw_data).__name__}")

    try:
        return DeployManifest.model_validate(raw_data)
    except Exception as exc:
        raise ManifestError(f"Invalid deploy.yaml configuration in {service_dir}: {exc}") from exc
