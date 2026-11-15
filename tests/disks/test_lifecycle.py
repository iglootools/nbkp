"""Tests for nbkp.disks.lifecycle (udisks2 backend)."""

import subprocess
from unittest.mock import patch

import paramiko
import pytest

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.credentials import CredentialError, CredentialErrorReason
from nbkp.disks.detection import DeviceProbeError
from nbkp.disks.lifecycle import (
    MountFailureReason,
    mount_count,
    mount_volume,
    mount_volumes,
    run_udisks,
    umount_volume,
    umount_volumes,
)
from nbkp.disks.models import (
    LifecycleFailure,
    LifecycleStep,
    MountCapabilities,
    UmountFailureReason,
)
from nbkp.disks.plan import plan_lifecycle
from nbkp.remote.endpoints import EndpointFilter, ResolvedEndpoints
from nbkp.remote.fabricssh import StdinClosedProcess
from nbkp.remote.resolution import resolve_all_endpoints

_ENC_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"
_USB_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CLEARTEXT = f"/dev/mapper/luks-{_ENC_UUID}"


def _mock_run(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _encrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="encrypted",
        path="/mnt/encrypted",
        mount=MountConfig(
            device_uuid=_ENC_UUID,
            encryption=LuksEncryptionConfig(passphrase_id="encrypted"),
        ),
    )


def _unencrypted_vol() -> LocalVolume:
    return LocalVolume(
        slug="usb",
        path="/mnt/usb",
        mount=MountConfig(device_uuid=_USB_UUID),
    )


@pytest.fixture(autouse=True)
def _no_udev_settle():
    """Keep the best-effort ``udevadm settle`` off the machine running the tests."""
    with patch("nbkp.disks.lifecycle.settle_udev") as settle:
        yield settle


_LOOKUP_ERROR = "Error looking up object for device /dev/disk/by-uuid/" + _ENC_UUID


class TestRunUdisks:
    def test_retries_while_udisks_has_no_object_for_the_device(
        self, _no_udev_settle
    ) -> None:
        sleeps: list[float] = []
        with patch(
            "nbkp.disks.lifecycle.run_on_volume",
            side_effect=[
                _mock_run(1, stderr=_LOOKUP_ERROR),
                _mock_run(1, stderr=_LOOKUP_ERROR),
                _mock_run(0, stdout="Unlocked"),
            ],
        ) as mock_run:
            result = run_udisks(
                ["udisksctl", "unlock"],
                _encrypted_vol(),
                {},
                input="secret",
                sleep=sleeps.append,
            )
        assert result.returncode == 0
        assert mock_run.call_count == 3
        assert all(c.kwargs["input"] == "secret" for c in mock_run.call_args_list)
        assert _no_udev_settle.call_count == 2
        assert len(sleeps) == 2

    def test_gives_up_after_bounded_attempts(self) -> None:
        sleeps: list[float] = []
        with patch(
            "nbkp.disks.lifecycle.run_on_volume",
            return_value=_mock_run(1, stderr=_LOOKUP_ERROR),
        ) as mock_run:
            result = run_udisks(
                ["udisksctl", "unlock"], _encrypted_vol(), {}, sleep=sleeps.append
            )
        assert result.returncode == 1
        assert mock_run.call_count == len(sleeps) + 1
        assert 1 < mock_run.call_count < 20

    def test_wrong_passphrase_is_not_retried(self) -> None:
        sleeps: list[float] = []
        with patch(
            "nbkp.disks.lifecycle.run_on_volume",
            return_value=_mock_run(
                1, stderr="Error unlocking /dev/sda1: Failed to activate device"
            ),
        ) as mock_run:
            run_udisks(
                ["udisksctl", "unlock"], _encrypted_vol(), {}, sleep=sleeps.append
            )
        assert mock_run.call_count == 1
        assert sleeps == []


