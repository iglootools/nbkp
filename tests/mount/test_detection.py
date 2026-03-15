"""Tests for nbkp.mount.detection."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from nbkp.config import LocalVolume
from nbkp.mount.detection import (
    detect_device_present,
    detect_luks_attached,
    detect_systemd_cryptsetup_path,
    detect_volume_mounted,
    resolve_mount_unit,
)


def _local_vol(path: str = "/mnt/disk") -> LocalVolume:
    return LocalVolume(slug="disk", path=path)


def _mock_run(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestDetectDevicePresent:
    def test_present(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)):
            assert detect_device_present(
                _local_vol(), "5941f273-f73c-44c5-a3ef-fae7248db1b6", {}
            )

    def test_not_present(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert not detect_device_present(
                _local_vol(), "5941f273-f73c-44c5-a3ef-fae7248db1b6", {}
            )

    def test_passes_correct_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)
        ) as mock:
            detect_device_present(_local_vol(), "aaaa-bbbb-cccc-dddd", {})
        mock.assert_called_once()
        cmd = mock.call_args[0][0]
        assert cmd == ["test", "-e", "/dev/disk/by-uuid/aaaa-bbbb-cccc-dddd"]


class TestDetectLuksAttached:
    def test_attached(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)):
            assert detect_luks_attached(_local_vol(), "seagate8tb", {})

    def test_not_attached(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            assert not detect_luks_attached(_local_vol(), "seagate8tb", {})

    def test_passes_correct_command(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)
        ) as mock:
            detect_luks_attached(_local_vol(), "mydisk", {})
        cmd = mock.call_args[0][0]
        assert cmd == ["test", "-b", "/dev/mapper/mydisk"]


class TestDetectVolumeMounted:
    def test_mounted(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(0)):
            assert detect_volume_mounted(_local_vol(), "mnt-disk.mount", {})

    def test_not_mounted(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(3)):
            assert not detect_volume_mounted(_local_vol(), "mnt-disk.mount", {})


class TestDetectSystemdCryptsetupPath:
    def test_found_usr_lib(self) -> None:
        def mock_run(cmd, **kwargs):
            if cmd[1] == "-x" and "/usr/lib/" in cmd[2]:
                return _mock_run(0)
            return _mock_run(1)

        with patch("nbkp.remote.dispatch.subprocess.run", side_effect=mock_run):
            path = detect_systemd_cryptsetup_path(_local_vol(), {})
        assert path == "/usr/lib/systemd/systemd-cryptsetup"

    def test_found_lib(self) -> None:
        def mock_run(cmd, **kwargs):
            if cmd[1] == "-x" and cmd[2] == "/lib/systemd/systemd-cryptsetup":
                return _mock_run(0)
            return _mock_run(1)

        with patch("nbkp.remote.dispatch.subprocess.run", side_effect=mock_run):
            path = detect_systemd_cryptsetup_path(_local_vol(), {})
        assert path == "/lib/systemd/systemd-cryptsetup"

    def test_not_found(self) -> None:
        with patch("nbkp.remote.dispatch.subprocess.run", return_value=_mock_run(1)):
            path = detect_systemd_cryptsetup_path(_local_vol(), {})
        assert path is None


class TestResolveMountUnit:
    def test_success(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="mnt-seagate8tb\n"),
        ):
            unit = resolve_mount_unit(_local_vol("/mnt/seagate8tb"), {})
        assert unit == "mnt-seagate8tb.mount"

    def test_failure(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(1),
        ):
            unit = resolve_mount_unit(_local_vol("/mnt/seagate8tb"), {})
        assert unit is None

    def test_passes_volume_path(self) -> None:
        with patch(
            "nbkp.remote.dispatch.subprocess.run",
            return_value=_mock_run(0, stdout="mnt-data\n"),
        ) as mock:
            resolve_mount_unit(_local_vol("/mnt/data"), {})
        cmd = mock.call_args[0][0]
        assert cmd == ["systemd-escape", "--path", "/mnt/data"]
