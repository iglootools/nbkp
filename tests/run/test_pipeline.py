"""Tests for the check-then-run pipeline's strictness semantics.

:func:`run_checked` takes the preflight result :func:`check_and_run` computed,
so these tests hand it testkit statuses; the CLI tests drive the whole
pipeline against fake hosts.
"""

from datetime import UTC, datetime

import pytest

from nbkp.config import Config, LocalVolume, SyncConfig, SyncEndpoint, Volume
from nbkp.disks import UmountResult
from nbkp.policy import Strictness
from nbkp.preflight import (
    DestinationEndpointErrorKind,
    SyncErrorKind,
    SyncStatus,
)
from nbkp.run.pipeline import (
    PipelineResult,
    fatal_sync_errors,
    run_checked,
    run_failed,
)
from nbkp.sync import RunOptions, SyncFailureKind, SyncOutcome
from tests.fakes import FakeRsync, completed
from tests.fixtures.preflight import (
    dst_ep_status,
    localhost_ssh_status,
    preflight,
    src_ep_status,
    vol_status,
)


def _chain_config() -> Config:
    """``a`` (v0 → v1) feeds ``b`` (v1 → v2)."""
    vols: dict[str, Volume] = {
        f"v{i}": LocalVolume(slug=f"v{i}", path=f"/v{i}") for i in range(3)
    }
    return Config(
        volumes=vols,
        sync_endpoints={
            f"ep-v{i}": SyncEndpoint(slug=f"ep-v{i}", volume=f"v{i}") for i in range(3)
        },
        syncs={
            "a": SyncConfig(slug="a", source="ep-v0", destination="ep-v1"),
            "b": SyncConfig(slug="b", source="ep-v1", destination="ep-v2"),
        },
    )


