"""Errors of the Docker test harness.

Separated from ``docker.py``, like ``constants.py``, so that callers can catch
them without the optional ``docker`` package installed.
"""

from enum import StrEnum


class DockerErrorKind(StrEnum):
    UNAVAILABLE = "unavailable"
    IMAGE_BUILD_FAILED = "image-build-failed"
    REQUEST_FAILED = "request-failed"


class DockerError(OSError):
    """A Docker step of the demo or test harness failed; *detail* is the
    Docker SDK's message."""

    def __init__(self, kind: DockerErrorKind, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
