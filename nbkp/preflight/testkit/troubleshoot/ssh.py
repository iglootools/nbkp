"""Host tool sets the scenarios' SSH endpoint statuses are built from."""

from nbkp.disks import MountToolCapabilities
from nbkp.preflight import (
    HostToolCapabilities,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SshEndpointToolNeeds,
)

HOST_TOOLS = HostToolCapabilities(
    has_rsync=True,
    rsync_version_ok=True,
    has_btrfs=False,
    has_stat=True,
    has_findmnt=True,
)
BTRFS_HOST_TOOLS = HOST_TOOLS.model_copy(update={"has_btrfs": True})

UDISKS_TOOLS = MountToolCapabilities(
    has_udisksctl=True,
    udisksd_running=True,
    has_findmnt=True,
    has_lsblk=True,
)

_NO_NEEDS = SshEndpointToolNeeds()
MOUNT_NEEDS = SshEndpointToolNeeds(has_mount_volumes=True)
BTRFS_SNAPSHOT_NEEDS = SshEndpointToolNeeds(
    has_btrfs_endpoints=True,
    has_snapshot_endpoints=True,
)


def localhost_status(
    *,
    host_tools: HostToolCapabilities = HOST_TOOLS,
    mount_tools: MountToolCapabilities | None = None,
    needs: SshEndpointToolNeeds = _NO_NEEDS,
) -> SshEndpointStatus:
    """Return the ``localhost`` endpoint status, with the given tools and *needs*."""
    return SshEndpointStatus.from_diagnostics(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(
            host_tools=host_tools,
            mount_tools=mount_tools,
        ),
        needs=needs,
    )


def nas_status(host_tools: HostToolCapabilities, **needs: bool) -> SshEndpointStatus:
    """Return the reachable ``nas`` endpoint, with *host_tools* installed."""
    return SshEndpointStatus.from_diagnostics(
        slug="nas",
        diagnostics=SshEndpointDiagnostics(ssh_reachable=True, host_tools=host_tools),
        needs=SshEndpointToolNeeds(**needs),
    )