def _status(config: Config, slug: str, *, src_sentinel: bool = True) -> SyncStatus:
    ssh = localhost_ssh_status()
    sync = config.syncs[slug]
    src_ep = config.sync_endpoints[sync.source]
    dst_ep = config.sync_endpoints[sync.destination]
    src = src_ep_status(
        src_ep.slug,
        vol_status(src_ep.volume, config, ssh),
        sentinel_exists=src_sentinel,
    )
    return SyncStatus(
        slug=slug,
        config=sync,
        source_endpoint_status=src,
        destination_endpoint_status=dst_ep_status(
            dst_ep.slug, vol_status(dst_ep.volume, config, ssh)
        ),
        errors=[] if src_sentinel else [SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
    )


def _infra_broken(status: SyncStatus) -> SyncStatus:
    """Destination not writable: infrastructure, sentinels observed."""
    dst = status.destination_endpoint_status.model_copy(
        update={"errors": [DestinationEndpointErrorKind.NOT_WRITABLE]}
    )
    return status.model_copy(
        update={
            "destination_endpoint_status": dst,
            "errors": [SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
        }
    )


def _run(
    config: Config,
    statuses: dict[str, SyncStatus],
    strictness: Strictness,
    rsync: FakeRsync,
) -> PipelineResult:
    return run_checked(
        config,
        preflight({}, statuses),
        RunOptions(
            clock=lambda: datetime(2026, 3, 6, tzinfo=UTC),
            platform="linux",
            strictness=strictness,
            rsync=rsync,
        ),
    )


class TestIgnoreInactiveCancellations:
    def test_cancelled_by_inactive_upstream_is_expected(self) -> None:
        """Docs: exit 0 if only inactive syncs were skipped — cascades included."""
        config = _chain_config()
        statuses = {
            "a": _status(config, "a", src_sentinel=False),
            "b": _status(config, "b"),
        }

        rsync = FakeRsync()
        pipeline = _run(config, statuses, Strictness.IGNORE_INACTIVE, rsync)

        outcomes = {r.sync_slug: r.failure for r in pipeline.results}
        assert outcomes == {
            "a": SyncFailureKind.INACTIVE,
            "b": SyncFailureKind.UPSTREAM_SKIPPED,
        }
        assert not pipeline.has_preflight_errors
        assert not pipeline.has_sync_failures
        assert rsync.calls == []

    def test_cancelled_by_failed_upstream_is_a_failure(self) -> None:
        config = _chain_config()
        statuses = {"a": _status(config, "a"), "b": _status(config, "b")}
        rsync = FakeRsync([completed(23, stderr="err")])

        pipeline = _run(config, statuses, Strictness.IGNORE_INACTIVE, rsync)

        outcomes = {r.sync_slug: r.failure for r in pipeline.results}
        assert outcomes == {
            "a": SyncFailureKind.RSYNC,
            "b": SyncFailureKind.UPSTREAM_FAILED,
        }
        assert pipeline.has_sync_failures


class TestIgnoreAll:
    def test_attempts_infra_broken_sync(self) -> None:
        config = _chain_config()
        statuses = {
            "a": _infra_broken(_status(config, "a")),
            "b": _status(config, "b"),
        }
        rsync = FakeRsync()

        pipeline = _run(config, statuses, Strictness.IGNORE_ALL, rsync)

        assert [r.outcome for r in pipeline.results] == [
            SyncOutcome.SUCCESS,
            SyncOutcome.SUCCESS,
        ]
        assert not pipeline.has_preflight_errors
        assert not pipeline.has_sync_failures
        assert rsync.synced == ["a", "b"]

    def test_execution_failure_still_fails(self) -> None:
        config = _chain_config()
        statuses = {
            "a": _infra_broken(_status(config, "a")),
            "b": _status(config, "b"),
        }
        rsync = FakeRsync([completed(11, stderr="err")])

        pipeline = _run(config, statuses, Strictness.IGNORE_ALL, rsync)

        assert pipeline.has_sync_failures

    def test_inactive_skip_is_not_a_failure(self) -> None:
        config = _chain_config()
        statuses = {
            "a": _status(config, "a", src_sentinel=False),
            "b": _status(config, "b"),
        }

        rsync = FakeRsync()
        pipeline = _run(config, statuses, Strictness.IGNORE_ALL, rsync)

        assert not pipeline.has_sync_failures
        assert rsync.calls == []


class TestPreflightAbort:
    @pytest.mark.parametrize(
        ("strictness", "aborts"),
        [
            (Strictness.IGNORE_NONE, True),
            (Strictness.IGNORE_INACTIVE, True),
            (Strictness.IGNORE_ALL, False),
        ],
    )
    def test_infra_errors(self, strictness: Strictness, aborts: bool) -> None:
        config = _chain_config()
        statuses = {
            "a": _infra_broken(_status(config, "a")),
            "b": _status(config, "b"),
        }
        rsync = FakeRsync()

        pipeline = _run(config, statuses, strictness, rsync)

        assert pipeline.has_preflight_errors is aborts
        assert (rsync.synced == []) is aborts

    def test_abort_reports_no_sync_failure(self) -> None:
        """No sync ran, so none failed: the abort is has_preflight_errors."""
        config = _chain_config()
        statuses = {"a": _infra_broken(_status(config, "a")), "b": _status(config, "b")}

        pipeline = _run(config, statuses, Strictness.IGNORE_INACTIVE, FakeRsync())

        assert pipeline.has_preflight_errors
        assert pipeline.results == []
        assert not pipeline.has_sync_failures


class TestFatalSyncErrors:
    def test_lists_only_the_syncs_fatal_under_the_strictness(self) -> None:
        config = _chain_config()
        statuses = {
            "a": _status(config, "a", src_sentinel=False),
            "b": _infra_broken(_status(config, "b")),
        }

        assert fatal_sync_errors(statuses, Strictness.IGNORE_INACTIVE) == {
            "b": [SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE]
        }
        assert fatal_sync_errors(statuses, Strictness.IGNORE_NONE) == {
            "a": [SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
            "b": [SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
        }

    def test_a_disabled_sync_is_never_fatal(self) -> None:
        """Under ignore-none an inactive sync aborts the run, but a disabled
        one must not be reported as offending."""
        config = _chain_config()
        disabled = _status(config, "a").model_copy(
            update={"errors": [SyncErrorKind.DISABLED]}
        )

        assert fatal_sync_errors({"a": disabled}, Strictness.IGNORE_NONE) == {}


class TestRunFailed:
    def _pipeline(
        self, *, aborted: bool = False, failed: bool = False
    ) -> PipelineResult:
        return PipelineResult(
            preflight=preflight({}, {}),
            results=[],
            has_preflight_errors=aborted,
            has_sync_failures=failed,
        )

    def test_a_clean_run_succeeds(self) -> None:
        ok = UmountResult("enc", success=True)
        assert not run_failed(self._pipeline(), [ok])

    @pytest.mark.parametrize(("aborted", "failed"), [(True, True), (False, True)])
    def test_abort_or_sync_failure_fails(self, aborted: bool, failed: bool) -> None:
        assert run_failed(self._pipeline(aborted=aborted, failed=failed), [])

    def test_a_volume_left_mounted_fails_a_successful_run(self) -> None:
        stuck = UmountResult("enc", success=False)
        assert run_failed(self._pipeline(), [stuck])
