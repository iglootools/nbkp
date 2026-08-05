"""Tests for nbkp.disks.lifecycle (udisks2 backend)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.disks.lifecycle import (
    MountFailureReason,
    mount_volume,
    mount_volumes,
    umount_volume,
    umount_volumes,
)

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


class TestMountVolume:
    def test_device_not_present(self) -> None:
        vol = _encrypted_vol()
        with patch("nbkp.disks.lifecycle.detect_device_present", return_value=False):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.DEVICE_NOT_PRESENT
        assert result.device_present is False
        assert "not plugged in" in (result.detail or "")

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
        # The mount command targets the by-uuid device.
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
        # No udisksctl invocation when already mounted.
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
        # Second run_on_volume mounts the discovered cleartext device.
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
        # Only the mount command runs (no unlock).
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
        assert "unlock failed" in (result.detail or "")

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
        assert "polkit" in (result.detail or "")

    def test_unlock_stdin_closed_classified_as_not_authorized(self) -> None:
        from nbkp.remote.fabricssh import STDIN_CLOSED_MARKER

        vol = _encrypted_vol()
        with (
            patch("nbkp.disks.lifecycle.detect_device_present", return_value=True),
            patch("nbkp.disks.lifecycle.discover_cleartext_device", return_value=None),
            patch(
                "nbkp.disks.lifecycle.run_on_volume",
                return_value=_mock_run(1, stderr=STDIN_CLOSED_MARKER),
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
        assert "mount failed" in (result.detail or "")

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

    def test_mount_volume_swallows_multiline_exception(self) -> None:
        vol = _encrypted_vol()
        multiline_msg = "first line\nsecond line\nthird line"
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=RuntimeError(multiline_msg),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UNREACHABLE
        assert "\n" not in (result.detail or "")
        assert "first line" in (result.detail or "")
        assert "second line" not in (result.detail or "")

    def test_ssh_timeout_returns_unreachable(self) -> None:
        vol = _encrypted_vol()
        with patch(
            "nbkp.disks.lifecycle.detect_device_present",
            side_effect=TimeoutError("timed out"),
        ):
            result = mount_volume(vol, vol.mount, {}, lambda x: "pass")  # type: ignore[arg-type]
        assert not result.success
        assert result.failure_reason == MountFailureReason.UNREACHABLE
        assert "timed out" in (result.detail or "")

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
        # only lock runs (no unmount)
        assert len(run_cmds) == 1
        assert run_cmds[0][:2] == ["udisksctl", "lock"]

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
            ),
        ):
            result = umount_volume(vol, mc, {})
        assert not result.success
        assert result.warning is not None

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
        assert "timed out" in (result.detail or "")
        assert result.warning is not None

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
