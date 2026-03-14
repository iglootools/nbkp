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


def _make_vol_statuses(
    config: Config,
    src_errors: list[VolumeError] | None = None,
    dst_errors: list[VolumeError] | None = None,
) -> dict[str, VolumeStatus]:
    return {
        "src": VolumeStatus(
            slug="src",
            config=config.volumes["src"],
            diagnostics=VolumeDiagnostics(),
            errors=src_errors or [],
        ),
        "dst": VolumeStatus(
            slug="dst",
            config=config.volumes["dst"],
            diagnostics=VolumeDiagnostics(),
            errors=dst_errors or [],
        ),
    }


class TestTroubleshootBtrfsStagingNotFound:
    def test_destination_staging_not_found_text(self) -> None:
        """Output mentions staging/ for DESTINATION_TMP_NOT_FOUND."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                errors=[SyncError.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
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
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                errors=[SyncError.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        # The fix instructions must reference staging/, not latest/
        assert "btrfs subvolume create /mnt/dst/staging" in output

    def test_destination_staging_not_found_reason_label(self) -> None:
        """SyncError label for TMP_NOT_FOUND appears."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                errors=[SyncError.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert SyncError.DESTINATION_TMP_NOT_FOUND.value in output

    def test_no_issues_message(self) -> None:
        """When all statuses are active, prints no-issues message."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                errors=[],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
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
        vol_statuses = {
            "src": VolumeStatus(
                slug="src",
                config=src,
                diagnostics=VolumeDiagnostics(),
                errors=[],
            ),
            "dst": VolumeStatus(
                slug="dst",
                config=dst,
                diagnostics=VolumeDiagnostics(),
                errors=[],
            ),
        }
        sync_statuses = {
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=sync,
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                errors=[SyncError.DESTINATION_TMP_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
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
            "btrfs-sync": SyncStatus(
                slug="btrfs-sync",
                config=config.syncs["btrfs-sync"],
                source_status=vol_statuses["src"],
                destination_status=vol_statuses["dst"],
                errors=[SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING],
            )
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            sync_statuses,
            config,
            console=console,
        )
        output = buf.getvalue()
        assert "dry-run" in output
        assert "upstream" in output
        assert SyncError.DRY_RUN_SOURCE_SNAPSHOT_PENDING.value in output


class TestTroubleshootLocationExcluded:
    def test_location_excluded_volume_text(self) -> None:
        """Troubleshoot output explains location-excluded volume."""
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
        vol_statuses = {
            "remote": VolumeStatus(
                slug="remote",
                config=vol,
                diagnostics=VolumeDiagnostics(location_excluded=True),
                errors=[VolumeError.LOCATION_EXCLUDED],
            ),
        }
        console, buf = _make_console()
        print_human_troubleshoot(
            vol_statuses,
            {},
            config,
            console=console,
        )
        output = buf.getvalue()
        assert VolumeError.LOCATION_EXCLUDED.value in output
        assert "--exclude-location" in output