class TestMountVolume:
    def test_settles_udev_before_looking_for_the_device(self, _no_udev_settle) -> None:
        """Regression: a check right after a lock found the by-uuid link missing."""
        order: list[str] = []
        _no_udev_settle.side_effect = lambda *_: order.append("settle")
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=lambda *_: order.append("detect") or False,
        ):
            mount_volume(_unencrypted_vol(), _unencrypted_vol().mount, {}, lambda _: "")
        assert order == ["settle", "detect"]

    def test_device_not_present(self) -> None:
        vol = _encrypted_vol()
        with patch("nbkp.disks.lifecycle.detect_device_present", return_value=False):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.DEVICE_NOT_PRESENT
        assert result.device_present is False
        assert result.failure == LifecycleFailure(device_uuid=_ENC_UUID)

    def test_unencrypted_mount(self) -> None:
        vol = _unencrypted_vol()
        # find_mountpoint: first call (pre-mount) -> None, second -> path.
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.find_mountpoint",
                side_effect=[None, "/mnt/usb"],
            ),
            patch(
                "nbkp.disks.lifecycle.run_on_volume", return_value=_mock_run(0)
            ) as mock_run,
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.success
        assert result.mounted is True
        assert result.luks_unlocked is None
        assert result.effective_path == "/mnt/usb"
        mount_cmd = mock_run.call_args_list[-1][0][0]
        assert mount_cmd == [
            "udisksctl",
            "mount",
            "-b",
            f"/dev/disk/by-uuid/{_USB_UUID}",
            "--no-user-interaction",
        ]

    def test_already_mounted_is_noop(self) -> None:
        vol = _unencrypted_vol()
        # find_mountpoint returns a path immediately -> no mount command run.
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/usb"),
            patch(
                "nbkp.disks.lifecycle.run_on_volume", return_value=_mock_run(0)
            ) as mock_run,
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.success
        assert result.mounted is True
        mock_run.assert_not_called()

    def test_encrypted_unlock_then_mount(self) -> None:
        vol = _encrypted_vol()
        # discover_cleartext_device: locked first, unlocked after unlock.
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                side_effect=[None, _CLEARTEXT],
            ),
            patch(
                "nbkp.disks.lifecycle.find_mountpoint",
                side_effect=[None, "/mnt/encrypted"],
            ),
            patch(
                "nbkp.disks.lifecycle.run_on_volume", return_value=_mock_run(0)
            ) as mock_run,
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "secret")  # type: ignore[arg-type]
        assert result.success
        assert result.luks_unlocked is True
        assert result.mounted is True
        assert result.cleartext_device == _CLEARTEXT
        # First run_on_volume is the unlock command piping the passphrase
        # via stdin with NO trailing newline.
        unlock_call = mock_run.call_args_list[0]
        unlock_cmd = unlock_call[0][0]
        assert unlock_cmd[:2] == ["udisksctl", "unlock"]
        assert unlock_call.kwargs["input"] == "secret"
        mount_cmd = mock_run.call_args_list[1][0][0]
        assert mount_cmd[:3] == ["udisksctl", "mount", "-b"]
        assert mount_cmd[3] == _CLEARTEXT

    def test_encrypted_already_unlocked_skips_unlock(self) -> None:
        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                return_value=_CLEARTEXT,
            ),
            patch(
                "nbkp.disks.lifecycle.find_mountpoint",
                side_effect=[None, "/mnt/encrypted"],
            ),
            patch(
                "nbkp.disks.lifecycle.run_on_volume", return_value=_mock_run(0)
            ) as mock_run,
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "secret")  # type: ignore[arg-type]
        assert result.success
        assert len(mock_run.call_args_list) == 1
        assert mock_run.call_args_list[0][0][0][:2] == ["udisksctl", "mount"]

    def test_unlock_failure(self) -> None:
        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.discover_cleartext_device", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr="Error unlocking: device busy"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "secret")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UNLOCK_FAILED
        assert result.luks_unlocked is False
        assert result.failure == LifecycleFailure(
            step=LifecycleStep.UNLOCK,
            exit_code=1,
            message="Error unlocking: device busy",
        )

    def test_unlock_polkit_denied_classified_as_not_authorized(self) -> None:
        """Polkit refusal under --no-user-interaction -> NOT_AUTHORIZED."""
        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.discover_cleartext_device", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(
                    1, stderr="Error unlocking: Not Authorized to perform operation"
                ),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "secret")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.NOT_AUTHORIZED
        assert result.failure is not None
        assert result.failure.step is LifecycleStep.UNLOCK

    def test_unlock_stdin_closed_classified_as_not_authorized(self) -> None:
        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.discover_cleartext_device", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=StdinClosedProcess(args="udisksctl unlock"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "secret")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.NOT_AUTHORIZED

    def test_mount_failure(self) -> None:
        vol = _unencrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr="mount: unknown filesystem"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.MOUNT_FAILED
        assert result.mounted is False
        assert result.failure == LifecycleFailure(
            step=LifecycleStep.MOUNT, exit_code=1, message="mount: unknown filesystem"
        )

    def test_mount_polkit_denied_classified_as_not_authorized(self) -> None:
        vol = _unencrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr="Error mounting: Not Authorized"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.NOT_AUTHORIZED

    def test_mount_udisks_not_available(self) -> None:
        vol = _unencrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(127, stderr="udisksctl: command not found"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UDISKS_NOT_AVAILABLE

    def test_mount_udisks_not_available_under_dash(self) -> None:
        # Verbatim dash output (`sh -c`, as on a Debian host) for a missing
        # binary, captured with exit code 127.
        vol = _unencrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(127, stderr="sh: 1: udisksctl: not found"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.failure_reason == MountFailureReason.UDISKS_NOT_AVAILABLE

    def test_udisks_unknown_device_is_a_mount_failure(self) -> None:
        """udisks failing to find a device does not mean udisks is missing."""
        # Verbatim udisksctl 2.10.1 (Debian 13) output, exit code 1, for a
        # device udisks has no object for.
        vol = _unencrypted_vol()
        stderr = f"Error looking up object for device /dev/disk/by-uuid/{_USB_UUID}"
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr=stderr),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.failure_reason == MountFailureReason.MOUNT_FAILED

    def test_mount_volume_swallows_multiline_exception(self) -> None:
        vol = _encrypted_vol()
        multiline_msg = "first line\nsecond line\nthird line"
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=OSError(multiline_msg),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UNREACHABLE
        assert result.failure == LifecycleFailure(message="first line")

    def test_programming_error_propagates(self) -> None:
        """Only connection failures become UNREACHABLE; a bug must surface."""
        vol = _encrypted_vol()
        with (
            patch(
                "nbkp.disks.lifecycle.detect_device_present",
                side_effect=RuntimeError("bug"),
            ),
            pytest.raises(RuntimeError),
        ):
            mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]

    def test_auth_failure_returns_unreachable(self) -> None:
        vol = _encrypted_vol()
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=paramiko.AuthenticationException("Authentication failed."),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.failure_reason == MountFailureReason.UNREACHABLE
        assert result.failure == LifecycleFailure(message="Authentication failed.")

    def test_missing_passphrase_on_plugged_in_drive(self) -> None:
        """A plugged-in drive whose passphrase cannot be retrieved fails loudly.

        Regression: the CredentialError used to be swallowed as UNREACHABLE,
        so the drive was silently skipped as "inactive".
        """
        vol = _encrypted_vol()

        def no_passphrase(pid: str) -> str:
            raise CredentialError(
                f"No passphrase found in keyring for id '{pid}'",
                reason=CredentialErrorReason.NOT_FOUND,
            )

        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.discover_cleartext_device", return_value=None),
            patch("nbkp.disks.lifecycle.run_on_volume") as mock_run,
        ):
            result = mount_volume(vol, vol.mount, {}, no_passphrase)  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.PASSPHRASE_NOT_AVAILABLE
        assert result.device_present is True
        assert result.luks_unlocked is False
        mock_run.assert_not_called()

    def test_failed_lsblk_probe_is_not_locked(self) -> None:
        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                side_effect=DeviceProbeError("lsblk", 1, "boom"),
            ),
            patch("nbkp.disks.lifecycle.run_on_volume") as mock_run,
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.failure_reason == MountFailureReason.PROBE_FAILED
        # No unlock attempted on a guess.
        mock_run.assert_not_called()

    def test_ssh_timeout_returns_unreachable(self) -> None:
        vol = _encrypted_vol()
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=TimeoutError("timed out"),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UNREACHABLE
        assert result.failure == LifecycleFailure(message="timed out")

    def test_missing_local_tool_is_udisks_not_available(self) -> None:
        """Locally, FileNotFoundError is a missing tool, not an unreachable host."""
        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                side_effect=FileNotFoundError(2, "No such file", "lsblk"),
            ),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert result.failure_reason == MountFailureReason.UDISKS_NOT_AVAILABLE
        assert result.failure == LifecycleFailure(command="lsblk")

    def test_connection_refused_returns_unreachable(self) -> None:
        vol = _encrypted_vol()
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UNREACHABLE


