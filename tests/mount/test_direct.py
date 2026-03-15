"""Tests for nbkp.mount.direct command builders."""

from __future__ import annotations

from nbkp.mount.direct import (
    build_close_luks_command,
    build_mount_command,
    build_attach_luks_command,
    build_umount_command,
)


class TestBuildAttachLuksCommand:
    def test_basic(self) -> None:
        cmd = build_attach_luks_command(
            mapper_name="seagate8tb",
            device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
        )
        assert cmd == [
            "sudo",
            "cryptsetup",
            "open",
            "--type",
            "luks",
            "/dev/disk/by-uuid/5941f273-f73c-44c5-a3ef-fae7248db1b6",
            "seagate8tb",
            "-",
        ]

    def test_mapper_name_with_hyphens(self) -> None:
        cmd = build_attach_luks_command(
            mapper_name="my-disk-1",
            device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        assert cmd[6] == "my-disk-1"


class TestBuildCloseLuksCommand:
    def test_basic(self) -> None:
        cmd = build_close_luks_command("seagate8tb")
        assert cmd == ["sudo", "cryptsetup", "close", "seagate8tb"]


class TestBuildMountCommand:
    def test_basic(self) -> None:
        cmd = build_mount_command("/mnt/backup")
        assert cmd == ["sudo", "mount", "/mnt/backup"]


class TestBuildUmountCommand:
    def test_basic(self) -> None:
        cmd = build_umount_command("/mnt/backup")
        assert cmd == ["sudo", "umount", "/mnt/backup"]
