"""Tests for the check-then-run pipeline's strictness semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from nbkp.config import Config, LocalVolume, SyncConfig, SyncEndpoint, Volume
from nbkp.preflight import (
    DestinationEndpointError,
    SyncError,
    SyncStatus,
)
from nbkp.run.pipeline import PipelineResult, Strictness, check_and_run
from nbkp.sync import SyncFailureKind, SyncOutcome
from tests.clihelpers import (
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
        errors=[] if src_sentinel else [SyncError.SOURCE_ENDPOINT_INACTIVE],
    )


def _infra_broken(status: SyncStatus) -> SyncStatus:
    """Destination not writable: infrastructure, sentinels observed."""
    dst = status.destination_endpoint_status.model_copy(
        update={"errors": [DestinationEndpointError.NOT_WRITABLE]}
    )
    return status.model_copy(
        update={
            "destination_endpoint_status": dst,
            "errors": [SyncError.DESTINATION_ENDPOINT_INACTIVE],
        }
    )


def _run(
    config: Config,
    statuses: dict[str, SyncStatus],
    strictness: Strictness,
    mock_checks: MagicMock,
) -> PipelineResult:
    mock_checks.return_value = preflight({}, statuses)
    return check_and_run(
        config,
        clock=lambda: datetime(2026, 3, 6, tzinfo=UTC),
        platform="linux",
        strictness=strictness,
    )


@patch("nbkp.sync.runner.run_rsync")
@patch("nbkp.run.pipeline.check_all_syncs")
class TestIgnoreInactiveCancellations:
    def test_cancelled_by_inactive_upstream_is_expected(
        self, mock_checks: MagicMock, mock_rsync: MagicMock
    ) -> None:
        """Docs: exit 0 if only inactive syncs were skipped — cascades included."""
        config = _chain_config()
        statuses = {
            "a": _status(config, "a", src_sentinel=False),
            "b": _status(config, "b"),
        }

        pipeline = _run(config, statuses, Strictness.IGNORE_INACTIVE, mock_checks)

        outcomes = {r.sync_slug: r.failure for r in pipeline.results}
        assert outcomes == {
            "a": SyncFailureKind.INACTIVE,
            "b": SyncFailureKind.UPSTREAM_SKIPPED,
        }
        assert not pipeline.has_preflight_errors
        assert not pipeline.has_sync_failures
        mock_rsync.assert_not_called()

    def test_cancelled_by_failed_upstream_is_a_failure(
        self, mock_checks: MagicMock, mock_rsync: MagicMock
    ) -> None:
        config = _chain_config()
        statuses = {"a": _status(config, "a"), "b": _status(config, "b")}
        mock_rsync.return_value = MagicMock(returncode=23, stdout="", stderr="err")

        pipeline = _run(config, statuses, Strictness.IGNORE_INACTIVE, mock_checks)

        outcomes = {r.sync_slug: r.failure for r in pipeline.results}
        assert outcomes == {
            "a": SyncFailureKind.RSYNC,
            "b": SyncFailureKind.UPSTREAM_FAILED,
        }
        assert pipeline.has_sync_failures


@patch("nbkp.sync.runner.run_rsync")
@patch("nbkp.run.pipeline.check_all_syncs")
class TestIgnoreAll:
    def test_attempts_infra_broken_sync(
        self, mock_checks: MagicMock, mock_rsync: MagicMock
    ) -> None:
        config = _chain_config()
        statuses = {
            "a": _infra_broken(_status(config, "a")),
            "b": _status(config, "b"),
        }
        mock_rsync.return_value = MagicMock(returncode=0, stdout="", stderr="")

        pipeline = _run(config, statuses, Strictness.IGNORE_ALL, mock_checks)

        assert [r.outcome for r in pipeline.results] == [
            SyncOutcome.SUCCESS,
            SyncOutcome.SUCCESS,
        ]
        assert not pipeline.has_preflight_errors
        assert not pipeline.has_sync_failures
        assert mock_rsync.call_count == 2

    def test_execution_failure_still_fails(
        self, mock_checks: MagicMock, mock_rsync: MagicMock
    ) -> None:
        config = _chain_config()
        statuses = {
            "a": _infra_broken(_status(config, "a")),
            "b": _status(config, "b"),
        }
        mock_rsync.return_value = MagicMock(returncode=11, stdout="", stderr="err")

        pipeline = _run(config, statuses, Strictness.IGNORE_ALL, mock_checks)

        assert pipeline.has_sync_failures

    def test_inactive_skip_is_not_a_failure(
        self, mock_checks: MagicMock, mock_rsync: MagicMock
    ) -> None:
        config = _chain_config()
        statuses = {
            "a": _status(config, "a", src_sentinel=False),
            "b": _status(config, "b"),
        }

        pipeline = _run(config, statuses, Strictness.IGNORE_ALL, mock_checks)

        assert not pipeline.has_sync_failures
        mock_rsync.assert_not_called()


@patch("nbkp.run.pipeline.run_all_syncs")
@patch("nbkp.run.pipeline.check_all_syncs")
class TestPreflightAbort:
    @pytest.mark.parametrize(
        ("strictness", "aborts"),
        [
            (Strictness.IGNORE_NONE, True),
            (Strictness.IGNORE_INACTIVE, True),
            (Strictness.IGNORE_ALL, False),
        ],
    )
    def test_infra_errors(
        self,
        mock_checks: MagicMock,
        mock_run: MagicMock,
        strictness: Strictness,
        aborts: bool,
    ) -> None:
        config = _chain_config()
        statuses = {
            "a": _infra_broken(_status(config, "a")),
            "b": _status(config, "b"),
        }
        mock_run.return_value = []

        pipeline = _run(config, statuses, strictness, mock_checks)

        assert pipeline.has_preflight_errors is aborts
        assert mock_run.called is not aborts
