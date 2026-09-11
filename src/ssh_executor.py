"""SSH execution and rsync deployment module."""

import logging
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from src.config import DeviceConfig
from src.deploy_manifest import DeployManifest
from src.exceptions import DeploymentExecutionError

logger = logging.getLogger(__name__)


def build_ssh_base_command(device: DeviceConfig) -> List[str]:
    """Construct base ssh arguments for interacting with the target device."""
    cmd = [
        "ssh",
        "-p", str(device.port),
        "-o", f"ConnectTimeout={device.connect_timeout}",
        "-o", f"StrictHostKeyChecking={device.strict_host_key_checking}",
    ]
    if device.ssh_key_path:
        key_path = Path(device.ssh_key_path).expanduser().resolve()
        if key_path.is_file():
            cmd.extend(["-i", str(key_path)])
        else:
            logger.warning("SSH key path '%s' not found for device '%s'", key_path, device.name)
    return cmd


def run_remote_command(
    device: DeviceConfig,
    remote_command: str,
    timeout_seconds: int = 300
) -> str:
    """Execute a shell command on the remote target device over SSH."""
    base_cmd = build_ssh_base_command(device)
    target = f"{device.user}@{device.host}"
    full_cmd = base_cmd + [target, remote_command]

    logger.info("Executing remote command on %s (%s): %s", device.name, target, remote_command)
    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise DeploymentExecutionError(
            f"Remote command timed out after {timeout_seconds}s on {device.name}: {remote_command}"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise DeploymentExecutionError(
            f"Failed to run SSH command on {device.name}: {exc}"
        ) from exc

    output = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        raise DeploymentExecutionError(
            f"Remote command failed on {device.name} (exit code {proc.returncode}):\n{output}"
        )

    return output


def sync_files_to_device(
    source_dir: Path | str,
    device: DeviceConfig,
    manifest: DeployManifest,
    timeout_seconds: int = 300
) -> str:
    """
    Synchronize files from local staging directory to remote target destination using rsync.
    Ensures the target directory exists before running rsync.
    """
    src_path = Path(source_dir).resolve()
    if not src_path.is_dir():
        raise DeploymentExecutionError(f"Staged source directory does not exist: {src_path}")

    target_dir = manifest.target_dir.rstrip("/")
    target = f"{device.user}@{device.host}"

    # 1. Ensure remote target directory exists
    run_remote_command(
        device,
        f"mkdir -p {shlex.quote(target_dir)}",
        timeout_seconds=min(60, timeout_seconds)
    )

    # 2. Run pre-deploy command if defined
    if manifest.pre_deploy_command:
        logger.info("Running pre-deploy command on %s", device.name)
        run_remote_command(
            device,
            f"cd {shlex.quote(target_dir)} && {manifest.pre_deploy_command}",
            timeout_seconds=timeout_seconds
        )

    # 3. Construct rsync command
    ssh_opts = f"ssh -p {device.port} -o ConnectTimeout={device.connect_timeout} -o StrictHostKeyChecking={device.strict_host_key_checking}"
    if device.ssh_key_path and Path(device.ssh_key_path).expanduser().is_file():
        ssh_opts += f" -i {shlex.quote(str(Path(device.ssh_key_path).expanduser().resolve()))}"

    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e", ssh_opts
    ]

    for pattern in manifest.rsync_exclude:
        rsync_cmd.extend(["--exclude", pattern])

    # Trailing slash on source ensures contents are synced into target_dir
    rsync_cmd.append(f"{src_path}/")
    rsync_cmd.append(f"{target}:{target_dir}/")

    logger.info("Syncing %s to %s:%s via rsync", src_path, device.name, target_dir)
    try:
        proc = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise DeploymentExecutionError(f"rsync timed out after {timeout_seconds}s to {device.name}") from exc
    except subprocess.SubprocessError as exc:
        raise DeploymentExecutionError(f"rsync process failed to start: {exc}") from exc

    output = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        raise DeploymentExecutionError(f"rsync to {device.name} failed (exit code {proc.returncode}):\n{output}")

    logger.info("File sync to %s completed successfully.", device.name)
    return output


