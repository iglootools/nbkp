"""Shared disk fixtures: the LUKS volume ``enc`` and hosts that answer for it."""

import subprocess

from nbkp.config import LocalVolume, LuksEncryptionConfig, MountConfig
from nbkp.disks import UmountResult
from nbkp.disks.models import LifecycleFailure, LifecycleStep, UmountFailureKind
from tests.fakes import FakeCommandRunner, completed

UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"

LOCK_FAILED = UmountResult(
    "enc",
    success=False,
    failure_reason=UmountFailureKind.LOCK_FAILED,
    failure=LifecycleFailure(step=LifecycleStep.LOCK, exit_code=1, message="busy"),
)


def encrypted_volume(path: str = "/mnt/enc") -> LocalVolume:
    return LocalVolume(
        slug="enc",
        path=path,
        mount=MountConfig(
            device_uuid=UUID, encryption=LuksEncryptionConfig(passphrase_id="enc")
        ),
    )


def mounted_drive_host(
    lock: subprocess.CompletedProcess[str], *, path: str = "/mnt/enc"
) -> FakeCommandRunner:
    """A host where ``enc`` is plugged in, unlocked and mounted at *path*,
    and ``udisksctl lock`` answers *lock*."""
    return (
        FakeCommandRunner()
        .on("lsblk", returns=completed(stdout=f"sdb part\nluks-{UUID} crypt\n"))
        .on("findmnt", "--source", returns=completed(stdout=f"{path}\n"))
        .on("udisksctl", "lock", returns=lock)
    )


BUSY = completed(1, stderr="busy")
