"""Tests for nbkp.sync.orchestration: ordering, skipping, cancellation, strictness."""

import pytest

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    LocalVolume,
    RsyncOptions,
    SyncConfig,
    SyncEndpoint,
    Volume,
)
from nbkp.policy import Strictness
from nbkp.preflight import (
    DestinationEndpointErrorKind,
    SyncErrorKind,
    SyncStatus,
)
from nbkp.sync.results import SyncFailureKind, SyncOutcome, SyncWarningKind
from tests.fakes import (
    FakeCommandRunner,
    FakeRsync,
    UnexpectedCommandError,
    completed,
)
from tests.sync.runner_helpers import (
    NEW_SNAPSHOT_NAME,
    active_statuses,
    inactive_statuses,
    make_local_config,
    make_snapshot_config,
    run_syncs,
)


class TestRunAllSyncs:
    def test_successful_sync(self) -> None:
        config = make_local_config()
        _, sync_statuses = active_statuses(config)
        rsync = FakeRsync()

        results = run_syncs(config, sync_statuses, rsync=rsync)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].rsync_exit_code == 0

    def test_attempted_sync_without_fakes_fails_the_test(self) -> None:
        """run_syncs never falls back to the real rsync: a stray sync must fail."""
        config = make_local_config()
        _, sync_statuses = active_statuses(config)

        with pytest.raises(UnexpectedCommandError):
            run_syncs(config, sync_statuses)

    def test_inactive_sync(self) -> None:
        config = make_local_config()
        _, sync_statuses = inactive_statuses(config)

        results = run_syncs(config, sync_statuses)
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].outcome == SyncOutcome.SKIPPED
        assert results[0].failure == SyncFailureKind.INACTIVE

    def test_skipped_sync_carries_its_preflight_errors_as_kinds(self) -> None:
        """The reason leaves the core as error kinds, worded at the edge."""
        config = make_local_config()
        _, sync_statuses = inactive_statuses(config)

        [result] = run_syncs(config, sync_statuses)

        assert result.preflight_errors == (
            SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,
            SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,
        )
        assert result.detail is None
        assert result.model_dump(mode="json")["preflight_errors"] == [
            e.value for e in result.preflight_errors
        ]

    def test_rsync_failure(self) -> None:
        config = make_local_config()
        _, sync_statuses = active_statuses(config)
        rsync = FakeRsync([completed(23, stderr="error")])

        results = run_syncs(config, sync_statuses, rsync=rsync)
        assert results[0].success is False
        assert results[0].rsync_exit_code == 23

    def test_vanished_files_warn_succeeds_with_a_warning(self) -> None:
        config = make_local_config()
        sync = config.syncs["s1"].model_copy(
            update={"rsync_options": RsyncOptions(vanished_files="warn")},
        )
        config = config.model_copy(update={"syncs": {"s1": sync}})
        _, sync_statuses = active_statuses(config)

        [result] = run_syncs(
            config,
            sync_statuses,
            rsync=FakeRsync([completed(24)]),
        )

        assert result.outcome == SyncOutcome.SUCCESS
        assert [w.kind for w in result.warnings] == [SyncWarningKind.VANISHED_FILES]

    def test_filter_by_sync_slug(self) -> None:
        config = make_local_config()
        _, sync_statuses = active_statuses(config)
        rsync = FakeRsync()

        results = run_syncs(
            config,
            sync_statuses,
            rsync=rsync,
            only_syncs=["nonexistent"],
        )
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
    def test_downstream_sync_cancelled_on_failure(self) -> None:
        config = _make_chain_config()
        _, sync_statuses = active_statuses(config)
        rsync = FakeRsync([completed(23, stderr="error")])

        results = run_syncs(config, sync_statuses, rsync=rsync)
        assert len(results) == 2

        r1 = next(r for r in results if r.sync_slug == "s1")
        assert r1.success is False
        assert r1.outcome == SyncOutcome.FAILED
        assert r1.rsync_exit_code == 23

        r2 = next(r for r in results if r.sync_slug == "s2")
        assert r2.success is False
        assert r2.outcome == SyncOutcome.CANCELLED
        assert r2.failure == SyncFailureKind.UPSTREAM_FAILED
        assert r2.cancelled_by == "s1"

    def test_independent_sync_not_cancelled(self) -> None:
        config = _make_independent_config()
        _, sync_statuses = active_statuses(config)

        rsync = FakeRsync([completed(23, stderr="error"), completed(0, "done\n")])

        results = run_syncs(config, sync_statuses, rsync=rsync)
        assert len(results) == 2
        # Independent syncs, so the result order is not fixed.
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        # The failure is a real rsync failure, not a cancellation
        assert failures[0].rsync_exit_code == 23
        assert failures[0].outcome == SyncOutcome.FAILED

    def test_transitive_cancellation(self) -> None:
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
        rsync = FakeRsync([completed(23, stderr="error")])

        results = run_syncs(config, sync_statuses, rsync=rsync)
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
    def test_later_syncs_still_run(self) -> None:
        config = make_snapshot_config(
            btrfs=BtrfsSnapshotConfig(enabled=True, max_snapshots=1),
        )
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
                        slug="s2",
                        source="ep-src2",
                        destination="ep-dst2",
                    ),
                },
            },
        )
        _, statuses = active_statuses(config)
        rsync = FakeRsync()
        runner = (
            FakeCommandRunner()
            .on(
                "ls",
                returns=completed(
                    0,
                    f"2026-03-05T10:00:00.000Z\n{NEW_SNAPSHOT_NAME}\n",
                ),
            )
            .on("readlink", returns=completed(0, f"snapshots/{NEW_SNAPSHOT_NAME}\n"))
            .on("btrfs", "property", returns=completed(1, stderr="Permission denied"))
        )

        results = run_syncs(config, statuses, rsync=rsync, run_command=runner)

        assert {r.sync_slug: r.outcome for r in results} == {
            "s1": SyncOutcome.SUCCESS,
            "s2": SyncOutcome.SUCCESS,
        }
        assert [w.kind for w in results[0].warnings] == [SyncWarningKind.PRUNE]
        assert rsync.synced == ["s1", "s2"]


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
    def test_inactive_upstream_cancels_as_skipped(self) -> None:
        config = _three_chain_config()
        _, statuses = active_statuses(config)
        _, inactive = inactive_statuses(config)
        statuses = {**statuses, "a": inactive["a"]}
        rsync = FakeRsync()

        results = {r.sync_slug: r for r in run_syncs(config, statuses, rsync=rsync)}

        assert results["a"].failure == SyncFailureKind.INACTIVE
        assert results["b"].failure == SyncFailureKind.UPSTREAM_SKIPPED
        assert results["b"].cancelled_by == "a"
        # Transitive: c is cancelled by b, whose root cause is inactivity.
        assert results["c"].failure == SyncFailureKind.UPSTREAM_SKIPPED
        assert results["c"].cancelled_by == "b"
        assert rsync.calls == []

    def test_disabled_upstream_cancels_as_disabled(self) -> None:
        config = _three_chain_config()
        _, statuses = active_statuses(config)
        _, inactive = inactive_statuses(config)
        disabled = inactive["a"].model_copy(update={"errors": [SyncErrorKind.DISABLED]})
        statuses = {**statuses, "a": disabled}
        rsync = FakeRsync()

        results = {r.sync_slug: r for r in run_syncs(config, statuses, rsync=rsync)}

        assert results["a"].failure == SyncFailureKind.DISABLED
        assert results["b"].failure == SyncFailureKind.UPSTREAM_DISABLED
        assert results["b"].cancelled_by == "a"
        assert results["b"].detail == "Cancelled: upstream sync 'a' is disabled"
        # Transitive: c is cancelled by b, whose root cause is the disabled a.
        assert results["c"].failure == SyncFailureKind.UPSTREAM_DISABLED
        assert results["c"].cancelled_by == "b"
        assert rsync.calls == []

    def test_failed_upstream_cancels_as_failed_transitively(self) -> None:
        config = _three_chain_config()
        _, statuses = active_statuses(config)
        rsync = FakeRsync([completed(23, stderr="e")])

        results = {r.sync_slug: r for r in run_syncs(config, statuses, rsync=rsync)}

        assert results["b"].failure == SyncFailureKind.UPSTREAM_FAILED
        assert results["c"].failure == SyncFailureKind.UPSTREAM_FAILED
        assert results["c"].cancelled_by == "b"


