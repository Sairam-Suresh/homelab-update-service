"""Unit tests for verifier module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.exceptions import SignatureVerificationError
from src.verifier import verify_commit_signature


def test_verify_commit_signature_skipped(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    result = verify_commit_signature(
        repo_dir=repo_dir,
        commit_sha="abcdef123456",
        allowed_signers_file=tmp_path / "allowed_signers",
        require_signature=False,
    )
    assert result["verified"] is True
    assert result["status"] == "SKIPPED"


def test_verify_commit_signature_missing_signers_file(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    missing_signers = tmp_path / "non_existent_signers"
    with pytest.raises(SignatureVerificationError) as exc:
        verify_commit_signature(
            repo_dir=repo_dir,
            commit_sha="abcdef123456",
            allowed_signers_file=missing_signers,
            require_signature=True,
        )
    assert "Allowed signers file not found" in str(exc.value)


@patch("subprocess.run")
def test_verify_commit_signature_valid(mock_run: MagicMock, tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    signers_file = tmp_path / "allowed_signers"
    signers_file.write_text("user@example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")

    # Mock git log status check (%G?|%GS|%GK) returning Good ('G')
    mock_status_proc = MagicMock()
    mock_status_proc.returncode = 0
    mock_status_proc.stdout = "G|user@example.com|SHA256:1234567890"

    # Mock git verify-commit returning 0
    mock_verify_proc = MagicMock()
    mock_verify_proc.returncode = 0
    mock_verify_proc.stdout = "Good \"git\" signature for user@example.com with ED25519 key"
    mock_verify_proc.stderr = ""

    mock_run.side_effect = [mock_status_proc, mock_verify_proc]

    result = verify_commit_signature(
        repo_dir=repo_dir,
        commit_sha="1234567890abcdef",
        allowed_signers_file=signers_file,
        require_signature=True,
    )
    assert result["verified"] is True
    assert result["status"] == "VALID"
    assert result["signer"] == "user@example.com"
    assert result["key_id"] == "SHA256:1234567890"


@patch("subprocess.run")
def test_verify_commit_signature_unsigned(mock_run: MagicMock, tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    signers_file = tmp_path / "allowed_signers"
    signers_file.write_text("user@example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")

    mock_status_proc = MagicMock()
    mock_status_proc.returncode = 0
    mock_status_proc.stdout = "N||"

    mock_verify_proc = MagicMock()
    mock_verify_proc.returncode = 1
    mock_verify_proc.stdout = ""
    mock_verify_proc.stderr = "error: no signature found"

    mock_run.side_effect = [mock_status_proc, mock_verify_proc]

    with pytest.raises(SignatureVerificationError) as exc:
        verify_commit_signature(
            repo_dir=repo_dir,
            commit_sha="1234567890abcdef",
            allowed_signers_file=signers_file,
            require_signature=True,
        )
    assert "is not signed" in str(exc.value)


@patch("subprocess.run")
def test_verify_commit_signature_untrusted_signer(mock_run: MagicMock, tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    signers_file = tmp_path / "allowed_signers"
    signers_file.write_text("user@example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")

    mock_status_proc = MagicMock()
    mock_status_proc.returncode = 0
    mock_status_proc.stdout = "U|attacker@evil.com|SHA256:badkey"

    mock_verify_proc = MagicMock()
    mock_verify_proc.returncode = 1
    mock_verify_proc.stdout = ""
    mock_verify_proc.stderr = "error: key not found in allowed_signers"

    mock_run.side_effect = [mock_status_proc, mock_verify_proc]

    with pytest.raises(SignatureVerificationError) as exc:
        verify_commit_signature(
            repo_dir=repo_dir,
            commit_sha="1234567890abcdef",
            allowed_signers_file=signers_file,
            require_signature=True,
        )
    assert "NOT in allowed_signers list" in str(exc.value)
