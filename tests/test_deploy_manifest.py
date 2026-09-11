"""Unit tests for deploy_manifest module."""

import pytest
from pathlib import Path
from src.deploy_manifest import load_deploy_manifest
from src.exceptions import ManifestError


def test_load_deploy_manifest_valid(tmp_path: Path):
    service_dir = tmp_path / "my-service"
    service_dir.mkdir()
    manifest_file = service_dir / "deploy.yaml"
    manifest_file.write_text(
        """
device: "tower"
target_dir: "/opt/homelab/services/my-service"
bootstrap_script: "setup.sh"
environment:
  FOO: "bar"
pre_deploy_command: "docker stop old || true"
post_deploy_command: "docker ps"
rsync_exclude:
  - ".git*"
  - "node_modules"
"""
    )

    manifest = load_deploy_manifest(service_dir)
    assert manifest.device == "tower"
    assert manifest.target_dir == "/opt/homelab/services/my-service"
    assert manifest.bootstrap_script == "setup.sh"
    assert manifest.environment["FOO"] == "bar"
    assert manifest.pre_deploy_command == "docker stop old || true"
    assert "node_modules" in manifest.rsync_exclude


def test_load_deploy_manifest_defaults(tmp_path: Path):
    service_dir = tmp_path / "gateway"
    service_dir.mkdir()
    (service_dir / "deploy.yaml").write_text(
        """
device: "gateway-node"
target_dir: "/srv/gateway"
"""
    )
    manifest = load_deploy_manifest(service_dir)
    assert manifest.bootstrap_script == "bootstrap.sh"
    assert manifest.environment == {}
    assert manifest.pre_deploy_command is None


def test_load_deploy_manifest_missing_file(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ManifestError) as exc:
        load_deploy_manifest(empty_dir)
    assert "Missing deploy.yaml" in str(exc.value)


def test_load_deploy_manifest_relative_target_dir(tmp_path: Path):
    service_dir = tmp_path / "bad-service"
    service_dir.mkdir()
    (service_dir / "deploy.yaml").write_text(
        """
device: "tower"
target_dir: "relative/path"
"""
    )
    with pytest.raises(ManifestError) as exc:
        load_deploy_manifest(service_dir)
    assert "must be an absolute path" in str(exc.value)


def test_load_deploy_manifest_traversal_bootstrap(tmp_path: Path):
    service_dir = tmp_path / "traversal-service"
    service_dir.mkdir()
    (service_dir / "deploy.yaml").write_text(
        """
device: "tower"
target_dir: "/opt/service"
bootstrap_script: "../escape.sh"
"""
    )
    with pytest.raises(ManifestError) as exc:
        load_deploy_manifest(service_dir)
    assert "must not contain directory traversal" in str(exc.value)


def test_load_deploy_manifest_only_copy_script(tmp_path: Path):
    service_dir = tmp_path / "script-only-service"
    service_dir.mkdir()
    (service_dir / "deploy.yaml").write_text(
        """
device: "pi"
target_dir: "/opt/homelab/services/updater"
bootstrap_script: "update-pod.sh"
only_copy_script: true
"""
    )
    manifest = load_deploy_manifest(service_dir)
    assert manifest.device == "pi"
    assert manifest.bootstrap_script == "update-pod.sh"
    assert manifest.only_copy_script is True
