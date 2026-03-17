"""Tests for nbkp.preflight.output (troubleshoot and check formatting)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointError,
    DestinationEndpointStatus,
    SourceEndpointDiagnostics,
    SourceEndpointError,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointError,
    SshEndpointStatus,
    SyncError,
    SyncStatus,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from nbkp.preflight.output import print_human_troubleshoot


def _make_console() -> tuple[Console, StringIO]:
    """Return a Console that writes to a StringIO buffer."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False)
    return console, buf


def _localhost_ssh_status() -> SshEndpointStatus:
    """Create an active localhost SSH endpoint status."""
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
        errors=[],
    )


def _btrfs_config() -> Config:
    src = LocalVolume(slug="src", path="/mnt/src")
    dst = LocalVolume(slug="dst", path="/mnt/dst")
    ep_src = SyncEndpoint(slug="ep-src", volume="src")
    ep_dst = SyncEndpoint(
        slug="ep-dst",
        volume="dst",
        btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
    )
    sync = SyncConfig(
        slug="btrfs-sync",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
        syncs={"btrfs-sync": sync},
    )


def _ssh_statuses_from(
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SshEndpointStatus]:
    """Extract unique SSH endpoint statuses from volume statuses."""
    return {
        vs.ssh_endpoint_status.slug: vs.ssh_endpoint_status
        for vs in vol_statuses.values()
    }


def _make_vol_statuses(
    config: Config,
    src_errors: list[VolumeError] | None = None,
    dst_errors: list[VolumeError] | None = None,
) -> dict[str, VolumeStatus]:
    ssh_status = _localhost_ssh_status()
    return {
        "src": VolumeStatus(
            slug="src",
            config=config.volumes["src"],
            ssh_endpoint_status=ssh_status,
            diagnostics=VolumeDiagnostics(),
            errors=src_errors or [],
        ),
        "dst": VolumeStatus(
            slug="dst",
            config=config.volumes["dst"],
            ssh_endpoint_status=ssh_status,
            diagnostics=VolumeDiagnostics(),
            errors=dst_errors or [],
        ),
    }


def _make_sync_status(
    config: Config,
    vol_statuses: dict[str, VolumeStatus],
    *,
    dst_ep_errors: list[DestinationEndpointError] | None = None,
    src_ep_errors: list[SourceEndpointError] | None = None,
    sync_errors: list[SyncError] | None = None,
    sync_slug: str = "btrfs-sync",
) -> SyncStatus:
    """Build a SyncStatus with the new 4-layer model."""
    sync_cfg = config.syncs[sync_slug]
    src_ep_slug = sync_cfg.source
    dst_ep_slug = sync_cfg.destination
    src_vol_slug = config.sync_endpoints[src_ep_slug].volume
    dst_vol_slug = config.sync_endpoints[dst_ep_slug].volume

    src_ep_status = SourceEndpointStatus(
        endpoint_slug=src_ep_slug,
        volume_status=vol_statuses[src_vol_slug],
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=src_ep_slug,
            sentinel_exists=True,
        ),
        errors=src_ep_errors or [],
    )
    dst_ep_status = DestinationEndpointStatus(
        endpoint_slug=dst_ep_slug,
        volume_status=vol_statuses[dst_vol_slug],
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug=dst_ep_slug,
            sentinel_exists=True,
            endpoint_writable=True,
        ),
        errors=dst_ep_errors or [],
    )
    return SyncStatus(
        slug=sync_slug,
        config=sync_cfg,
        source_endpoint_status=src_ep_status,
        destination_endpoint_status=dst_ep_status,
        errors=sync_errors or [],
    )


class TestTroubleshootBtrfsStagingNotFound:
    def test_destination_staging_not_found_text(self) -> None:
        """Output mentions staging/ for STAGING_SUBVOL_NOT_FOUND."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "staging" in output
        assert "btrfs subvolume create" in output
        assert "/mnt/dst/staging" in output

    def test_destination_staging_not_found_no_latest(self) -> None:
        """Fix instructions reference staging/, not latest/."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        # The fix instructions must reference staging/, not latest/
        assert "btrfs subvolume create /mnt/dst/staging" in output

    def test_destination_staging_not_found_reason_label(self) -> None:
        """DestinationEndpointError label for STAGING_SUBVOL_NOT_FOUND appears."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND.value in output

    def test_no_issues_message(self) -> None:
        """When all statuses are active, prints no-issues message."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {"btrfs-sync": _make_sync_status(config, vol_statuses)}
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "No issues found" in output

    def test_destination_staging_not_found_with_subdir(self) -> None:
        """Troubleshoot output uses the correct subdir path for staging/."""
        src = LocalVolume(slug="src", path="/mnt/src")
        dst = LocalVolume(slug="dst", path="/mnt/dst")
        ep_src = SyncEndpoint(slug="ep-src", volume="src")
        ep_dst = SyncEndpoint(
            slug="ep-dst",
            volume="dst",
            subdir="backup",
            btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
        )
        sync = SyncConfig(
            slug="btrfs-sync",
            source="ep-src",
            destination="ep-dst",
        )
        config = Config(
            volumes={"src": src, "dst": dst},
            sync_endpoints={"ep-src": ep_src, "ep-dst": ep_dst},
            syncs={"btrfs-sync": sync},
        )
        ssh_status = _localhost_ssh_status()
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src,
                ssh_endpoint_status=ssh_status,
                diagnostics=VolumeDiagnostics(),
                errors=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst,
                ssh_endpoint_status=ssh_status,
                diagnostics=VolumeDiagnostics(),
                errors=[],
            ),
        }
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[DestinationEndpointError.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "/mnt/dst/backup/staging" in output


class TestTroubleshootDryRunPendingSnapshot:
    def test_dry_run_pending_snapshot_text(self) -> None:
        """Troubleshoot output explains dry-run snapshot skip."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                sync_errors=[SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "dry-run" in output
        assert "upstream" in output
        assert SyncError.DRY_RUN_SRC_EP_SNAPSHOT_PENDING.value in output


class TestTroubleshootLocationExcluded:
    def test_location_excluded_volume_text(self) -> None:
        """Troubleshoot output explains location-excluded SSH endpoint."""
        vol = RemoteVolume(
            slug="remote",
            ssh_endpoint="server",
            path="/data",
        )
        config = Config(
            ssh_endpoints={
                "server": SshEndpoint(
                    slug="server",
                    host="10.0.0.1",
                    location="home",
                ),
            },
            volumes={"remote": vol},
            syncs={},
        )
        ssh_status = SshEndpointStatus(
            slug="server",
            diagnostics=SshEndpointDiagnostics(location_excluded=True),
            errors=[SshEndpointError.LOCATION_EXCLUDED],
        )
        vol_statuses = {
            "remote": VolumeStatus(
                slug="remote",
                config=vol,
                ssh_endpoint_status=ssh_status,
                diagnostics=None,
                errors=[],
            ),
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            _ssh_statuses_from(vol_statuses),
            vol_statuses,
            {},
            config,
            console=console,
        )
        output = buf.getvalue()
        assert SshEndpointError.LOCATION_EXCLUDED.value in output
        assert "--exclude-location" in output
