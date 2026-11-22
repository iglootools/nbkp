"""
Errors of the Docker test harness.

Separated from ``docker.py``, like ``constants.py``, so that callers can catch
them without the optional ``docker`` package installed.
"""

from enum import StrEnum


class DockerErrorKind(StrEnum):
    """Which Docker step of the harness failed."""

    UNAVAILABLE = "unavailable"
    IMAGE_BUILD_FAILED = "image-build-failed"
    REQUEST_FAILED = "request-failed"


class DockerError(OSError):
    """
    A Docker step of the demo or test harness failed.

    *detail* is the Docker SDK's message.
    """

    def __init__(self, kind: DockerErrorKind, detail: str) -> None:
        """Build the error from its kind and the Docker SDK's message."""
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class SshNotReadyError(TimeoutError):
    """A container's SSH daemon sent no banner within *timeout_seconds*."""

    def __init__(self, timeout_seconds: int) -> None:
        """Build the error from the seconds waited for the banner."""
        super().__init__(f"SSH not ready after {timeout_seconds}s")
        self.timeout_seconds = timeout_seconds


class SentinelCreationError(OSError):
    """Creating the sentinel *sentinel_name* failed; *stderr* is the remote's."""

    def __init__(self, sentinel_name: str, stderr: str) -> None:
        """Build the error from the sentinel's name and the remote's stderr."""
        super().__init__(f"Failed to create sentinel {sentinel_name}: {stderr}")
        self.sentinel_name = sentinel_name
        self.stderr = stderr
