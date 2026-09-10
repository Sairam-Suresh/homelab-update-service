"""Custom exception definitions for Homelab Updater Service."""


class HomelabUpdaterError(Exception):
    """Base exception for all Homelab Updater errors."""
    pass


class AuthenticationError(HomelabUpdaterError):
    """Raised when request authentication fails."""
    pass


class SignatureVerificationError(HomelabUpdaterError):
    """Raised when Git cryptographic signature verification fails."""
    pass


class ManifestError(HomelabUpdaterError):
    """Raised when deploy.yaml is missing or invalid."""
    pass


class DeviceNotFoundError(HomelabUpdaterError):
    """Raised when the specified target device is not defined in devices.yaml."""
    pass


class DeploymentExecutionError(HomelabUpdaterError):
    """Raised when SSH file transfer or bootstrap execution fails."""
    pass