def _infra_broken_statuses(
    config: Config,
    *,
    presence_proven: bool,
) -> dict[str, SyncStatus]:
    """Destination snapshots/ missing (infrastructure), sentinels observed."""
    _, statuses = active_statuses(config)

    def broken(ss: SyncStatus) -> SyncStatus:
        dst = ss.destination_endpoint_status.model_copy(
            update={
                "errors": [DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND],
                **({} if presence_proven else {"diagnostics": None}),
            },
        )
        return ss.model_copy(
            update={
                "destination_endpoint_status": dst,
                "errors": [SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
            },
        )

    return {k: broken(v) for k, v in statuses.items()}


class TestIgnoreAll:
    def test_attempts_broken_sync_with_proven_presence(self) -> None:
        config = make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=True)
        rsync = FakeRsync()

        results = run_syncs(
            config,
            statuses,
            rsync=rsync,
            strictness=Strictness.IGNORE_ALL,
        )

        assert results[0].outcome == SyncOutcome.SUCCESS
        assert len(rsync.calls) == 1

    def test_skips_broken_sync_without_proven_presence(self) -> None:
        config = make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=False)
        rsync = FakeRsync()

        results = run_syncs(
            config,
            statuses,
            rsync=rsync,
            strictness=Strictness.IGNORE_ALL,
        )

        assert results[0].failure == SyncFailureKind.PREFLIGHT
        assert rsync.calls == []

    def test_never_attempts_inactive_sync(self) -> None:
        config = make_local_config()
        _, statuses = inactive_statuses(config)
        rsync = FakeRsync()

        results = run_syncs(
            config,
            statuses,
            rsync=rsync,
            strictness=Strictness.IGNORE_ALL,
        )

        assert results[0].failure == SyncFailureKind.INACTIVE
        assert rsync.calls == []

    def test_broken_sync_skipped_under_ignore_inactive(self) -> None:
        config = make_local_config()
        statuses = _infra_broken_statuses(config, presence_proven=True)
        rsync = FakeRsync()

        results = run_syncs(
            config,
            statuses,
            rsync=rsync,
            strictness=Strictness.IGNORE_INACTIVE,
        )

        assert results[0].failure == SyncFailureKind.PREFLIGHT
        assert rsync.calls == []


