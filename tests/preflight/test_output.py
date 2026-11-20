"""Tests for nbkp.preflight.output (troubleshoot and check formatting)."""

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
from nbkp.policy import Strictness
from nbkp.preflight import (
    DestinationEndpointDiagnostics,
    DestinationEndpointErrorKind,
    DestinationEndpointStatus,
    SourceEndpointDiagnostics,
    SourceEndpointErrorKind,
    SourceEndpointStatus,
    SshEndpointDiagnostics,
    SshEndpointErrorKind,
    SshEndpointStatus,
    SyncErrorKind,
    SyncStatus,
    VolumeDiagnostics,
    VolumeErrorKind,
    VolumeStatus,
)
from nbkp.preflight.output import format_human_troubleshoot
from nbkp.preflight.output.check import (
    _format_destination_diagnostics,
    _format_latest,
)
from nbkp.preflight.status.endpoint import LatestSymlinkState


def _make_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False)
    return console, buf


def _localhost_ssh_status() -> SshEndpointStatus:
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
    return {
        vs.ssh_endpoint_status.slug: vs.ssh_endpoint_status
        for vs in vol_statuses.values()
    }


def _make_vol_statuses(
    config: Config,
    src_errors: list[VolumeErrorKind] | None = None,
    dst_errors: list[VolumeErrorKind] | None = None,
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
    dst_ep_errors: list[DestinationEndpointErrorKind] | None = None,
    src_ep_errors: list[SourceEndpointErrorKind] | None = None,
    sync_errors: list[SyncErrorKind] | None = None,
    sync_slug: str = "btrfs-sync",
) -> SyncStatus:
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
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
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
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
        )
        output = buf.getvalue()
        assert "btrfs subvolume create /mnt/dst/staging" in output

    def test_destination_staging_not_found_reason_label(self) -> None:
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
        )
        output = buf.getvalue()
        assert "staging/ directory not found" in output

    def test_no_issues_message(self) -> None:
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {"btrfs-sync": _make_sync_status(config, vol_statuses)}
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
        )
        output = buf.getvalue()
        assert "No issues found" in output

    def test_destination_staging_not_found_with_subdir(self) -> None:
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
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            )
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
        )
        output = buf.getvalue()
        assert "/mnt/dst/backup/staging" in output


class TestTroubleshootStagingNotBtrfsSubvolume:
    def test_moves_the_plain_directory_aside_before_creating_the_subvolume(
        self,
    ) -> None:
        """staging/ exists as a plain directory, so `subvolume create` on its
        path alone would fail with "target path already exists"."""
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                dst_ep_errors=[
                    DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME
                ],
            )
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
        )
        output = buf.getvalue()
        move = output.index("sudo mv /mnt/dst/staging /mnt/dst/staging.old")
        create = output.index("sudo btrfs subvolume create /mnt/dst/staging")
        assert move < create
        assert "sudo mkdir -p /mnt/dst/snapshots" in output


class TestTroubleshootDryRunPendingSnapshot:
    def test_dry_run_pending_snapshot_text(self) -> None:
        config = _btrfs_config()
        vol_statuses = _make_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": _make_sync_status(
                config,
                vol_statuses,
                sync_errors=[SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING],
            )
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            )
        )
        output = buf.getvalue()
        assert "dry-run" in output
        assert "upstream" in output
        assert (
            "source snapshot not yet available (dry-run; upstream has not run)"
            in output
        )


class TestTroubleshootLocationExcluded:
    def test_location_excluded_volume_text(self) -> None:
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
            errors=[SshEndpointErrorKind.LOCATION_EXCLUDED],
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
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                {},
                config,
            )
        )
        output = buf.getvalue()
        assert "excluded by location filter" in output
        assert "--exclude-location" in output


class TestCheckLatestColumn:
    def test_invalid_target_shown_as_error(self) -> None:
        text = _format_latest(
            LatestSymlinkState(
                exists=True, raw_target="snapshots/foo", target_valid=False
            )
        )
        assert str(text) == "✗latest → snapshots/foo"

    def test_valid_target_shown_as_ok(self) -> None:
        target = "snapshots/2026-03-06T14:30:00.000Z"
        text = _format_latest(
            LatestSymlinkState(exists=True, raw_target=target, target_valid=True)
        )
        assert str(text) == f"✓latest → {target}"

    def test_devnull_shown_as_ok(self) -> None:
        text = _format_latest(LatestSymlinkState(exists=True, raw_target="/dev/null"))
        assert str(text) == "✓latest → /dev/null"

    def test_missing_shown_as_error(self) -> None:
        assert str(_format_latest(LatestSymlinkState(exists=False))) == "✗latest"


class TestCheckDestinationColumn:
    def _status(self, *, sentinel_exists: bool) -> SyncStatus:
        config = _btrfs_config()
        status = _make_sync_status(config, _make_vol_statuses(config))
        dst = status.destination_endpoint_status
        return status.model_copy(
            update={
                "destination_endpoint_status": dst.model_copy(
                    update={
                        "diagnostics": DestinationEndpointDiagnostics(
                            endpoint_slug=dst.endpoint_slug,
                            sentinel_exists=sentinel_exists,
                            endpoint_writable=False,
                        )
                    }
                )
            }
        )

    def test_writable_hidden_when_sentinel_missing(self) -> None:
        """The status layer suppresses NOT_WRITABLE then: no ✗writable."""
        text = _format_destination_diagnostics(
            self._status(sentinel_exists=False), Strictness.IGNORE_INACTIVE
        )
        assert "writable" not in str(text)

    def test_writable_shown_when_sentinel_present(self) -> None:
        text = _format_destination_diagnostics(
            self._status(sentinel_exists=True), Strictness.IGNORE_INACTIVE
        )
        assert "✗writable" in str(text)
