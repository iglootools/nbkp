"""Tests for nbkp.disks.strategy implementations."""

from __future__ import annotations

import pytest

from nbkp.disks.strategy import DirectMountStrategy, SystemdMountStrategy


class TestSystemdMountStrategy:
    def test_build_attach_luks_command(self) -> None:
        backend = SystemdMountStrategy(
            mount_unit="mnt-backup.mount",
            cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
        )
        cmd = backend.build_attach_luks_command("mapper1", "uuid-1234")
        assert cmd[0] == "sudo"
        assert cmd[1] == "/usr/lib/systemd/systemd-cryptsetup"
        assert "attach" in cmd
        assert "mapper1" in cmd

    def test_build_attach_luks_command_no_cryptsetup_path(self) -> None:
        backend = SystemdMountStrategy(mount_unit="mnt-backup.mount")
        with pytest.raises(ValueError, match="cryptsetup path"):
            backend.build_attach_luks_command("mapper1", "uuid-1234")

    def test_build_close_luks_command(self) -> None:
        backend = SystemdMountStrategy(mount_unit="mnt-backup.mount")
        cmd = backend.build_close_luks_command("mapper1")
        assert cmd == ["systemctl", "stop", "systemd-cryptsetup@mapper1.service"]

    def test_build_mount_command(self) -> None:
        backend = SystemdMountStrategy(mount_unit="mnt-backup.mount")
        cmd = backend.build_mount_command()
        assert cmd == ["systemctl", "start", "mnt-backup.mount"]

    def test_build_umount_command(self) -> None:
        backend = SystemdMountStrategy(mount_unit="mnt-backup.mount")
        cmd = backend.build_umount_command()
        assert cmd == ["systemctl", "stop", "mnt-backup.mount"]

    def test_frozen(self) -> None:
        backend = SystemdMountStrategy(mount_unit="mnt-backup.mount")
        with pytest.raises(AttributeError):
            backend.mount_unit = "other"  # type: ignore[misc]


class TestDirectMountStrategy:
    def test_build_attach_luks_command(self) -> None:
        backend = DirectMountStrategy(volume_path="/mnt/backup")
        cmd = backend.build_attach_luks_command("mapper1", "uuid-1234")
        assert cmd[0] == "sudo"
        assert "cryptsetup" in cmd
        assert "open" in cmd

    def test_build_close_luks_command(self) -> None:
        backend = DirectMountStrategy(volume_path="/mnt/backup")
        cmd = backend.build_close_luks_command("mapper1")
        assert cmd == ["sudo", "cryptsetup", "close", "mapper1"]

    def test_build_mount_command(self) -> None:
        backend = DirectMountStrategy(volume_path="/mnt/backup")
        cmd = backend.build_mount_command()
        assert cmd == ["sudo", "mount", "/mnt/backup"]

    def test_build_umount_command(self) -> None:
        backend = DirectMountStrategy(volume_path="/mnt/backup")
        cmd = backend.build_umount_command()
        assert cmd == ["sudo", "umount", "/mnt/backup"]

    def test_frozen(self) -> None:
        backend = DirectMountStrategy(volume_path="/mnt/backup")
        with pytest.raises(AttributeError):
            backend.volume_path = "/other"  # type: ignore[misc]