def _pending_middle_statuses(config: Config) -> dict[str, SyncStatus]:
    """
    Return statuses for ``a → b → c`` where ``b`` is pending in a dry run.

    ``b`` reads a snapshot ``a`` would create: preflight observed every sentinel but
    flags ``b`` pending.
    """
    _, statuses = active_statuses(config)
    pending = statuses["b"].model_copy(
        update={"errors": [SyncErrorKind.DRY_RUN_SRC_EP_SNAPSHOT_PENDING]},
    )
    return {**statuses, "b": pending}


class TestDryRunPending:
    def test_pending_is_skipped_without_cancelling_downstream(self) -> None:
        """A real run would give ``b`` its snapshot, so ``c`` still runs."""
        config = _three_chain_config()
        rsync = FakeRsync()

        results = {
            r.sync_slug: r
            for r in run_syncs(
                config,
                _pending_middle_statuses(config),
                dry_run=True,
                rsync=rsync,
            )
        }

        assert results["a"].outcome == SyncOutcome.SUCCESS
        assert results["b"].outcome == SyncOutcome.SKIPPED
        assert results["b"].failure == SyncFailureKind.DRY_RUN_PENDING
        assert results["c"].outcome == SyncOutcome.SUCCESS
        assert rsync.synced == ["a", "c"]

    def test_pending_is_never_attempted_under_ignore_all(self) -> None:
        """Its sentinels were observed, but there is no snapshot to read yet."""
        config = _three_chain_config()
        rsync = FakeRsync()

        results = {
            r.sync_slug: r
            for r in run_syncs(
                config,
                _pending_middle_statuses(config),
                dry_run=True,
                strictness=Strictness.IGNORE_ALL,
                rsync=rsync,
            )
        }

        assert results["b"].failure == SyncFailureKind.DRY_RUN_PENDING
        assert results["c"].outcome == SyncOutcome.SUCCESS
        assert rsync.synced == ["a", "c"]
