"""Mount-related data models: tool capabilities and mount state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class MountToolCapabilities(BaseModel):
    """Mount management tool availability on a host.

    Probed once per SSH endpoint when any volume on that host has
    mount config.  Tool *availability* is host-level; tool *need*
    depends on volume config and is evaluated during error interpretation.
    """

    model_config = ConfigDict(frozen=True)

    # Systemd tools
    has_systemctl: bool | None = None
    has_systemd_escape: bool | None = None
    has_systemd_cryptsetup: bool | None = None
    systemd_cryptsetup_path: str | None = None

    # Shared (both backends)
    has_sudo: bool | None = None
    has_cryptsetup: bool | None = None

    # Direct tools
    has_mount_cmd: bool | None = None
    has_umount_cmd: bool | None = None
    has_mountpoint: bool | None = None


class MountCapabilities(BaseModel):
    """Volume-specific mount diagnostics (config checks + runtime state).

    Host-level tool availability (sudo, cryptsetup, systemctl, etc.)
    lives on ``MountToolCapabilities`` at the SSH endpoint level.
    This model captures only volume-specific config validation and
    runtime mount state.
    """

    model_config = ConfigDict(frozen=True)

    resolved_backend: Literal["systemd", "direct"] | None = None
    """Which backend was resolved (``None`` when auto-detection was not
    performed)."""

    # Systemd config checks (None when direct backend)
    mount_unit: str | None = None
    has_mount_unit_config: bool | None = None
    mount_unit_what: str | None = None
    mount_unit_where: str | None = None
    has_cryptsetup_service_config: bool | None = None
    cryptsetup_service_exec_start: str | None = None

    # Auth rules (checked per-volume path)
    has_polkit_rules: bool | None = None
    has_sudoers_rules: bool | None = None

    # Runtime mount state (probed during observation)
    device_present: bool | None = None
    luks_attached: bool | None = None
    mounted: bool | None = None
