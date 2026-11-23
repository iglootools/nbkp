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
    SshEndpointDiagnostics,
    SshEndpointErrorKind,
    SshEndpointStatus,
    SyncErrorKind,
    SyncStatus,
    VolumeDiagnostics,
    VolumeStatus,
)
from nbkp.preflight.output import format_human_troubleshoot
from nbkp.preflight.output.check import format_human_check
from nbkp.preflight.status.endpoint import LatestSymlinkState
from nbkp.preflight.testkit import troubleshoot_config, troubleshoot_data
from tests.fixtures.preflight import (
    btrfs_config,
    local_vol_statuses,
    localhost_ssh_status,
    sync_status,
)


def _make_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False)
    return console, buf


def _ssh_statuses_from(
    vol_statuses: dict[str, VolumeStatus],
) -> dict[str, SshEndpointStatus]:
    return {
        vs.ssh_endpoint_status.slug: vs.ssh_endpoint_status
        for vs in vol_statuses.values()
    }


class TestTroubleshootBtrfsStagingNotFound:
    def test_destination_staging_not_found_text(self) -> None:
        config = btrfs_config()
        vol_statuses = local_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": sync_status(
                config,
                vol_statuses,
                "btrfs-sync",
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            ),
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
        )
        output = buf.getvalue()
        assert "staging" in output
        assert "btrfs subvolume create" in output
        assert "/mnt/dst/staging" in output

    def test_destination_staging_not_found_no_latest(self) -> None:
        """Fix instructions reference staging/, not latest/."""
        config = btrfs_config()
        vol_statuses = local_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": sync_status(
                config,
                vol_statuses,
                "btrfs-sync",
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            ),
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
        )
        output = buf.getvalue()
        assert "btrfs subvolume create /mnt/dst/staging" in output

    def test_destination_staging_not_found_reason_label(self) -> None:
        config = btrfs_config()
        vol_statuses = local_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": sync_status(
                config,
                vol_statuses,
                "btrfs-sync",
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            ),
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
        )
        output = buf.getvalue()
        assert "staging/ directory not found" in output

    def test_no_issues_message(self) -> None:
        config = btrfs_config()
        vol_statuses = local_vol_statuses(config)
        sync_statuses = {"btrfs-sync": sync_status(config, vol_statuses, "btrfs-sync")}
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
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
        ssh_status = localhost_ssh_status()
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
            "btrfs-sync": sync_status(
                config,
                vol_statuses,
                "btrfs-sync",
                dst_ep_errors=[DestinationEndpointErrorKind.STAGING_SUBVOL_NOT_FOUND],
            ),
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
        )
        output = buf.getvalue()
        assert "/mnt/dst/backup/staging" in output


class TestTroubleshootStagingNotBtrfsSubvolume:
    def test_moves_the_plain_directory_aside_before_creating_the_subvolume(
        self,
    ) -> None:
        """
        Move a plain ``staging/`` directory aside before creating the subvolume.

        staging/ exists as a plain directory, so `subvolume create` on its path alone
        would fail with "target path already exists".
        """
        config = btrfs_config()
        vol_statuses = local_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": sync_status(
                config,
                vol_statuses,
                "btrfs-sync",
                dst_ep_errors=[
                    DestinationEndpointErrorKind.STAGING_NOT_BTRFS_SUBVOLUME,
                ],
            ),
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
        )
        output = buf.getvalue()
        move = output.index("sudo mv /mnt/dst/staging /mnt/dst/staging.old")
        create = output.index("sudo btrfs subvolume create /mnt/dst/staging")
        assert move < create
        assert "sudo mkdir -p /mnt/dst/snapshots" in output


class TestTroubleshootDryRunPendingSnapshot:
    def test_dry_run_pending_snapshot_text(self) -> None:
        config = btrfs_config()
        vol_statuses = local_vol_statuses(config)
        sync_statuses = {
            "btrfs-sync": sync_status(
                config,
                vol_statuses,
                "btrfs-sync",
                sync_errors=[SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING],
            ),
        }
        console, buf = _make_console()
        console.print(
            format_human_troubleshoot(
                _ssh_statuses_from(vol_statuses),
                vol_statuses,
                sync_statuses,
                config,
            ),
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
            ),
        )
        output = buf.getvalue()
        assert "excluded by location filter" in output
        assert "--exclude-location" in output


