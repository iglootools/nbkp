"""Tests for nbkp.disks.detection."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from nbkp.config import (
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks.detection import (
    detect_device_present,
    discover_cleartext_device,
    find_mountpoint,
    resolve_effective_path,
    resolve_target_device,
)

_UUID = "5941f273-f73c-44c5-a3ef-fae7248db1b6"


def _local_vol(path: str | None = "/mnt/disk") -> LocalVolume:
    if path is None:
        return LocalVolume(
            slug="disk",
            mount=MountConfig(device_uuid=_UUID),
        )
    return LocalVolume(slug="disk", path=path)


def _mount_config(encrypted: bool) -> MountConfig:
    if encrypted:
        return MountConfig(
            device_uuid=_UUID,
            encryption=LuksEncryptionConfig(passphrase_id="disk"),
        )
    return MountConfig(device_uuid=_UUID)


def _mock_run(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestDetectDevicePresent:
    def test_present(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)):
            assert detect_device_present(_local_vol(), _UUID, {})

    def test_not_present(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert not detect_device_present(_local_vol(), _UUID, {})

    def test_passes_correct_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)
        ) as mock:
            detect_device_present(_local_vol(), "aaaa-bbbb-cccc-dddd", {})
        mock.assert_called_once()
        cmd = mock.call_args[0][0]
        assert cmd == ["test", "-e", "/dev/disk/by-uuid/aaaa-bbbb-cccc-dddd"]


class TestDiscoverCleartextDevice:
    def test_passes_lsblk_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout=f"luks-{_UUID} crypt\n"),
        ) as mock:
            discover_cleartext_device(_local_vol(), _UUID, {})
        cmd = mock.call_args[0][0]
        assert cmd == ["lsblk", "-rno", "NAME,TYPE", f"/dev/disk/by-uuid/{_UUID}"]

    def test_returns_mapper_for_crypt_child(self) -> None:
        # lsblk lists the parent (the LUKS container) and its crypt child.
        stdout = f"sda crypt\nluks-{_UUID} crypt\n"
        # The first crypt row wins; build deterministic output instead.
        stdout = f"sdb part\nluks-{_UUID} crypt\n"
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout=stdout),
        ):
            device = discover_cleartext_device(_local_vol(), _UUID, {})
        assert device == f"/dev/mapper/luks-{_UUID}"

    def test_locked_returns_none(self) -> None:
        # No crypt child: container is still locked.
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="sdb disk\nsdb1 part\n"),
        ):
            assert discover_cleartext_device(_local_vol(), _UUID, {}) is None

    def test_lsblk_failure_returns_none(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert discover_cleartext_device(_local_vol(), _UUID, {}) is None


class TestResolveTargetDevice:
    def test_unencrypted_is_by_uuid(self) -> None:
        device = resolve_target_device(_local_vol(), _mount_config(False), {})
        assert device == f"/dev/disk/by-uuid/{_UUID}"

    def test_encrypted_discovers_cleartext(self) -> None:
        with patch(
            "nbkp.disks.detection.discover_cleartext_device",
            return_value=f"/dev/mapper/luks-{_UUID}",
        ):
            device = resolve_target_device(_local_vol(), _mount_config(True), {})
        assert device == f"/dev/mapper/luks-{_UUID}"

    def test_encrypted_locked_returns_none(self) -> None:
        with patch(
            "nbkp.disks.detection.discover_cleartext_device",
            return_value=None,
        ):
            assert resolve_target_device(_local_vol(), _mount_config(True), {}) is None


class TestFindMountpoint:
    def test_passes_findmnt_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="/mnt/disk\n"),
        ) as mock:
            find_mountpoint(_local_vol(), "/dev/mapper/x", {})
        cmd = mock.call_args[0][0]
        assert cmd == ["findmnt", "--source", "/dev/mapper/x", "-n", "-o", "TARGET"]

    def test_mounted_returns_target(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="/mnt/disk\n"),
        ):
            assert find_mountpoint(_local_vol(), "/dev/mapper/x", {}) == "/mnt/disk"

    def test_unmounted_returns_none(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert find_mountpoint(_local_vol(), "/dev/mapper/x", {}) is None

    def test_empty_output_returns_none(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="\n"),
        ):
            assert find_mountpoint(_local_vol(), "/dev/mapper/x", {}) is None


class TestResolveEffectivePath:
    def test_declared_path_is_authoritative(self) -> None:
        # Option A: volume.path declared — returned directly, no probing.
        with patch("nbkp.disks.detection.resolve_target_device") as mock_resolve:
            path = resolve_effective_path(
                _local_vol("/mnt/disk"), _mount_config(False), {}
            )
        assert path == "/mnt/disk"
        mock_resolve.assert_not_called()

    def test_discovered_path_when_omitted(self) -> None:
        # Option B: no volume.path — discover via findmnt.
        with (
            patch(
                "nbkp.disks.detection.resolve_target_device",
                return_value=f"/dev/disk/by-uuid/{_UUID}",
            ),
            patch(
                "nbkp.disks.detection.find_mountpoint",
                return_value="/run/media/backup/label",
            ),
        ):
            path = resolve_effective_path(_local_vol(None), _mount_config(False), {})
        assert path == "/run/media/backup/label"

    def test_omitted_path_device_locked_returns_none(self) -> None:
        with patch(
            "nbkp.disks.detection.resolve_target_device",
            return_value=None,
        ):
            assert (
                resolve_effective_path(_local_vol(None), _mount_config(True), {})
                is None
            )

    def test_omitted_path_unmounted_returns_none(self) -> None:
        with (
            patch(
                "nbkp.disks.detection.resolve_target_device",
                return_value=f"/dev/disk/by-uuid/{_UUID}",
            ),
            patch("nbkp.disks.detection.find_mountpoint", return_value=None),
        ):
            assert (
                resolve_effective_path(_local_vol(None), _mount_config(False), {})
                is None
            )