def copy_script_to_device(
    script_path: Path | str,
    device: DeviceConfig,
    manifest: DeployManifest,
    script_rel_name: str,
    timeout_seconds: int = 300
) -> str:
    """
    Copy only the specified script to the target device without syncing the entire directory.
    Uses rsync without --delete to ensure existing device files and configurations are preserved.
    """
    local_script = Path(script_path).resolve()
    if not local_script.is_file():
        raise DeploymentExecutionError(f"Staged script does not exist: {local_script}")

    target_dir = manifest.target_dir.rstrip("/")
    target = f"{device.user}@{device.host}"
    clean_rel_name = script_rel_name.strip().lstrip("/").removeprefix("./")
    remote_script_path = f"{target_dir}/{clean_rel_name}"
    remote_parent_dir = str(Path(remote_script_path).parent)

    # 1. Ensure remote target parent directory exists
    run_remote_command(
        device,
        f"mkdir -p {shlex.quote(remote_parent_dir)}",
        timeout_seconds=min(60, timeout_seconds)
    )

    # 2. Run pre-deploy command if defined
    if manifest.pre_deploy_command:
        logger.info("Running pre-deploy command on %s", device.name)
        run_remote_command(
            device,
            f"cd {shlex.quote(target_dir)} && {manifest.pre_deploy_command}",
            timeout_seconds=timeout_seconds
        )

    # 3. Construct rsync command for single script
    ssh_opts = f"ssh -p {device.port} -o ConnectTimeout={device.connect_timeout} -o StrictHostKeyChecking={device.strict_host_key_checking}"
    if device.ssh_key_path and Path(device.ssh_key_path).expanduser().is_file():
        ssh_opts += f" -i {shlex.quote(str(Path(device.ssh_key_path).expanduser().resolve()))}"

    rsync_cmd = [
        "rsync",
        "-avz",
        "-e", ssh_opts,
        str(local_script),
        f"{target}:{remote_script_path}"
    ]

    logger.info("Copying script %s to %s:%s via rsync", local_script, device.name, remote_script_path)
    try:
        proc = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise DeploymentExecutionError(f"rsync timed out after {timeout_seconds}s to {device.name}") from exc
    except subprocess.SubprocessError as exc:
        raise DeploymentExecutionError(f"rsync process failed to start: {exc}") from exc

    output = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        raise DeploymentExecutionError(f"rsync to {device.name} failed (exit code {proc.returncode}):\n{output}")

    # 4. Ensure executable permissions on remote script
    run_remote_command(
        device,
        f"chmod +x {shlex.quote(remote_script_path)}",
        timeout_seconds=min(60, timeout_seconds)
    )

    logger.info("Script copy to %s completed successfully.", device.name)
    return f"Script '{clean_rel_name}' copied successfully to '{remote_script_path}'.\n{output}"


def execute_bootstrap(
    device: DeviceConfig,
    manifest: DeployManifest,
    script_name: Optional[str] = None,
    timeout_seconds: int = 300
) -> Optional[str]:
    """
    Execute bootstrap script and optional post-deploy commands on target device.
    If script_name is provided, it overrides manifest.bootstrap_script.
    """
    target_dir = manifest.target_dir.rstrip("/")
    outputs: List[str] = []

    active_script = script_name or manifest.bootstrap_script
    if active_script:
        clean_script = active_script.strip().lstrip("/").removeprefix("./")
        script_full_remote = f"{target_dir}/{clean_script}"

        # Build exported environment variables string
        env_exports = ""
        if manifest.environment:
            exports = [f"export {k}={shlex.quote(v)}" for k, v in manifest.environment.items()]
            env_exports = " && ".join(exports) + " && "

        bootstrap_cmd = (
            f"if [ -f {shlex.quote(script_full_remote)} ]; then "
            f"chmod +x {shlex.quote(script_full_remote)} && "
            f"cd {shlex.quote(target_dir)} && "
            f"{env_exports}./{shlex.quote(clean_script)}; "
            f"else echo 'Bootstrap script {clean_script} not found, skipping execution.'; fi"
        )

        logger.info("Executing bootstrap script '%s' on %s", clean_script, device.name)
        bootstrap_output = run_remote_command(device, bootstrap_cmd, timeout_seconds=timeout_seconds)
        outputs.append(f"--- Bootstrap Output ---\n{bootstrap_output}")

    if manifest.post_deploy_command:
        logger.info("Executing post-deploy command on %s", device.name)
        post_cmd = f"cd {shlex.quote(target_dir)} && {manifest.post_deploy_command}"
        post_output = run_remote_command(device, post_cmd, timeout_seconds=timeout_seconds)
        outputs.append(f"--- Post-Deploy Output ---\n{post_output}")

    return "\n\n".join(outputs) if outputs else None
