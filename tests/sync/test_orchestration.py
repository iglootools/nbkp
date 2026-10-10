"""Tests for nbkp.sync.orchestration: ordering, skipping, cancellation, strictness."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbkp.config import (
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.policy import Strictness
from nbkp.preflight import (
    DestinationEndpointError,
    SyncError,
    SyncStatus,
)
from nbkp.snapshots.errors import SnapshotOp
from nbkp.sync.results import SyncFailureKind, SyncOutcome
from tests.sync.runner_helpers import (
    active_statuses,
    inactive_statuses,
    make_btrfs_config_with_max,
    make_local_config,
    ok_proc,
    run_syncs,
    snapshot_error,
)


class TestRunAllSyncs:
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_successful_sync(self, mock_rsync: MagicMock) -> None:
        config = make_local_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")

        results = run_syncs(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].rsync_exit_code == 0

    def test_inactive_sync(self) -> None:
        config = make_local_config()
        _, sync_statuses = inactive_statuses(config)

        results = run_syncs(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].outcome == SyncOutcome.SKIPPED
        assert results[0].failure == SyncFailureKind.INACTIVE

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_rsync_failure(self, mock_rsync: MagicMock) -> None:
        config = make_local_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="error")

        results = run_syncs(config, sync_statuses)
        assert results[0].success is False
        assert results[0].rsync_exit_code == 23

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_filter_by_sync_slug(self, mock_rsync: MagicMock) -> None:
        config = make_local_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=0, stdout="done\n", stderr="")

        results = run_syncs(config, sync_statuses, only_syncs=["nonexistent"])
        assert len(results) == 0


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
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_downstream_sync_cancelled_on_failure(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_chain_config()
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="error")

        results = run_syncs(config, sync_statuses)
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

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_independent_sync_not_cancelled(
        self,
        mock_rsync: MagicMock,
    ) -> None:
        config = _make_independent_config()
        _, sync_statuses = active_statuses(config)

        call_count = 0

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(returncode=23, stdout="", stderr="error")
            return MagicMock(returncode=0, stdout="done\n", stderr="")

        mock_rsync.side_effect = _side_effect

        results = run_syncs(config, sync_statuses)
        assert len(results) == 2
        # One failed, one succeeded (order may vary)
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        # The failure is a real rsync failure, not a cancellation
        assert failures[0].rsync_exit_code == 23
        assert failures[0].outcome == SyncOutcome.FAILED

    @patch("nbkp.sync.modes.common.run_rsync")
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
        _, sync_statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="error")

        results = run_syncs(config, sync_statuses)
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


class TestPruneFailureDoesNotAbortRun:
    @patch("nbkp.sync.modes.common.update_latest_symlink")
    @patch("nbkp.sync.modes.btrfs.btrfs_prune_snapshots")
    @patch("nbkp.sync.modes.btrfs.create_snapshot")
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_later_syncs_still_run(
        self,
        mock_rsync: MagicMock,
        mock_snap: MagicMock,
        mock_prune: MagicMock,
        _mock_symlink: MagicMock,
    ) -> None:
        config = make_btrfs_config_with_max()
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
        _, statuses = active_statuses(config)
        mock_rsync.return_value = ok_proc()
        mock_snap.return_value = "/dst/snapshots/2026-03-06T14:30:00.000Z"
        mock_prune.side_effect = snapshot_error(SnapshotOp.DELETE)

        results = run_syncs(config, statuses)

        assert {r.sync_slug: r.outcome for r in results} == {
            "s1": SyncOutcome.SUCCESS,
            "s2": SyncOutcome.SUCCESS,
        }


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
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_inactive_upstream_cancels_as_skipped(self, mock_rsync: MagicMock) -> None:
        config = _three_chain_config()
        _, statuses = active_statuses(config)
        _, inactive = inactive_statuses(config)
        statuses = {**statuses, "a": inactive["a"]}

        results = {r.sync_slug: r for r in run_syncs(config, statuses)}

        assert results["a"].failure == SyncFailureKind.INACTIVE
        assert results["b"].failure == SyncFailureKind.UPSTREAM_SKIPPED
        assert results["b"].cancelled_by == "a"
        # Transitive: c is cancelled by b, whose root cause is inactivity.
        assert results["c"].failure == SyncFailureKind.UPSTREAM_SKIPPED
        assert results["c"].cancelled_by == "b"
        mock_rsync.assert_not_called()

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_failed_upstream_cancels_as_failed_transitively(
        self, mock_rsync: MagicMock
    ) -> None:
        config = _three_chain_config()
        _, statuses = active_statuses(config)
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="e")

        results = {r.sync_slug: r for r in run_syncs(config, statuses)}

        assert results["b"].failure == SyncFailureKind.UPSTREAM_FAILED
        assert results["c"].failure == SyncFailureKind.UPSTREAM_FAILED
        assert results["c"].cancelled_by == "b"


def _infra_broken_statuses(
    config: Config, *, presence_proven: bool
) -> dict[str, SyncStatus]:
    """Destination snapshots/ missing (infrastructure), sentinels observed."""
    _, statuses = active_statuses(config)

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
    @patch("nbkp.sync.modes.common.run_rsync")
    def test_attempts_broken_sync_with_proven_presence(
        self, mock_rsync: MagicMock
    ) -> None:
        config = make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=True)
        mock_rsync.return_value = ok_proc()

        results = run_syncs(config, statuses, strictness=Strictness.IGNORE_ALL)

        assert results[0].outcome == SyncOutcome.SUCCESS
        mock_rsync.assert_called_once()

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_skips_broken_sync_without_proven_presence(
        self, mock_rsync: MagicMock
    ) -> None:
        config = make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=False)

        results = run_syncs(config, statuses, strictness=Strictness.IGNORE_ALL)

        assert results[0].failure == SyncFailureKind.PREFLIGHT
        mock_rsync.assert_not_called()

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_never_attempts_inactive_sync(self, mock_rsync: MagicMock) -> None:
        config = make_local_config()
        _, statuses = inactive_statuses(config)

        results = run_syncs(config, statuses, strictness=Strictness.IGNORE_ALL)

        assert results[0].failure == SyncFailureKind.INACTIVE
        mock_rsync.assert_not_called()

    @patch("nbkp.sync.modes.common.run_rsync")
    def test_broken_sync_skipped_under_ignore_inactive(
        self, mock_rsync: MagicMock
    ) -> None:
        config = make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=True)

        results = run_syncs(config, statuses, strictness=Strictness.IGNORE_INACTIVE)

        assert results[0].failure == SyncFailureKind.PREFLIGHT
        mock_rsync.assert_not_called()
