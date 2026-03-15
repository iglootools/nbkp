"""Mount backend strategy: Protocol + systemd/direct implementations.

A ``MountStrategy`` encapsulates the concrete commands for attach-luks/close-luks/
mount/umount and mounted-state detection. Two frozen-dataclass
implementations hold resolved infrastructure and delegate to the pure
command builders in ``systemd.py`` and ``direct.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import ResolvedEndpoints, Volume
from ..remote.dispatch import run_on_volume
from . import direct as direct_cmds
from . import systemd as systemd_cmds


class MountStrategy(Protocol):
    """Strategy interface for mount operations."""

    def build_attach_luks_command(
        self, mapper_name: str, device_uuid: str
    ) -> list[str]: ...

    def build_close_luks_command(self, mapper_name: str) -> list[str]: ...

    def build_mount_command(self) -> list[str]: ...

    def build_umount_command(self) -> list[str]: ...

    def detect_mounted(
        self,
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
    ) -> bool: ...


@dataclass(frozen=True)
class SystemdMountStrategy:
    """Mounts via systemctl, unlocks via systemd-cryptsetup."""

    mount_unit: str
    cryptsetup_path: str | None = None

    def build_attach_luks_command(
        self, mapper_name: str, device_uuid: str
    ) -> list[str]:
        if self.cryptsetup_path is None:
            msg = "systemd-cryptsetup path not resolved"
            raise ValueError(msg)
        return systemd_cmds.build_attach_luks_command(
            self.cryptsetup_path, mapper_name, device_uuid
        )

    def build_close_luks_command(self, mapper_name: str) -> list[str]:
        return systemd_cmds.build_close_luks_command(mapper_name)

    def build_mount_command(self) -> list[str]:
        return systemd_cmds.build_mount_command(self.mount_unit)

    def build_umount_command(self) -> list[str]:
        return systemd_cmds.build_umount_command(self.mount_unit)

    def detect_mounted(
        self,
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
    ) -> bool:
        result = run_on_volume(
            ["systemctl", "is-active", self.mount_unit, "--quiet"],
            volume,
            resolved_endpoints,
        )
        return result.returncode == 0


@dataclass(frozen=True)
class DirectMountStrategy:
    """Mounts via sudo mount/umount, unlocks via sudo cryptsetup.

    The device and mount options come from fstab (mirroring how the
    systemd strategy reads options from the unit file).
    """

    volume_path: str

    def build_attach_luks_command(
        self, mapper_name: str, device_uuid: str
    ) -> list[str]:
        return direct_cmds.build_attach_luks_command(mapper_name, device_uuid)

    def build_close_luks_command(self, mapper_name: str) -> list[str]:
        return direct_cmds.build_close_luks_command(mapper_name)

    def build_mount_command(self) -> list[str]:
        return direct_cmds.build_mount_command(self.volume_path)

    def build_umount_command(self) -> list[str]:
        return direct_cmds.build_umount_command(self.volume_path)

    def detect_mounted(
        self,
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
    ) -> bool:
        result = run_on_volume(
            ["mountpoint", "-q", self.volume_path],
            volume,
            resolved_endpoints,
        )
        return result.returncode == 0
