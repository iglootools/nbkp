"""Tests for nbkp.disks.udisks command builders."""

from __future__ import annotations

from nbkp.disks.udisks import (
    build_lock_command,
    build_mount_command,
    build_unlock_command,
    build_unmount_command,
    cleartext_mapper_name,
)

_UUID = "5941F273-F73C-44C5-A3EF-FAE7248DB1B6"
_UUID_LOWER = "5941f273-f73c-44c5-a3ef-fae7248db1b6"


class TestCleartextMapperName:
    def test_lowercases_uuid(self) -> None:
        assert cleartext_mapper_name(_UUID) == f"luks-{_UUID_LOWER}"

    def test_already_lowercase(self) -> None:
        assert cleartext_mapper_name(_UUID_LOWER) == f"luks-{_UUID_LOWER}"


class TestBuildUnlockCommand:
    def test_command(self) -> None:
        assert build_unlock_command(_UUID_LOWER) == [
            "udisksctl",
            "unlock",
            "-b",
            f"/dev/disk/by-uuid/{_UUID_LOWER}",
            "--key-file",
            "/dev/stdin",
            "--no-user-interaction",
        ]

    def test_reads_passphrase_from_stdin(self) -> None:
        cmd = build_unlock_command(_UUID_LOWER)
        # The passphrase is piped via stdin, addressed by --key-file /dev/stdin.
        assert "--key-file" in cmd
        idx = cmd.index("--key-file")
        assert cmd[idx + 1] == "/dev/stdin"

    def test_non_interactive(self) -> None:
        assert "--no-user-interaction" in build_unlock_command(_UUID_LOWER)


class TestBuildLockCommand:
    def test_command(self) -> None:
        assert build_lock_command(_UUID_LOWER) == [
            "udisksctl",
            "lock",
            "-b",
            f"/dev/disk/by-uuid/{_UUID_LOWER}",
            "--no-user-interaction",
        ]


class TestBuildMountCommand:
    def test_cleartext_device(self) -> None:
        assert build_mount_command(f"/dev/mapper/luks-{_UUID_LOWER}") == [
            "udisksctl",
            "mount",
            "-b",
            f"/dev/mapper/luks-{_UUID_LOWER}",
            "--no-user-interaction",
        ]

    def test_uuid_device(self) -> None:
        assert build_mount_command(f"/dev/disk/by-uuid/{_UUID_LOWER}") == [
            "udisksctl",
            "mount",
            "-b",
            f"/dev/disk/by-uuid/{_UUID_LOWER}",
            "--no-user-interaction",
        ]


class TestBuildUnmountCommand:
    def test_command(self) -> None:
        assert build_unmount_command(f"/dev/mapper/luks-{_UUID_LOWER}") == [
            "udisksctl",
            "unmount",
            "-b",
            f"/dev/mapper/luks-{_UUID_LOWER}",
            "--no-user-interaction",
        ]
