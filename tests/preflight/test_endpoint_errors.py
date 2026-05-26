"""Tests for sync-endpoint error interpretation.

Covers the policy logic in ``status.py`` that translates raw
diagnostics into error enums, separately from full ``check_all_syncs``
orchestration tests.
"""

from __future__ import annotations

from nbkp.config import (
    BtrfsSnapshotConfig,
    LocalVolume,
    SyncEndpoint,
)
from nbkp.preflight.status import (
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    DestinationEndpointStatus,
    HostToolCapabilities,
    SnapshotDirsDiagnostics,
    SshEndpointDiagnostics,
    SshEndpointStatus,
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeStatus,
)


def _ssh_status() -> SshEndpointStatus:
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(
            host_tools=HostToolCapabilities(
                has_rsync=True,
                rsync_version_ok=True,
                has_btrfs=False,
                has_stat=True,
                has_findmnt=True,
            )
        ),
        errors=[],
    )


def _vol_status() -> VolumeStatus:
    return VolumeStatus(
        slug="dst",
        config=LocalVolume(slug="dst", path="/mnt/dst"),
        ssh_endpoint_status=_ssh_status(),
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            )
        ),
        errors=[],
    )


class TestNotWritableSuppression:
    """``NOT_WRITABLE`` only fires when the endpoint directory exists.

    Approximated via ``sentinel_exists`` (the sentinel lives inside
    the dir, so its presence proves the dir exists).  This prevents
    a misleading "fix permissions" message when ``test -w`` actually
    failed because the directory doesn't exist yet.
    """

    def test_not_writable_fires_when_sentinel_exists(self) -> None:
        """Sentinel present, dir not writable → NOT_WRITABLE is real."""
        ep = SyncEndpoint(slug="ep-dst", volume="dst")
        diag = DestinationEndpointDiagnostics(
            endpoint_slug="ep-dst",
            sentinel_exists=True,
            endpoint_writable=False,
        )
        status = DestinationEndpointStatus.from_diagnostics(ep, _vol_status(), diag)
        assert DestinationEndpointError.NOT_WRITABLE in status.errors

    def test_not_writable_suppressed_when_sentinel_missing(self) -> None:
        """Sentinel missing → dir likely doesn't exist → suppress NOT_WRITABLE.

        Showing both SENTINEL_NOT_FOUND (with its ``mkdir -p`` fix) and
        NOT_WRITABLE would be misleading: the create-dir fix already
        addresses the underlying cause when the dir is absent.
        """
        ep = SyncEndpoint(slug="ep-dst", volume="dst")
        diag = DestinationEndpointDiagnostics(
            endpoint_slug="ep-dst",
            sentinel_exists=False,
            endpoint_writable=False,
        )
        status = DestinationEndpointStatus.from_diagnostics(ep, _vol_status(), diag)
        assert DestinationEndpointError.SENTINEL_NOT_FOUND in status.errors
        assert DestinationEndpointError.NOT_WRITABLE not in status.errors

    def test_not_writable_suppressed_with_snapshot_errors(self) -> None:
        """Same suppression holds when other 'create me' errors are present."""
        ep = SyncEndpoint(
            slug="ep-dst",
            volume="dst",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=False),
        )
        diag = DestinationEndpointDiagnostics(
            endpoint_slug="ep-dst",
            sentinel_exists=False,
            endpoint_writable=False,
            snapshot_dirs=SnapshotDirsDiagnostics(exists=False),
        )
        status = DestinationEndpointStatus.from_diagnostics(ep, _vol_status(), diag)
        assert DestinationEndpointError.SENTINEL_NOT_FOUND in status.errors
        assert DestinationEndpointError.NOT_WRITABLE not in status.errors