class TestUmountVolume:
    def test_umount_and_lock_luks(self) -> None:
        vol = _encrypted_vol()
        mc = vol.mount
        assert mc is not None
        run_cmds: list[list[str]] = []

        def mock_run(cmd, volume, resolved, **kwargs):
            run_cmds.append(cmd)
            return _mock_run(0)

        with (
            patch(
                "nbkp.disks.lifecycle.resolve_target_device", return_value=_CLEARTEXT
            ),
            patch(
                "nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/encrypted"
            ),
            patch("nbkp.disks.lifecycle.run_on_volume", side_effect=mock_run),
        ):
            result = umount_volume(vol, mc, {})
        assert result.success
        # unmount, then lock
        assert run_cmds[0][:2] == ["udisksctl", "unmount"]
        assert run_cmds[1][:2] == ["udisksctl", "lock"]

    def test_not_mounted_still_locks_luks(self) -> None:
        vol = _encrypted_vol()
        mc = vol.mount
        assert mc is not None
        run_cmds: list[list[str]] = []

        def mock_run(cmd, volume, resolved, **kwargs):
            run_cmds.append(cmd)
            return _mock_run(0)

        with (
            patch(
                "nbkp.disks.lifecycle.resolve_target_device", return_value=_CLEARTEXT
            ),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
            patch("nbkp.disks.lifecycle.run_on_volume", side_effect=mock_run),
        ):
            result = umount_volume(vol, mc, {})
        assert result.success
        assert len(run_cmds) == 1
        assert run_cmds[0][:2] == ["udisksctl", "lock"]

    def test_lock_failure_after_unmount_is_a_failure(self) -> None:
        """The drive must end locked: a failed lock is not a successful umount."""
        vol = _encrypted_vol()
        mc = vol.mount
        assert mc is not None
        run_cmds: list[list[str]] = []

        def mock_run(cmd, volume, resolved, **kwargs):
            run_cmds.append(cmd)
            match cmd[:2]:
                case ["udisksctl", "lock"]:
                    return _mock_run(1, stderr="Error locking: device busy")
                case _:
                    return _mock_run(0)

        with (
            patch(
                "nbkp.disks.lifecycle.resolve_target_device", return_value=_CLEARTEXT
            ),
            patch(
                "nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/encrypted"
            ),
            patch("nbkp.disks.lifecycle.run_on_volume", side_effect=mock_run),
        ):
            result = umount_volume(vol, mc, {})
        assert [cmd[:2] for cmd in run_cmds] == [
            ["udisksctl", "unmount"],
            ["udisksctl", "lock"],
        ]
        assert not result.success
        assert result.failure_reason == UmountFailureReason.LOCK_FAILED
        assert result.failure == LifecycleFailure(
            step=LifecycleStep.LOCK, exit_code=1, message="Error locking: device busy"
        )

    def test_umount_failure(self) -> None:
        vol = _encrypted_vol()
        mc = vol.mount
        assert mc is not None
        with (
            patch(
                "nbkp.disks.lifecycle.resolve_target_device", return_value=_CLEARTEXT
            ),
            patch(
                "nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/encrypted"
            ),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr="target is busy"),
            ) as mock_run,
        ):
            result = umount_volume(vol, mc, {})
        assert not result.success
        assert result.failure_reason == UmountFailureReason.UNMOUNT_FAILED
        # Locking a mounted container cannot succeed: it is not attempted.
        assert mock_run.call_count == 1

    def test_ssh_timeout_returns_failed(self) -> None:
        vol = _encrypted_vol()
        mc = vol.mount
        assert mc is not None
        with patch(
            "nbkp.disks.lifecycle.resolve_target_device",
            side_effect=TimeoutError("timed out"),
        ):
            result = umount_volume(vol, mc, {})
        assert not result.success
        assert result.failure_reason == UmountFailureReason.UNREACHABLE
        assert result.failure == LifecycleFailure(message="timed out")

    def test_missing_local_tool_is_not_unreachable(self) -> None:
        vol = _unencrypted_vol()
        mc = vol.mount
        assert mc is not None
        with patch(
            "nbkp.disks.lifecycle.find_mountpoint",
            side_effect=FileNotFoundError(2, "No such file", "findmnt"),
        ):
            result = umount_volume(vol, mc, {})
        assert not result.success
        assert result.failure_reason == UmountFailureReason.UDISKS_NOT_AVAILABLE
        assert result.failure == LifecycleFailure(command="findmnt")

    def test_failed_probe_is_reported_not_skipped(self) -> None:
        """A failed lsblk must not be read as "locked" and skip the lock."""
        vol = _encrypted_vol()
        mc = vol.mount
        assert mc is not None
        with (
            patch(
                "nbkp.disks.lifecycle.resolve_target_device",
                side_effect=DeviceProbeError("lsblk", 1, "boom"),
            ),
            patch("nbkp.disks.lifecycle.run_on_volume") as mock_run,
        ):
            result = umount_volume(vol, mc, {})
        assert not result.success
        assert result.failure_reason == UmountFailureReason.PROBE_FAILED
        assert result.failure == LifecycleFailure(
            command="lsblk", exit_code=1, message="boom"
        )
        mock_run.assert_not_called()

    def test_failed_findmnt_is_reported_not_skipped(self) -> None:
        """A failed findmnt must not be read as "not mounted" and skip unmount."""
        vol = _unencrypted_vol()
        mc = vol.mount
        assert mc is not None
        with (
            patch(
                "nbkp.disks.detection.run_on_volume",
                return_value=_mock_run(127, stderr="findmnt: command not found"),
            ),
            patch("nbkp.disks.lifecycle.run_on_volume") as mock_udisks,
        ):
            result = umount_volume(vol, mc, {})
        assert not result.success
        assert result.failure_reason == UmountFailureReason.PROBE_FAILED
        assert result.failure == LifecycleFailure(
            command="findmnt", exit_code=127, message="findmnt: command not found"
        )
        mock_udisks.assert_not_called()  # no unmount on a guess

    def test_unencrypted_umount_no_lock(self) -> None:
        vol = _unencrypted_vol()
        mc = vol.mount
        assert mc is not None
        run_cmds: list[list[str]] = []

        def mock_run(cmd, volume, resolved, **kwargs):
            run_cmds.append(cmd)
            return _mock_run(0)

        with (
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/usb"),
            patch("nbkp.disks.lifecycle.run_on_volume", side_effect=mock_run),
        ):
            result = umount_volume(vol, mc, {})
        assert result.success
        # Only unmount, no lock for unencrypted.
        assert len(run_cmds) == 1
        assert run_cmds[0][:2] == ["udisksctl", "unmount"]


