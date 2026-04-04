"""Tests for nbkp.disks.systemd command builders."""

from __future__ import annotations

from nbkp.disks.systemd import (
    build_close_luks_command,
    build_mount_command,
    build_attach_luks_command,
    build_umount_command,
)


class TestBuildAttachLuksCommand:
    def test_basic(self) -> None:
        cmd = build_attach_luks_command(
            cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
            mapper_name="seagate8tb",
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
        )
        assert cmd == [
            "sudo",
            "/usr/lib/systemd/systemd-cryptsetup",
            "attach",
            "seagate8tb",
            "/dev/disk/by-uuid/5941f273-f73c-44c5-a3ef-fae7248db1b6",
            "/dev/stdin",
            "luks",
        ]

    def test_different_cryptsetup_path(self) -> None:
        cmd = build_attach_luks_command(
            cryptsetup_path="/lib/systemd/systemd-cryptsetup",
            mapper_name="disk1",
            device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        assert cmd[1] == "/lib/systemd/systemd-cryptsetup"
        assert cmd[3] == "disk1"

    def test_mapper_name_with_hyphens(self) -> None:
        cmd = build_attach_luks_command(
            cryptsetup_path="/usr/lib/systemd/systemd-cryptsetup",
            mapper_name="my-disk-1",
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
        )
        assert cmd[3] == "my-disk-1"


class TestBuildMountCommand:
    def test_basic(self) -> None:
        cmd = build_mount_command("mnt-seagate8tb.mount")
        assert cmd == ["systemctl", "start", "mnt-seagate8tb.mount"]


class TestBuildUmountCommand:
    def test_basic(self) -> None:
        cmd = build_umount_command("mnt-seagate8tb.mount")
        assert cmd == ["systemctl", "stop", "mnt-seagate8tb.mount"]


class TestBuildCloseLuksCommand:
    def test_basic(self) -> None:
        cmd = build_close_luks_command("seagate8tb")
        assert cmd == [
            "systemctl",
            "stop",
            "systemd-cryptsetup@seagate8tb.service",
        ]

    def test_mapper_name_with_hyphens(self) -> None:
        cmd = build_close_luks_command("my-disk-1")
        assert cmd == [
            "systemctl",
            "stop",
            "systemd-cryptsetup@my-disk-1.service",
        ]
