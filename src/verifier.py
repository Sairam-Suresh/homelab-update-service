"""Cryptographic Git commit signature verification module."""

import subprocess
import logging
from pathlib import Path
from typing import Dict, Any
from src.exceptions import SignatureVerificationError

logger = logging.getLogger(__name__)


def verify_commit_signature(
    repo_dir: Path | str,
    commit_sha: str,
    allowed_signers_file: Path | str,
    require_signature: bool = True,
    timeout_seconds: int = 30
) -> Dict[str, Any]:
    """
    Verify the cryptographic signature (SSH or GPG) of a git commit.
    
    Uses git commit verification against an allowed_signers file.
    
    Args:
        repo_dir: Path to local git repository checkout.
        commit_sha: Commit hash to verify.
        allowed_signers_file: Path to SSH allowed_signers file.
        require_signature: Whether an unsigned or untrusted commit should fail.
        timeout_seconds: Maximum execution time for git commands.
        
    Returns:
        Dict with status, signer, key_id, and details.
        
    Raises:
        SignatureVerificationError: If verification fails or signature is untrusted.
    """
    repo_path = Path(repo_dir).resolve()
    if not repo_path.is_dir():
        raise SignatureVerificationError(f"Repository directory does not exist: {repo_path}")

    if not require_signature:
        logger.warning("Commit signature verification is disabled by configuration.")
        return {
            "verified": True,
            "status": "SKIPPED",
            "signer": "unknown",
            "key_id": "none",
            "details": "Signature verification was skipped by configuration."
        }

    signers_path = Path(allowed_signers_file).resolve()
    if not signers_path.is_file():
        raise SignatureVerificationError(
            f"Allowed signers file not found at '{signers_path}'. Cannot verify commit cryptographic signature."
        )

    # 1. Inspect signature status code using git format specifiers:
    # %G? = G (Good), B (Bad), U (Untrusted/Unknown), N (No signature), E (Cannot check), etc.
    # %GS = Signer name
    # %GK = Key fingerprint/ID
    status_cmd = [
        "git",
        "-c", f"gpg.ssh.allowedSignersFile={signers_path}",
        "log", "-1", "--format=%G?|%GS|%GK",
        commit_sha
    ]

    try:
        status_proc = subprocess.run(
            status_cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False
        )
    except subprocess.SubprocessError as exc:
        raise SignatureVerificationError(f"Failed to execute git signature status check: {exc}") from exc

    if status_proc.returncode != 0:
        raise SignatureVerificationError(
            f"Git log failed for commit {commit_sha}: {status_proc.stderr.strip()}"
        )

    output = status_proc.stdout.strip()
    parts = output.split("|", 2)
    sig_status = parts[0] if len(parts) > 0 else "N"
    signer = parts[1] if len(parts) > 1 else ""
    key_id = parts[2] if len(parts) > 2 else ""

    # 2. Run git verify-commit for detailed diagnostics
    verify_cmd = [
        "git",
        "-c", f"gpg.ssh.allowedSignersFile={signers_path}",
        "verify-commit",
        commit_sha
    ]

    try:
        verify_proc = subprocess.run(
            verify_cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False
        )
    except subprocess.SubprocessError as exc:
        raise SignatureVerificationError(f"Failed to execute git verify-commit: {exc}") from exc

    verify_output = (verify_proc.stdout + "\n" + verify_proc.stderr).strip()

    # Evaluation
    # 'G' = Good signature and trusted key in allowed_signers
    if sig_status == "G" and verify_proc.returncode == 0:
        logger.info(
            "Cryptographic signature verified successfully for commit %s (Signer: %s, Key: %s)",
            commit_sha, signer, key_id
        )
        return {
            "verified": True,
            "status": "VALID",
            "signer": signer,
            "key_id": key_id,
            "details": verify_output
        }

    # Handle failure cases
    error_reasons = {
        "N": f"Commit {commit_sha} is not signed.",
        "B": f"Commit {commit_sha} has a BAD cryptographic signature (tampered or corrupted).",
        "U": f"Commit {commit_sha} has a signature, but the signer is NOT in allowed_signers list.",
        "E": f"Could not check signature for commit {commit_sha} (missing key or toolchain error).",
        "X": f"Signature for commit {commit_sha} has expired.",
        "Y": f"Signature key for commit {commit_sha} has expired.",
        "R": f"Signature key for commit {commit_sha} has been revoked."
    }
    reason = error_reasons.get(sig_status, f"Signature verification returned status '{sig_status}'.")
    full_message = f"{reason}\nGit Output:\n{verify_output}"
    logger.error("Signature verification failed: %s", full_message)
    raise SignatureVerificationError(full_message)