def _config_with_mount() -> Config:
    return Config(
        volumes={
            "enc": _encrypted_vol(),
            "usb": _unencrypted_vol(),
            "plain": LocalVolume(slug="plain", path="/mnt/plain"),
        },
        sync_endpoints={
            "ep-enc": SyncEndpoint(slug="ep-enc", volume="enc"),
            "ep-usb": SyncEndpoint(slug="ep-usb", volume="usb"),
            "ep-plain": SyncEndpoint(slug="ep-plain", volume="plain"),
        },
        syncs={
            "s1": SyncConfig(slug="s1", source="ep-enc", destination="ep-usb"),
            "s2": SyncConfig(slug="s2", source="ep-usb", destination="ep-plain"),
        },
    )


class TestMountVolumes:
    def test_only_mount_config_volumes(self) -> None:
        cfg = _config_with_mount()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch(
                "nbkp.disks.lifecycle.discover_cleartext_device",
                return_value=_CLEARTEXT,
            ),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/x"),
            patch("nbkp.disks.lifecycle.run_on_volume", return_value=_mock_run(0)),
        ):
            results = mount_volumes(cfg, {}, lambda x: "pass")
        # Only 2 volumes have mount config (enc, usb), not plain.
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_filter_by_name(self) -> None:
        cfg = _config_with_mount()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/usb"),
            patch("nbkp.disks.lifecycle.run_on_volume", return_value=_mock_run(0)),
        ):
            results = mount_volumes(cfg, {}, lambda x: "pass", names=["usb"])
        assert len(results) == 1
        assert results[0].volume_slug == "usb"

    def test_returns_list(self) -> None:
        cfg = _config_with_mount()
        with patch("nbkp.disks.lifecycle.detect_device_present", return_value=False):
            results = mount_volumes(cfg, {}, lambda x: "pass")
        assert isinstance(results, list)
        assert all(not r.success for r in results)


