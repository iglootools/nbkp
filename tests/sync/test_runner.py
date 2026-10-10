"""Tests for nbkp.runner."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from nbkp.clihelpers import Strictness
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    RemoteVolume,
    SshEndpoint,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.fsprotocol import Snapshot
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
    VolumeCapabilities,
    VolumeDiagnostics,
    VolumeError,
    VolumeStatus,
)
from nbkp.snapshots.errors import SnapshotOp, SnapshotOperationError
from nbkp.sync import SyncResult, run_all_syncs
from nbkp.sync.runner import SyncFailureKind, SyncOutcome, SyncWarningKind

_FIXED_NOW = datetime(2026, 3, 6, 14, 30, 0, tzinfo=UTC)


def _run(
    config: Config, sync_statuses: dict[str, SyncStatus], **kwargs: Any
) -> list[SyncResult]:
    """run_all_syncs with a fixed clock and platform."""
    return run_all_syncs(
        config,
        sync_statuses,
        clock=lambda: _FIXED_NOW,
        platform="linux",
        **kwargs,
    )


def _active_ssh_status() -> SshEndpointStatus:
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(),
        errors=[],
    )


def _inactive_ssh_status() -> SshEndpointStatus:
    return SshEndpointStatus(
        slug="localhost",
        diagnostics=SshEndpointDiagnostics(ssh_reachable=False),
        errors=[SshEndpointError.UNREACHABLE],
    )


def _active_vol_status(name: str, vol: Volume) -> VolumeStatus:
    return VolumeStatus(
        slug=name,
        config=vol,
        ssh_endpoint_status=_active_ssh_status(),
        diagnostics=VolumeDiagnostics(
            capabilities=VolumeCapabilities(
                sentinel_exists=True,
                is_btrfs_filesystem=False,
                hardlink_supported=True,
                btrfs_user_subvol_rm=False,
            ),
        ),
        errors=[],
    )


def _inactive_vol_status(name: str, vol: Volume) -> VolumeStatus:
    return VolumeStatus(
        slug=name,
        config=vol,
        ssh_endpoint_status=_inactive_ssh_status(),
        diagnostics=None,
        errors=[VolumeError.SSH_ENDPOINT_INACTIVE],
    )


def _active_src_ep_status(config: Config, sync: SyncConfig) -> SourceEndpointStatus:
    ep = config.source_endpoint(sync)
    vol = config.volumes[ep.volume]
    return SourceEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=_active_vol_status(ep.volume, vol),
        diagnostics=SourceEndpointDiagnostics(
            endpoint_slug=ep.slug,
            sentinel_exists=True,
        ),
        errors=[],
    )


def _active_dst_ep_status(
    config: Config, sync: SyncConfig
) -> DestinationEndpointStatus:
    ep = config.destination_endpoint(sync)
    vol = config.volumes[ep.volume]
    return DestinationEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=_active_vol_status(ep.volume, vol),
        diagnostics=DestinationEndpointDiagnostics(
            endpoint_slug=ep.slug,
            sentinel_exists=True,
            endpoint_writable=True,
        ),
        errors=[],
    )


def _inactive_src_ep_status(config: Config, sync: SyncConfig) -> SourceEndpointStatus:
    ep = config.source_endpoint(sync)
    vol = config.volumes[ep.volume]
    return SourceEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=_inactive_vol_status(ep.volume, vol),
        diagnostics=None,
        errors=[SourceEndpointError.VOLUME_INACTIVE],
    )


def _inactive_dst_ep_status(
    config: Config, sync: SyncConfig
) -> DestinationEndpointStatus:
    ep = config.destination_endpoint(sync)
    vol = config.volumes[ep.volume]
    return DestinationEndpointStatus(
        endpoint_slug=ep.slug,
        volume_status=_inactive_vol_status(ep.volume, vol),
        diagnostics=None,
        errors=[DestinationEndpointError.VOLUME_INACTIVE],
    )


def _make_local_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={"s1": sync},
    )


def _make_btrfs_config() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )


def _make_btrfs_config_with_max() -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=5),
            ),
        },
        syncs={"s1": sync},
    )


def _make_remote_same_server_btrfs_config() -> Config:
    server = SshEndpoint(slug="server", host="nas.local", user="backup")
    src = RemoteVolume(
        slug="src",
        ssh_endpoint="server",
        path="/data",
    )
    dst = RemoteVolume(
        slug="dst",
        ssh_endpoint="server",
        path="/backup",
    )
    sync = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-dst",
    )
    return Config(
        ssh_endpoints={"server": server},
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True),
            ),
        },
        syncs={"s1": sync},
    )


def _active_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=_active_src_ep_status(config, sync),
            destination_endpoint_status=_active_dst_ep_status(config, sync),
            errors=[],
        )
        for name, sync in config.syncs.items()
    }
    return {}, sync_statuses


def _inactive_statuses(
    config: Config,
) -> tuple[dict[str, VolumeStatus], dict[str, SyncStatus]]:
    sync_statuses = {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=_inactive_src_ep_status(config, sync),
            destination_endpoint_status=_inactive_dst_ep_status(config, sync),
            errors=[
                SyncError.SOURCE_ENDPOINT_INACTIVE,
                SyncError.DESTINATION_ENDPOINT_INACTIVE,
            ],
        )
        for name, sync in config.syncs.items()
    }
    return {}, sync_statuses


class TestRunAllSyncs:
    @patch("nbkp.sync.runner.run_rsync")
    def test_successful_sync(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")

        results = _run(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].rsync_exit_code == 0

    def test_inactive_sync(self) -> None:
        config = _make_local_config()
        _, sync_statuses = _inactive_statuses(config)

        results = _run(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].outcome == SyncOutcome.SKIPPED
        assert results[0].failure == SyncFailureKind.INACTIVE

    @patch("nbkp.sync.runner.run_rsync")
    def test_rsync_failure(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="error")

        results = _run(config, sync_statuses)
        assert results[0].success is False
        assert results[0].rsync_exit_code == 23

    @patch("nbkp.sync.runner.run_rsync")
    def test_filter_by_sync_slug(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")

        results = _run(config, sync_statuses, only_syncs=["nonexistent"])
        assert len(results) == 0

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_btrfs_snapshot_after_sync(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        results = _run(config, sync_statuses)
        assert results[0].success is True
        assert results[0].snapshot_path == "/dst/snapshots/20240115T120000Z"
        mock_snap.assert_called_once()
        assert mock_snap.call_args.kwargs["now"] == _FIXED_NOW
        assert mock_snap.call_args.kwargs["platform"] == "linux"
        mock_symlink.assert_called_once()

    @patch("nbkp.sync.runner.run_rsync")
    def test_btrfs_snapshot_skipped_on_dry_run(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")

        results = _run(config, sync_statuses, dry_run=True)
        assert results[0].success is True
        assert results[0].snapshot_path is None

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_btrfs_no_link_dest(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        _run(config, sync_statuses)

        # Btrfs workflow no longer passes --link-dest
        call_kwargs = mock_rsync.call_args
        assert call_kwargs.kwargs.get("link_dest") is None

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_remote_same_server_with_btrfs(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_symlink: MagicMock,
    ) -> None:
        config = _make_remote_same_server_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/backup/snapshots/20240115T120000Z"

        results = _run(config, sync_statuses)
        assert results[0].success is True
        assert results[0].snapshot_path is not None
        mock_snap.assert_called_once()
        mock_symlink.assert_called_once()

    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_snapshot_failure(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.side_effect = SnapshotOperationError(
            SnapshotOp.CREATE, "/dst/snapshots/x", "btrfs failed"
        )

        results = _run(config, sync_statuses)
        assert results[0].outcome == SyncOutcome.FAILED
        assert results[0].failure == SyncFailureKind.SNAPSHOT

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.btrfs_prune_snapshots")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_auto_prune_after_snapshot(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config_with_max()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"
        mock_prune.return_value = ["/dst/snapshots/old"]

        results = _run(config, sync_statuses)
        assert results[0].success is True
        assert results[0].pruned_paths == ("/dst/snapshots/old",)
        mock_prune.assert_called_once()

    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.btrfs_prune_snapshots")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_no_auto_prune_without_max_snapshots(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")
        mock_snap.return_value = "/dst/snapshots/20240115T120000Z"

        results = _run(config, sync_statuses)
        assert results[0].success is True
        assert results[0].pruned_paths is None
        mock_prune.assert_not_called()


def _make_chain_config() -> Config:
    """Upstream s1 writes to 'mid', downstream s2 reads."""
    src = LocalVolume(slug="src", path="/src")
    mid = LocalVolume(slug="mid", path="/mid")
    dst = LocalVolume(slug="dst", path="/dst")
    s1 = SyncConfig(
        slug="s1",
        source="ep-src",
        destination="ep-mid",
    )
    s2 = SyncConfig(
        slug="s2",
        source="ep-mid",
        destination="ep-dst",
    )
    return Config(
        volumes={"src": src, "mid": mid, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-mid": SyncEndpoint(slug="ep-mid", volume="mid"),
            "ep-dst": SyncEndpoint(slug="ep-dst", volume="dst"),
        },
        syncs={"s1": s1, "s2": s2},
    )


def _make_independent_config() -> Config:
    """Two independent syncs with no shared volumes."""
    src1 = LocalVolume(slug="src1", path="/src1")
    dst1 = LocalVolume(slug="dst1", path="/dst1")
    src2 = LocalVolume(slug="src2", path="/src2")
    dst2 = LocalVolume(slug="dst2", path="/dst2")
    s1 = SyncConfig(
        slug="s1",
        source="ep-src1",
        destination="ep-dst1",
    )
    s2 = SyncConfig(
        slug="s2",
        source="ep-src2",
        destination="ep-dst2",
    )
    return Config(
        volumes={
            "src1": src1,
            "dst1": dst1,
            "src2": src2,
            "dst2": dst2,
        },
        sync_endpoints={
            "ep-src1": SyncEndpoint(slug="ep-src1", volume="src1"),
            "ep-dst1": SyncEndpoint(slug="ep-dst1", volume="dst1"),
            "ep-src2": SyncEndpoint(slug="ep-src2", volume="src2"),
            "ep-dst2": SyncEndpoint(slug="ep-dst2", volume="dst2"),
        },
        syncs={"s1": s1, "s2": s2},
    )


class TestFailurePropagation:
    @patch("nbkp.sync.runner.run_rsync")
    def test_downstream_sync_cancelled_on_failure(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_chain_config()
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="error")

        results = _run(config, sync_statuses)
        assert len(results) == 2

        # s1 failed
        r1 = next(r for r in results if r.sync_slug == "s1")
        assert r1.success is False
        assert r1.outcome == SyncOutcome.FAILED
        assert r1.rsync_exit_code == 23

        # s2 cancelled
        r2 = next(r for r in results if r.sync_slug == "s2")
        assert r2.success is False
        assert r2.outcome == SyncOutcome.CANCELLED
        assert r2.failure == SyncFailureKind.UPSTREAM_FAILED
        assert r2.cancelled_by == "s1"

    @patch("nbkp.sync.runner.run_rsync")
    def test_independent_sync_not_cancelled(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_independent_config()
        _, sync_statuses = _active_statuses(config)

        call_count = 0

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(returncode=23, stdout="", stderr="error")
            return MagicMock(returncode=0, stdout="done\n", stderr="")

        mock_rsync.side_effect = _side_effect

        results = _run(config, sync_statuses)
        assert len(results) == 2
        # One failed, one succeeded (order may vary)
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        # The failure is a real rsync failure, not a cancellation
        assert failures[0].rsync_exit_code == 23
        assert failures[0].outcome == SyncOutcome.FAILED

    @patch("nbkp.sync.runner.run_rsync")
    def test_transitive_cancellation(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        """Upstream A fails → downstream B and C cancelled."""
        v0 = LocalVolume(slug="v0", path="/v0")
        v1 = LocalVolume(slug="v1", path="/v1")
        v2 = LocalVolume(slug="v2", path="/v2")
        v3 = LocalVolume(slug="v3", path="/v3")
        config = Config(
            volumes={
                "v0": v0,
                "v1": v1,
                "v2": v2,
                "v3": v3,
            },
            sync_endpoints={
                "ep-v0": SyncEndpoint(slug="ep-v0", volume="v0"),
                "ep-v1": SyncEndpoint(slug="ep-v1", volume="v1"),
                "ep-v2": SyncEndpoint(slug="ep-v2", volume="v2"),
                "ep-v3": SyncEndpoint(slug="ep-v3", volume="v3"),
            },
            syncs={
                "a": SyncConfig(
                    slug="a",
                    source="ep-v0",
                    destination="ep-v1",
                ),
                "b": SyncConfig(
                    slug="b",
                    source="ep-v1",
                    destination="ep-v2",
                ),
                "c": SyncConfig(
                    slug="c",
                    source="ep-v2",
                    destination="ep-v3",
                ),
            },
        )
        _, sync_statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="error")

        results = _run(config, sync_statuses)
        assert len(results) == 3

        ra = next(r for r in results if r.sync_slug == "a")
        rb = next(r for r in results if r.sync_slug == "b")
        rc = next(r for r in results if r.sync_slug == "c")

        assert ra.success is False
        assert ra.outcome == SyncOutcome.FAILED
        assert ra.rsync_exit_code == 23

        assert rb.success is False
        assert rb.outcome == SyncOutcome.CANCELLED

        assert rc.success is False
        assert rc.outcome == SyncOutcome.CANCELLED


# ── Hard-link snapshots ──────────────────────────────────────


def _make_hard_link_config(max_snapshots: int | None = None) -> Config:
    src = LocalVolume(slug="src", path="/src")
    dst = LocalVolume(slug="dst", path="/dst")
    return Config(
        volumes={"src": src, "dst": dst},
        sync_endpoints={
            "ep-src": SyncEndpoint(slug="ep-src", volume="src"),
            "ep-dst": SyncEndpoint(
                slug="ep-dst",
                volume="dst",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True, max_snapshots=max_snapshots
                ),
            ),
        },
        syncs={"s1": SyncConfig(slug="s1", source="ep-src", destination="ep-dst")},
    )


_NEW_SNAPSHOT = "/dst/snapshots/2026-03-06T14:30:00.000Z"
_PREVIOUS = Snapshot.from_name("2026-03-05T10:00:00.000Z")


def _snapshot_error(op: SnapshotOp) -> SnapshotOperationError:
    return SnapshotOperationError(op, "/dst/snapshots/x", "Permission denied")


def _ok_proc() -> MagicMock:
    return MagicMock(returncode=0, stdout="done\n", stderr="")


@patch("nbkp.sync.runner.hl_prune_snapshots")
@patch("nbkp.sync.runner.update_latest_symlink")
@patch("nbkp.sync.runner.hl_delete_snapshot")
@patch("nbkp.sync.runner.create_snapshot_dir")
@patch("nbkp.sync.runner.cleanup_orphaned_snapshots")
@patch("nbkp.sync.runner.run_rsync")
class TestHardLinkSync:
    def test_dry_run_never_modifies_destination(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        """No orphan cleanup, no snapshot dir, no latest update in dry-run."""
        config = _make_hard_link_config(max_snapshots=2)
        _, statuses = _active_statuses(config)
        statuses = {
            k: v.model_copy(update={"destination_latest_snapshot": _PREVIOUS})
            for k, v in statuses.items()
        }
        mock_rsync.return_value = _ok_proc()

        results = _run(config, statuses, dry_run=True)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path is None
        mock_cleanup.assert_not_called()
        mock_mkdir.assert_not_called()
        mock_delete.assert_not_called()
        mock_symlink.assert_not_called()
        mock_prune.assert_not_called()
        kwargs = mock_rsync.call_args.kwargs
        assert kwargs["dry_run"] is True
        assert kwargs["dest_suffix"] == "snapshots/2026-03-06T14:30:00.000Z"
        assert kwargs["link_dest"] == f"../{_PREVIOUS.name}"

    def test_dry_run_rsync_failure_removes_nothing(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="err")

        results = _run(config, statuses, dry_run=True)

        assert results[0].failure == SyncFailureKind.RSYNC
        mock_delete.assert_not_called()

    def test_real_run_names_snapshot_from_clock(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_rsync.return_value = _ok_proc()
        mock_mkdir.return_value = _NEW_SNAPSHOT

        results = _run(config, statuses)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert results[0].warnings == ()
        mock_cleanup.assert_called_once()
        assert mock_mkdir.call_args.kwargs["now"] == _FIXED_NOW
        assert mock_mkdir.call_args.kwargs["platform"] == "linux"
        mock_symlink.assert_called_once()
        mock_prune.assert_not_called()  # no max-snapshots

    def test_orphan_cleanup_failure_is_a_warning(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_cleanup.side_effect = _snapshot_error(SnapshotOp.DELETE)
        mock_rsync.return_value = _ok_proc()
        mock_mkdir.return_value = _NEW_SNAPSHOT

        results = _run(config, statuses)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.ORPHAN_CLEANUP]

    def test_mkdir_failure(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_mkdir.side_effect = _snapshot_error(SnapshotOp.MKDIR)

        results = _run(config, statuses)

        assert results[0].failure == SyncFailureKind.MKDIR
        mock_rsync.assert_not_called()

    def test_rsync_failure_removes_new_snapshot_dir(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="err")

        results = _run(config, statuses)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert results[0].rsync_exit_code == 23
        assert mock_delete.call_args.args[0] == _NEW_SNAPSHOT
        mock_symlink.assert_not_called()

    def test_snapshot_dir_removal_failure_is_a_warning(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.side_effect = OSError("rsync: not found")
        mock_delete.side_effect = _snapshot_error(SnapshotOp.DELETE)

        results = _run(config, statuses)

        assert results[0].failure == SyncFailureKind.RSYNC
        assert [w.kind for w in results[0].warnings] == [
            SyncWarningKind.SNAPSHOT_DIR_CLEANUP
        ]

    def test_symlink_failure(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        config = _make_hard_link_config()
        _, statuses = _active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.return_value = _ok_proc()
        mock_symlink.side_effect = _snapshot_error(SnapshotOp.UPDATE_LATEST)

        results = _run(config, statuses)

        assert results[0].failure == SyncFailureKind.SYMLINK

    def test_prune_failure_is_a_warning(
        self,
        mock_rsync: MagicMock,
        mock_cleanup: MagicMock,
        mock_mkdir: MagicMock,
        mock_delete: MagicMock,
        mock_symlink: MagicMock,
        mock_prune: MagicMock,
    ) -> None:
        """A complete snapshot stays a success when pruning fails."""
        config = _make_hard_link_config(max_snapshots=2)
        _, statuses = _active_statuses(config)
        mock_mkdir.return_value = _NEW_SNAPSHOT
        mock_rsync.return_value = _ok_proc()
        mock_prune.side_effect = _snapshot_error(SnapshotOp.DELETE)

        results = _run(config, statuses)

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert results[0].snapshot_path == _NEW_SNAPSHOT
        assert results[0].pruned_paths is None
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.PRUNE]


class TestPruneFailureDoesNotAbortRun:
    @patch("nbkp.sync.runner.update_latest_symlink")
    @patch("nbkp.sync.runner.btrfs_prune_snapshots")
    @patch("nbkp.sync.runner.create_snapshot")
    @patch("nbkp.sync.runner.run_rsync")
    def test_later_syncs_still_run(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = _make_btrfs_config_with_max()
        config = config.model_copy(
            update={
                "volumes": {
                    **config.volumes,
                    "src2": LocalVolume(slug="src2", path="/src2"),
                    "dst2": LocalVolume(slug="dst2", path="/dst2"),
                },
                "sync_endpoints": {
                    **config.sync_endpoints,
                    "ep-src2": SyncEndpoint(slug="ep-src2", volume="src2"),
                    "ep-dst2": SyncEndpoint(slug="ep-dst2", volume="dst2"),
                },
                "syncs": {
                    **config.syncs,
                    "s2": SyncConfig(
                        slug="s2", source="ep-src2", destination="ep-dst2"
                    ),
                },
            }
        )
        _, statuses = _active_statuses(config)
        mock_rsync.return_value = _ok_proc()
        mock_snap.return_value = "/dst/snapshots/2026-03-06T14:30:00.000Z"
        mock_prune.side_effect = _snapshot_error(SnapshotOp.DELETE)

        results = _run(config, statuses)

        assert {r.sync_slug: r.outcome for r in results} == {
            "s1": SyncOutcome.SUCCESS,
            "s2": SyncOutcome.SUCCESS,
        }


# ── Cancellation causes ──────────────────────────────────────


def _three_chain_config() -> Config:
    vols: dict[str, Volume] = {
        f"v{i}": LocalVolume(slug=f"v{i}", path=f"/v{i}") for i in range(4)
    }
    return Config(
        volumes=vols,
        sync_endpoints={
            f"ep-v{i}": SyncEndpoint(slug=f"ep-v{i}", volume=f"v{i}") for i in range(4)
        },
        syncs={
            name: SyncConfig(slug=name, source=f"ep-v{i}", destination=f"ep-v{i + 1}")
            for i, name in enumerate(["a", "b", "c"])
        },
    )


class TestCancellationCause:
    @patch("nbkp.sync.runner.run_rsync")
    def test_inactive_upstream_cancels_as_skipped(self, mock_rsync: MagicMock) -> None:
        config = _three_chain_config()
        _, statuses = _active_statuses(config)
        _, inactive = _inactive_statuses(config)
        statuses = {**statuses, "a": inactive["a"]}

        results = {r.sync_slug: r for r in _run(config, statuses)}

        assert results["a"].failure == SyncFailureKind.INACTIVE
        assert results["b"].failure == SyncFailureKind.UPSTREAM_SKIPPED
        assert results["b"].cancelled_by == "a"
        # Transitive: c is cancelled by b, whose root cause is inactivity.
        assert results["c"].failure == SyncFailureKind.UPSTREAM_SKIPPED
        assert results["c"].cancelled_by == "b"
        mock_rsync.assert_not_called()

    @patch("nbkp.sync.runner.run_rsync")
    def test_failed_upstream_cancels_as_failed_transitively(
        self, mock_rsync: MagicMock
    ) -> None:
        config = _three_chain_config()
        _, statuses = _active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="e")

        results = {r.sync_slug: r for r in _run(config, statuses)}

        assert results["b"].failure == SyncFailureKind.UPSTREAM_FAILED
        assert results["c"].failure == SyncFailureKind.UPSTREAM_FAILED
        assert results["c"].cancelled_by == "b"


# ── Strictness: ignore-all ───────────────────────────────────


def _infra_broken_statuses(
    config: Config, *, presence_proven: bool
) -> dict[str, SyncStatus]:
    """Destination snapshots/ missing (infrastructure), sentinels observed."""
    _, statuses = _active_statuses(config)

    def broken(ss: SyncStatus) -> SyncStatus:
        dst = ss.destination_endpoint_status.model_copy(
            update={
                "errors": [DestinationEndpointError.SNAPSHOTS_DIR_NOT_FOUND],
                **({} if presence_proven else {"diagnostics": None}),
            }
        )
        return ss.model_copy(
            update={
                "destination_endpoint_status": dst,
                "errors": [SyncError.DESTINATION_ENDPOINT_INACTIVE],
            }
        )

    return {k: broken(v) for k, v in statuses.items()}


class TestIgnoreAll:
    @patch("nbkp.sync.runner.run_rsync")
    def test_attempts_broken_sync_with_proven_presence(
        self, mock_rsync: MagicMock
    ) -> None:
        config = _make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=True)
        mock_rsync.return_value = _ok_proc()

        results = _run(config, statuses, strictness=Strictness.IGNORE_ALL)

        assert results[0].outcome == SyncOutcome.SUCCESS
        mock_rsync.assert_called_once()

    @patch("nbkp.sync.runner.run_rsync")
    def test_skips_broken_sync_without_proven_presence(
        self, mock_rsync: MagicMock
    ) -> None:
        config = _make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=False)

        results = _run(config, statuses, strictness=Strictness.IGNORE_ALL)

        assert results[0].failure == SyncFailureKind.PREFLIGHT
        mock_rsync.assert_not_called()

    @patch("nbkp.sync.runner.run_rsync")
    def test_never_attempts_inactive_sync(self, mock_rsync: MagicMock) -> None:
        config = _make_local_config()
        _, statuses = _inactive_statuses(config)

        results = _run(config, statuses, strictness=Strictness.IGNORE_ALL)

        assert results[0].failure == SyncFailureKind.INACTIVE
        mock_rsync.assert_not_called()

    @patch("nbkp.sync.runner.run_rsync")
    def test_broken_sync_skipped_under_ignore_inactive(
        self, mock_rsync: MagicMock
    ) -> None:
        config = _make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=True)

        results = _run(config, statuses, strictness=Strictness.IGNORE_INACTIVE)

        assert results[0].failure == SyncFailureKind.PREFLIGHT
        mock_rsync.assert_not_called()
