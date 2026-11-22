"""Statuses shared by the check and troubleshoot demo data."""

from ...fsprotocol import Snapshot
from .. import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    HostToolCapabilities,
    LatestSymlinkState,
    SnapshotDirsDiagnostics,
    SourceEndpointDiagnostics,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    SyncStatus,
    VolumeCapabilities,
    VolumeStatus,
)


def demo_ssh_statuses(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
) -> dict[str, SshEndpointStatus]:
    """Every distinct SSH endpoint status embedded in the demo statuses.

    Production has one status per endpoint, but the demo reuses ``localhost``
    and ``nas`` across scenarios with different tool errors.  Keying by slug
    alone would keep only one of them and hide the others' remediation, so
    later variants of a slug get a numbered key (``localhost #2``).
    """
    embedded = [
        *(vs.ssh_endpoint_status for vs in vol_statuses.values()),
        *(
            ep.volume_status.ssh_endpoint_status
            for ss in sync_statuses.values()
            for ep in (ss.source_endpoint_status, ss.destination_endpoint_status)
        ),
    ]
    distinct = list({s.model_dump_json(): s for s in embedded}.values())
    return {
        key: status.model_copy(update={"slug": key})
        for i, status in enumerate(distinct)
        for n in [sum(1 for other in distinct[:i] if other.slug == status.slug)]
        for key in [status.slug if n == 0 else f"{status.slug} #{n + 1}"]
    }


LOCALHOST_SSH_STATUS = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=False,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)

LOCALHOST_SSH_STATUS_BTRFS = SshEndpointStatus.from_diagnostics(
    slug="localhost",
    diagnostics=SshEndpointDiagnostics(
        host_tools=HostToolCapabilities(
            has_rsync=True,
            rsync_version_ok=True,
            has_btrfs=True,
            has_stat=True,
            has_findmnt=True,
        ),
    ),
)


def standalone_ssh_status(slug: str, *, reachable: bool = True) -> SshEndpointStatus:
    return SshEndpointStatus.from_diagnostics(
        slug=slug,
        diagnostics=SshEndpointDiagnostics(ssh_reachable=reachable),
    )


SENTINEL_MISSING_CAPS = VolumeCapabilities(
    sentinel_exists=False,
    is_btrfs_filesystem=False,
    hardlink_supported=True,
    btrfs_user_subvol_rm=False,
)


def active_src_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
    *,
    snapshot_mode: bool = False,
) -> SourceEndpointStatus:
    return SourceEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=endpoint_slug,
            sentinel_exists=True,
            snapshot_dirs=(
                SnapshotDirsDiagnostics(exists=True, writable=True)
                if snapshot_mode
                else None
            ),
            latest=(
                LatestSymlinkState(
                    exists=True,
                    raw_target="snapshots/2026-03-06T14:30:00.000Z",
                    target_valid=True,
                    snapshot=Snapshot.from_name("2026-03-06T14:30:00.000Z"),
                )
                if snapshot_mode
                else None
            ),
        ),
        errors=[],
    )


def active_dst_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
    diagnostics: DestinationEndpointDiagnostics | None = None,
) -> DestinationEndpointStatus:
    diag = diagnostics or DestinationEndpointDiagnostics(
        endpoint_slug=endpoint_slug,
        sentinel_exists=True,
        endpoint_writable=True,
    )
    return DestinationEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=diag,
        errors=[],
    )


def inactive_dst_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
) -> DestinationEndpointStatus:
    return DestinationEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=None,
        errors=(
            [DestinationEndpointErrorKind.VOLUME_INACTIVE]
            if not volume_status.active
            else []
        ),
    )


def inactive_src_ep_status(
    endpoint_slug: str,
    volume_status: VolumeStatus,
) -> SourceEndpointStatus:
    return SourceEndpointStatus(
        endpoint_slug=endpoint_slug,
        volume_status=volume_status,
        diagnostics=None,
        errors=(
            [SourceEndpointErrorKind.VOLUME_INACTIVE]
            if not volume_status.active
            else []
        ),
    )
