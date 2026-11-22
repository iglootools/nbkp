"""The shape of a troubleshoot scenario, and the statuses scenarios share."""

from collections.abc import Callable
from dataclasses import dataclass

from nbkp.config import Config, SshEndpoint, SyncConfig, SyncEndpoint, Volume
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SyncErrorKind,
    SyncStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from nbkp.preflight.testkit.common import (
    LOCALHOST_SSH_STATUS,
    LOCALHOST_SSH_STATUS_BTRFS,
    SENTINEL_MISSING_CAPS,
    inactive_src_ep_status,
)
from nbkp.preflight.testkit.troubleshoot.ssh import HOST_TOOLS, nas_status

#: Source endpoint most scenarios read from, on the shared ``laptop`` volume.
LAPTOP_SRC = "laptop-src"

BOTH_ENDPOINTS_INACTIVE = (
    SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
    SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
)


@dataclass(frozen=True)
class SharedStatuses:
    """Volume and endpoint statuses several scenarios point at."""

    laptop: VolumeStatus
    """Volume sentinel missing, so most scenarios' source is inactive."""
    laptop_active: VolumeStatus
    laptop_src_inactive: SourceEndpointStatus
    usb_drive: VolumeStatus
    nas_unreachable: VolumeStatus
    nas_reachable: VolumeStatus

    @property
    def reported_volumes(self) -> tuple[VolumeStatus, ...]:
        """Return the shared volume statuses listed in the result, whatever runs."""
        return (self.laptop, self.usb_drive, self.nas_unreachable)


@dataclass(frozen=True)
class ScenarioStatuses:
    """The statuses one scenario contributes to the troubleshoot result."""

    syncs: tuple[SyncStatus, ...] = ()
    volumes: tuple[VolumeStatus, ...] = ()
    """Volume statuses listed in the result, keyed by slug."""
    standalone_ssh_endpoints: tuple[SshEndpointStatus, ...] = ()
    """Endpoints no volume uses; the others are collected from the statuses."""


@dataclass(frozen=True)
class Scenario:
    """
    Config entries a scenario adds, and the statuses that exercise them.

    The statuses are built by hand rather than by ``from_diagnostics``, so a
    scenario can show errors production would cascade away, and says so
    where it does.
    """

    statuses: Callable[[Config, SharedStatuses], ScenarioStatuses]
    syncs: tuple[SyncConfig, ...] = ()
    sync_endpoints: tuple[SyncEndpoint, ...] = ()
    volumes: tuple[Volume, ...] = ()
    ssh_endpoints: tuple[SshEndpoint, ...] = ()


def sync_from_laptop(slug: str, destination: str) -> SyncConfig:
    """Return the sync *slug* from the shared laptop source to *destination*."""
    return SyncConfig(slug=slug, source=LAPTOP_SRC, destination=destination)


def healthy_caps(
    *,
    btrfs: bool | None = False,
    hardlinks: bool = True,
    subvol_rm: bool = False,
) -> VolumeCapabilities:
    """Capabilities of a mounted volume whose sentinel is present."""
    return VolumeCapabilities(
        sentinel_exists=True,
        is_btrfs_filesystem=btrfs,
        hardlink_supported=hardlinks,
        btrfs_user_subvol_rm=subvol_rm,
    )


def volume_status(
    config: Config,
    slug: str,
    ssh_status: SshEndpointStatus,
    capabilities: VolumeCapabilities,
    errors: tuple[VolumeErrorKind, ...] = (),
) -> VolumeStatus:
    """Return the status of *config*'s volume *slug*, with *errors* as given."""
    return VolumeStatus(
        slug=slug,
        config=config.volumes[slug],
        ssh_endpoint_status=ssh_status,
        diagnostics=VolumeDiagnostics(capabilities=capabilities),
        errors=list(errors),
    )


def broken_destination(
    config: Config,
    shared: SharedStatuses,
    sync_slug: str,
    volume: VolumeStatus,
    diagnostics: DestinationEndpointDiagnostics,
    *,
    errors: list[DestinationEndpointErrorKind],
) -> SyncStatus:
    """Build a sync from the inactive laptop whose destination has *errors*."""
    return SyncStatus(
        slug=sync_slug,
        config=config.syncs[sync_slug],
        source_endpoint_status=shared.laptop_src_inactive,
        destination_endpoint_status=DestinationEndpointStatus(
            endpoint_slug=diagnostics.endpoint_slug,
            volume_status=volume,
            diagnostics=diagnostics,
            errors=errors,
        ),
        errors=list(BOTH_ENDPOINTS_INACTIVE),
    )


# NAS SSH endpoint: unreachable
_NAS_UNREACHABLE_SSH = SshEndpointStatus.from_diagnostics(
    slug="nas",
    diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
)

# NAS SSH endpoint for src-latest scenario (reachable but volume inactive)
_NAS_REACHABLE_SSH = nas_status(HOST_TOOLS)


def shared_statuses(config: Config) -> SharedStatuses:
    """Return the statuses scenarios share, for *config*'s shared volumes."""
    laptop = volume_status(
        config,
        "laptop",
        LOCALHOST_SSH_STATUS,
        SENTINEL_MISSING_CAPS,
        (VolumeErrorKind.SENTINEL_NOT_FOUND,),
    )
    return SharedStatuses(
        laptop=laptop,
        laptop_active=volume_status(
            config,
            "laptop",
            LOCALHOST_SSH_STATUS,
            healthy_caps(),
        ),
        laptop_src_inactive=inactive_src_ep_status(LAPTOP_SRC, laptop),
        usb_drive=VolumeStatus(
            slug="usb-drive",
            config=config.volumes["usb-drive"],
            ssh_endpoint_status=LOCALHOST_SSH_STATUS_BTRFS,
            diagnostics=VolumeDiagnostics(),
            errors=[],
        ),
        # SSH unreachable (Layer 1 error → cascade to Layer 2)
        nas_unreachable=VolumeStatus(
            slug="nas-backup",
            config=config.volumes["nas-backup"],
            ssh_endpoint_status=_NAS_UNREACHABLE_SSH,
            diagnostics=None,
            errors=[VolumeErrorKind.SSH_ENDPOINT_INACTIVE],
        ),
        nas_reachable=volume_status(
            config,
            "nas-backup",
            _NAS_REACHABLE_SSH,
            healthy_caps(),
        ),
    )
