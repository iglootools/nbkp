"""Layer 1 — SSH endpoint: reachability and host tool availability."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, computed_field

from ...disks import (
    MountToolCapabilities,
)


class SshEndpointError(str, enum.Enum):
    """Errors at the SSH endpoint (host) level.

    Host tool availability errors live here because tool presence is a
    property of the host, shared by all volumes on that host.
    """

    # Reachability
    UNREACHABLE = "unreachable"
    LOCATION_EXCLUDED = "excluded by location filter"
    AUTH_FAILED = "ssh authentication or host key verification failed"

    # Always needed
    RSYNC_NOT_FOUND = "rsync not found"
    RSYNC_TOO_OLD = "rsync too old (3.0+ required)"

    # Needed when any endpoint on this host has btrfs snapshots
    BTRFS_NOT_FOUND = "btrfs not found"

    # Needed when any endpoint on this host has btrfs or hardlink snapshots
    STAT_NOT_FOUND = "stat not found"

    # Needed when any endpoint on this host has btrfs snapshots,
    # or any mount-managed volume needs mountpoint discovery
    FINDMNT_NOT_FOUND = "findmnt not found"

    # Needed when any volume on this host has mount config (udisks backend)
    UDISKSCTL_NOT_FOUND = "udisksctl not found"
    UDISKSD_NOT_RUNNING = "udisksd (udisks2 daemon) not running"
    LSBLK_NOT_FOUND = "lsblk not found"


INACTIVE_SSH_ERRORS: frozenset[SshEndpointError] = frozenset(
    {
        SshEndpointError.UNREACHABLE,
        SshEndpointError.LOCATION_EXCLUDED,
    }
)


class SshEndpointWarning(str, enum.Enum):
    """Host-level findings worth fixing that do not make the endpoint inactive.

    Kept apart from ``SshEndpointError`` so they never reach ``active`` or
    the strictness policy: ``check`` and ``troubleshoot`` display them, and
    nothing else reads them.
    """

    # Btrfs-backed mount volumes: udisks mounts btrfs without its btrfs
    # module, but cannot manage btrfs-specific features.
    UDISKS_BTRFS_MODULE_MISSING = "udisks2 btrfs module not installed"


@dataclass(frozen=True)
class SshEndpointToolNeeds:
    """What tools are required on this SSH endpoint, derived from config.

    Computed by ``checks.py`` by scanning volumes and endpoints on this host.
    """

    has_btrfs_endpoints: bool = False
    """Any sync endpoint on a volume using this host has btrfs snapshots."""
    has_snapshot_endpoints: bool = False
    """Any sync endpoint on a volume using this host has btrfs or hardlink
    snapshots (stat is needed for both)."""
    has_mount_volumes: bool = False
    """Any volume on this host has mount config (needs udisks tools)."""
    has_btrfs_mount: bool = False
    """Any mount-managed volume on this host is on btrfs (needs the udisks
    btrfs module)."""


class HostToolCapabilities(BaseModel):
    """Host-level tool availability, probed once per SSH endpoint."""

    model_config = ConfigDict(frozen=True)

    has_rsync: bool
    rsync_version_ok: bool
    has_btrfs: bool
    has_stat: bool
    has_findmnt: bool


class SshEndpointDiagnostics(BaseModel):
    """Observed state of an SSH endpoint (or implicit localhost).

    Pure diagnostics — no interpretation of what constitutes an error.
    ``SshEndpointStatus.from_diagnostics`` translates these into
    ``SshEndpointError`` values using ``SshEndpointToolNeeds``.
    """

    model_config = ConfigDict(frozen=True)

    location_excluded: bool = False
    """Whether all SSH endpoints for this volume were excluded by location
    filter.  Only meaningful for remote volumes."""
    ssh_reachable: bool | None = None
    """Whether the SSH endpoint is reachable.
    ``None`` for implicit localhost (always reachable)."""
    ssh_auth_failed: bool = False
    """The host answered but refused authentication or failed host key
    verification.  Only meaningful when ``ssh_reachable`` is ``False``."""
    ssh_error: str | None = None
    """First line of the connection error, when the connection failed."""
    host_tools: HostToolCapabilities | None = None
    """Host-level tool availability.
    ``None`` when the host is unreachable or excluded."""
    mount_tools: MountToolCapabilities | None = None
    """Mount management tool availability.
    ``None`` when no volumes on this host have mount config,
    or when the host is unreachable."""


_NO_TOOL_NEEDS = SshEndpointToolNeeds()
"""Shared default for endpoints with no tool requirements.
Safe to share because ``SshEndpointToolNeeds`` is frozen."""


class SshEndpointStatus(BaseModel):
    """Runtime status of an SSH endpoint (or implicit localhost)."""

    model_config = ConfigDict(frozen=True)

    slug: str
    """SSH endpoint slug, or ``\"localhost\"`` for local volumes."""
    diagnostics: SshEndpointDiagnostics
    errors: list[SshEndpointError]
    warnings: list[SshEndpointWarning] = []
    """Non-fatal findings; never affect ``active``."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return not self.errors

    @staticmethod
    def from_diagnostics(
        slug: str,
        diagnostics: SshEndpointDiagnostics,
        needs: SshEndpointToolNeeds = _NO_TOOL_NEEDS,
    ) -> SshEndpointStatus:
        """Create status by interpreting diagnostics into errors."""
        return SshEndpointStatus(
            slug=slug,
            diagnostics=diagnostics,
            errors=_ssh_endpoint_errors(diagnostics, needs),
            warnings=_ssh_endpoint_warnings(diagnostics, needs),
        )


# ── Layer 1 error interpretation ───────────────────────────


def _ssh_endpoint_errors(
    diag: SshEndpointDiagnostics,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointError]:
    """Translate SSH endpoint diagnostics into errors."""
    match diag:
        case SshEndpointDiagnostics(location_excluded=True):
            return [SshEndpointError.LOCATION_EXCLUDED]
        case SshEndpointDiagnostics(ssh_reachable=False, ssh_auth_failed=True):
            return [SshEndpointError.AUTH_FAILED]
        case SshEndpointDiagnostics(ssh_reachable=False):
            return [SshEndpointError.UNREACHABLE]
        case SshEndpointDiagnostics(host_tools=HostToolCapabilities() as tools):
            # findmnt is needed both by btrfs endpoints and by mount
            # management: report it once when both need it.
            return list(
                dict.fromkeys(
                    [
                        *_ssh_rsync_errors(tools),
                        *_ssh_snapshot_tool_errors(tools, needs),
                        *(
                            _ssh_mount_tool_errors(diag.mount_tools, needs)
                            if diag.mount_tools is not None
                            else []
                        ),
                    ]
                )
            )
        case _:
            return []


def _ssh_endpoint_warnings(
    diag: SshEndpointDiagnostics,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointWarning]:
    """Non-fatal host findings (only for a probed host)."""
    return (
        [SshEndpointWarning.UDISKS_BTRFS_MODULE_MISSING]
        if diag.mount_tools is not None
        and needs.has_mount_volumes
        and needs.has_btrfs_mount
        and diag.mount_tools.has_btrfs_module is False
        else []
    )


def _ssh_rsync_errors(tools: HostToolCapabilities) -> list[SshEndpointError]:
    """Rsync availability errors (always needed)."""
    match (tools.has_rsync, tools.rsync_version_ok):
        case (False, _):
            return [SshEndpointError.RSYNC_NOT_FOUND]
        case (True, False):
            return [SshEndpointError.RSYNC_TOO_OLD]
        case _:
            return []


def _ssh_snapshot_tool_errors(
    tools: HostToolCapabilities,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointError]:
    """Snapshot-related tool errors (btrfs, stat, findmnt)."""
    return [
        *(
            [SshEndpointError.BTRFS_NOT_FOUND]
            if needs.has_btrfs_endpoints and not tools.has_btrfs
            else []
        ),
        *(
            [SshEndpointError.STAT_NOT_FOUND]
            if needs.has_snapshot_endpoints and not tools.has_stat
            else []
        ),
        *(
            [SshEndpointError.FINDMNT_NOT_FOUND]
            if needs.has_btrfs_endpoints and not tools.has_findmnt
            else []
        ),
    ]


def _ssh_mount_tool_errors(
    mount_tools: MountToolCapabilities,
    needs: SshEndpointToolNeeds,
) -> list[SshEndpointError]:
    """udisks mount management tool errors.

    A mount-managed host needs ``udisksctl`` + a running ``udisksd`` (for
    unlock/mount/lock), ``findmnt`` (mountpoint discovery), and ``lsblk``
    (cleartext-device discovery).  The udisks btrfs module that btrfs-backed
    mount volumes want is a warning (see ``_ssh_endpoint_warnings``).
    """
    if not needs.has_mount_volumes:
        return []
    return [
        *(
            [SshEndpointError.UDISKSCTL_NOT_FOUND]
            if mount_tools.has_udisksctl is False
            else []
        ),
        *(
            [SshEndpointError.UDISKSD_NOT_RUNNING]
            if mount_tools.has_udisksctl is True
            and mount_tools.udisksd_running is False
            else []
        ),
        *(
            [SshEndpointError.FINDMNT_NOT_FOUND]
            if mount_tools.has_findmnt is False
            else []
        ),
        # lsblk is only *used* for encrypted volumes today — discovering the
        # unlocked cleartext mapper (see disks.detection.discover_cleartext_device).
        # We deliberately gate it on has_mount_volumes (any mount-managed volume)
        # rather than encrypted-only: lsblk is core util-linux and present
        # wherever udisks is, so the broader check never false-alarms, and it
        # leaves room for plausible future uses across all mount-managed volumes —
        # pre-mount introspection (lsblk -o FSTYPE/SIZE/MODEL), label/partition
        # resolution, multi-device btrfs enumeration, or a richer `disks status`
        # block-device tree.
        *([SshEndpointError.LSBLK_NOT_FOUND] if mount_tools.has_lsblk is False else []),
    ]