class TestUmountVolumes:
    def test_reverse_order(self) -> None:
        cfg = _config_with_mount()
        slugs: list[str] = []
        with (
            patch("nbkp.disks.lifecycle.resolve_target_device", return_value=None),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
        ):
            umount_volumes(
                cfg,
                {},
                on_umount_start=lambda slug: slugs.append(slug),
            )
        # Should be reversed: usb first, then enc.
        assert slugs == ["usb", "enc"]

    def test_returns_list(self) -> None:
        cfg = _config_with_mount()
        with (
            patch("nbkp.disks.lifecycle.resolve_target_device", return_value=None),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
        ):
            results = umount_volumes(cfg, {})
        assert isinstance(results, list)
        assert len(results) == 2


def _config_with_excluded_remote_mount() -> tuple[Config, ResolvedEndpoints]:
    """``usb`` (local) and ``nas-drive`` (remote, only reachable from home),
    resolved under ``--exclude-location home``."""
    cfg = Config(
        ssh_endpoints={
            "nas": SshEndpoint(slug="nas", host="nas.lan", location="home"),
        },
        volumes={
            "nas-drive": RemoteVolume(
                slug="nas-drive",
                ssh_endpoint="nas",
                path="/mnt/nas-drive",
                mount=MountConfig(device_uuid=_ENC_UUID),
            ),
            "usb": _unencrypted_vol(),
        },
    )
    resolved = resolve_all_endpoints(
        cfg,
        EndpointFilter(exclude_locations=["home"]),
        ssh_config=paramiko.SSHConfig(),
        lookup=lambda _host: {"192.168.1.10"},
    )
    return cfg, resolved


class TestLocationExcludedVolumes:
    """A volume excluded by location has no endpoint: nothing may reach it."""

    def test_mount_skips_excluded_volume(self) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value="/mnt/usb"),
        ):
            results = mount_volumes(cfg, resolved, lambda x: "pass")
        assert [r.volume_slug for r in results] == ["usb"]

    def test_umount_skips_excluded_volume(self) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        with (
            patch("nbkp.disks.lifecycle.resolve_target_device", return_value=None),
            patch("nbkp.disks.lifecycle.find_mountpoint", return_value=None),
        ):
            results = umount_volumes(cfg, resolved)
        assert [r.volume_slug for r in results] == ["usb"]

    def test_mount_count_leaves_out_excluded_volume(self) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        assert mount_count(cfg, resolved) == 1

    @pytest.mark.parametrize("mounting", [True, False])
    def test_plan_skips_excluded_volume(self, mounting: bool) -> None:
        cfg, resolved = _config_with_excluded_remote_mount()
        with patch(
            "nbkp.disks.plan.check_mount_status",
            return_value=MountCapabilities(device_present=False),
        ):
            plans = plan_lifecycle(cfg, resolved, mounting=mounting)
        assert [p.volume_slug for p in plans] == ["usb"]
