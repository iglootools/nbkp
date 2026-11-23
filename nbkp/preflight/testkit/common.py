"""Statuses shared by the check and troubleshoot demo data."""

from nbkp.fsprotocol import Snapshot
from nbkp.preflight import (
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
from nbkp.preflight.status.result import PreflightResult


def demo_ssh_statuses(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
) -> dict[str, SshEndpointStatus]:
    """
    Every distinct SSH endpoint status embedded in the demo statuses.

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


def collect_ssh_endpoint_statuses(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
) -> dict[str, SshEndpointStatus]:
    """
    Extract the SSH endpoint statuses embedded in volume statuses.

    They feed the SSH Endpoints table, in order of first appearance; the
    first status seen for a slug wins over later ones.
    """
    all_ssh = [
        *[vs.ssh_endpoint_status for vs in vol_statuses.values()],
        *[
            ep.volume_status.ssh_endpoint_status
            for ss in sync_statuses.values()
            for ep in [ss.source_endpoint_status, ss.destination_endpoint_status]
        ],
    ]
    # Iterating in reverse lets earlier statuses overwrite later ones, but
    # leaves the keys in reverse order, so the order comes from fromkeys.
    first_seen = {s.slug: s for s in reversed(all_ssh)}
    return {slug: first_seen[slug] for slug in dict.fromkeys(s.slug for s in all_ssh)}


def assemble_preflight_result(
    vol_statuses: dict[str, VolumeStatus],
    sync_statuses: dict[str, SyncStatus],
    *,
    ssh_endpoint_statuses: dict[str, SshEndpointStatus] | None = None,
) -> PreflightResult:
    """
    Return the result of a preflight that observed these statuses.

    The endpoint statuses are the ones the sync statuses embed, keyed by endpoint
    slug as a real preflight keys them. *ssh_endpoint_statuses* defaults to the
    ones the volume and sync statuses embed, by
    :func:`collect_ssh_endpoint_statuses`.
    """
    return PreflightResult(
        ssh_endpoint_statuses=(
            collect_ssh_endpoint_statuses(vol_statuses, sync_statuses)
            if ssh_endpoint_statuses is None
            else ssh_endpoint_statuses
        ),
        volume_statuses=vol_statuses,
        source_endpoint_statuses={
            ss.source_endpoint_status.endpoint_slug: ss.source_endpoint_status
            for ss in sync_statuses.values()
        },
        destination_endpoint_statuses={
            ss.destination_endpoint_status.endpoint_slug: ss.destination_endpoint_status
            for ss in sync_statuses.values()
        },
        sync_statuses=sync_statuses,
    )


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
    """Return the status of SSH endpoint *slug*, with no host tools probed."""
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
    """
    Return an error-free source endpoint status on *volume_status*.

    With *snapshot_mode*, it also has snapshot directories and a valid ``latest``.
    """
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
    """
    Return an error-free destination endpoint status on *volume_status*.

    *diagnostics* defaults to a writable endpoint with its sentinel.
    """
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
    """
    Return an unobserved destination endpoint status on *volume_status*.

    Its error is ``VOLUME_INACTIVE`` when the volume is inactive, and none otherwise.
    """
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
    """
    Return an unobserved source endpoint status on *volume_status*.

    Its error is ``VOLUME_INACTIVE`` when the volume is inactive, and none otherwise.
    """
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