def _check_output(
    sync: SyncStatus,
    config: Config,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> str:
    """``preflight check``'s human output for *sync*, on one line per row."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=False, width=400)
    console.print(
        format_human_check(
            {},
            {},
            {sync.slug: sync},
            config,
            strictness=strictness,
            wrap_in_panel=False,
        ),
    )
    return buf.getvalue()


def _with_destination_diagnostics(
    sync: SyncStatus,
    **diagnostics: object,
) -> SyncStatus:
    dst = sync.destination_endpoint_status
    return sync.model_copy(
        update={
            "destination_endpoint_status": dst.model_copy(
                update={
                    "diagnostics": DestinationEndpointDiagnostics.model_validate(
                        {"endpoint_slug": dst.endpoint_slug, **diagnostics},
                    ),
                },
            ),
        },
    )


class TestCheckLatestColumn:
    def _output(
        self,
        latest: LatestSymlinkState,
        strictness: Strictness = Strictness.IGNORE_INACTIVE,
    ) -> str:
        config = btrfs_config()
        sync = sync_status(config, local_vol_statuses(config), "btrfs-sync")
        return _check_output(
            _with_destination_diagnostics(
                sync,
                sentinel_exists=True,
                endpoint_writable=True,
                latest=latest,
            ),
            config,
            strictness,
        )

    def test_invalid_target_shown_as_error(self) -> None:
        output = self._output(
            LatestSymlinkState(
                exists=True,
                raw_target="snapshots/foo",
                target_valid=False,
            ),
        )
        assert "✗latest → snapshots/foo" in output

    def test_valid_target_shown_as_ok(self) -> None:
        target = "snapshots/2026-03-06T14:30:00.000Z"
        output = self._output(
            LatestSymlinkState(exists=True, raw_target=target, target_valid=True),
        )
        assert f"✓latest → {target}" in output

    def test_devnull_shown_as_ok(self) -> None:
        output = self._output(LatestSymlinkState(exists=True, raw_target="/dev/null"))
        assert "✓latest → /dev/null" in output

    def test_missing_shown_as_error(self) -> None:
        output = self._output(LatestSymlinkState(exists=False))
        assert "✗latest" in output
        assert "✓latest" not in output

    def test_missing_shown_as_warning_under_ignore_all(self) -> None:
        output = self._output(LatestSymlinkState(exists=False), Strictness.IGNORE_ALL)
        assert "⚠latest" in output
        assert "✗latest" not in output

    def test_invalid_target_shown_as_warning_under_ignore_all(self) -> None:
        output = self._output(
            LatestSymlinkState(
                exists=True,
                raw_target="snapshots/foo",
                target_valid=False,
            ),
            Strictness.IGNORE_ALL,
        )
        assert "⚠latest → snapshots/foo" in output


class TestCheckLatestSnapshotWritable:
    def test_shown_as_a_warning_with_the_troubleshoot_hint(self) -> None:
        config = troubleshoot_config()
        sync = troubleshoot_data(config).sync_statuses["latest-snapshot-writable"]
        buf = StringIO()
        Console(file=buf, highlight=False, markup=False, width=400).print(
            format_human_check({}, {}, {sync.slug: sync}, config, wrap_in_panel=False),
        )
        output = buf.getvalue()
        assert "the snapshot latest points to is not read-only" in output
        assert "preflight troubleshoot" in output


class TestCheckDestinationColumn:
    def _output(self, *, sentinel_exists: bool) -> str:
        config = btrfs_config()
        sync = sync_status(config, local_vol_statuses(config), "btrfs-sync")
        return _check_output(
            _with_destination_diagnostics(
                sync,
                sentinel_exists=sentinel_exists,
                endpoint_writable=False,
            ),
            config,
        )

    def test_writable_hidden_when_sentinel_missing(self) -> None:
        """The status layer suppresses NOT_WRITABLE then: no ✗writable."""
        assert "writable" not in self._output(sentinel_exists=False)

    def test_writable_shown_when_sentinel_present(self) -> None:
        assert "✗writable" in self._output(sentinel_exists=True)
