"""Tests for show_all_syncs / prune_all_syncs and their outcome decisions.

Each takes the preflight statuses ``snapshots show|prune`` computed, so the
tests hand them testkit statuses for the syncs of ``tests.nas`` and script
the NAS's answers (``ls``, ``readlink``, ``btrfs``) on a FakeCommandRunner.
"""

import subprocess

from nbkp.config import Config
from nbkp.policy import Strictness
from nbkp.preflight import (
    DestinationEndpointErrorKind,
    SyncErrorKind,
    SyncStatus,
)
from nbkp.remote.resolution import resolve_all_endpoints
from nbkp.snapshots import prune_all_syncs, show_all_syncs
from nbkp.snapshots.disposition import failed_in_preflight, has_errors
from nbkp.snapshots.errors import SnapshotOp
from nbkp.snapshots.models import (
    PruneResult,
    ShowResult,
    SnapshotErrorKind,
    SnapshotResultError,
    SnapshotSkipReason,
)
from tests.clidisplay import (
    dst_ep_status,
    localhost_ssh_status,
    src_ep_status,
    vol_status,
)
from tests.fakes import FakeCommandRunner, completed
from tests.nas import nas_config

OLD = "2026-03-01T10:00:00.000Z"
MID = "2026-03-04T10:00:00.000Z"
NEW = "2026-03-06T14:30:00.000Z"
SNAPSHOTS = "/backup/photos/snapshots"


def _listing(*names: str) -> subprocess.CompletedProcess[str]:
    return completed(stdout="".join(f"{name}\n" for name in names))


def active_statuses(config: Config) -> dict[str, SyncStatus]:
    local_ssh = localhost_ssh_status()
    vols = {name: vol_status(name, config, local_ssh) for name in config.volumes}
    return {
        name: SyncStatus(
            slug=name,
            config=sync,
            source_endpoint_status=src_ep_status(
                sync.source, vols[config.sync_endpoints[sync.source].volume]
            ),
            destination_endpoint_status=dst_ep_status(
                sync.destination, vols[config.sync_endpoints[sync.destination].volume]
            ),
            errors=[],
        )
        for name, sync in config.syncs.items()
    }


def infra_error_statuses(config: Config) -> dict[str, SyncStatus]:
    """Destination snapshots/ missing: an infrastructure error, not inactivity."""
    return {
        name: ss.model_copy(
            update={
                "destination_endpoint_status": ss.destination_endpoint_status.model_copy(
                    update={
                        "errors": [DestinationEndpointErrorKind.SNAPSHOTS_DIR_NOT_FOUND]
                    }
                ),
                "errors": [SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE],
            }
        )
        for name, ss in active_statuses(config).items()
    }


def inactive_statuses(config: Config) -> dict[str, SyncStatus]:
    """Source sentinel missing: expected inactivity."""
    return {
        name: ss.model_copy(
            update={
                "source_endpoint_status": src_ep_status(
                    ss.config.source,
                    ss.source_endpoint_status.volume_status,
                    sentinel_exists=False,
                ),
                "errors": [SyncErrorKind.SOURCE_ENDPOINT_INACTIVE],
            }
        )
        for name, ss in active_statuses(config).items()
    }


_BTRFS = nas_config(snapshots="btrfs", max_snapshots=2)


def _nas_with(*listings: tuple[str, ...], latest: str = NEW) -> FakeCommandRunner:
    """``ls`` of the snapshots answers each of *listings* in turn."""
    return (
        FakeCommandRunner()
        .on("ls", returns=[_listing(*names) for names in listings])
        .on("readlink", returns=completed(stdout=f"snapshots/{latest}\n"))
    )


def _show(
    run: FakeCommandRunner,
    statuses: dict[str, SyncStatus] | None = None,
    *,
    config: Config = _BTRFS,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> list[ShowResult]:
    return show_all_syncs(
        config,
        statuses if statuses is not None else active_statuses(config),
        resolved_endpoints=resolve_all_endpoints(config),
        strictness=strictness,
        run=run,
    )


def _prune(
    run: FakeCommandRunner,
    statuses: dict[str, SyncStatus] | None = None,
    *,
    config: Config = _BTRFS,
    dry_run: bool = False,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    only_syncs: list[str] | None = None,
) -> list[PruneResult]:
    return prune_all_syncs(
        config,
        statuses if statuses is not None else active_statuses(config),
        dry_run=dry_run,
        only_syncs=only_syncs,
        resolved_endpoints=resolve_all_endpoints(config),
        strictness=strictness,
        run=run,
    )


class TestPrune:
    def test_deletes_the_oldest_beyond_the_limit(self) -> None:
        run = _nas_with((OLD, MID, NEW), (MID, NEW))

        [result] = _prune(run)

        assert result == PruneResult(
            sync_slug="photos",
            deleted=(f"{SNAPSHOTS}/{OLD}",),
            kept=2,
            dry_run=False,
        )
        assert run.commands_starting_with("btrfs") == [
            ["btrfs", "property", "set", f"{SNAPSHOTS}/{OLD}", "ro", "false"],
            ["btrfs", "subvolume", "delete", f"{SNAPSHOTS}/{OLD}"],
        ]

    def test_dry_run_deletes_nothing(self) -> None:
        run = _nas_with((OLD, MID, NEW))

        [result] = _prune(run, dry_run=True)

        assert (result.deleted, result.dry_run) == ((f"{SNAPSHOTS}/{OLD}",), True)
        assert run.commands_starting_with("btrfs") == []

    def test_dry_run_keeps_every_snapshot(self) -> None:
        """A dry run deletes nothing, so every snapshot is still kept: the
        listing already holds the would-be-deleted ones (it used to count them
        twice, 4 kept of 3)."""
        [result] = _prune(_nas_with((OLD, MID, NEW)), dry_run=True)

        assert result.kept == 3

    def test_sync_without_snapshots_is_skipped(self) -> None:
        run = FakeCommandRunner()

        [result] = _prune(run, config=nas_config())

        assert result.skip_reason is SnapshotSkipReason.NO_SNAPSHOTS
        assert run.calls == []

    def test_failed_delete_is_reported(self) -> None:
        run = _nas_with((OLD, MID, NEW)).on(
            "btrfs",
            "subvolume",
            "delete",
            returns=completed(1, stderr="Permission denied"),
        )

        [result] = _prune(run)

        assert result.skip_reason is None
        assert result.error == SnapshotResultError(
            kind=SnapshotErrorKind.OPERATION_FAILED,
            operation=SnapshotOp.DELETE,
            path=f"{SNAPSHOTS}/{OLD}",
            message="Permission denied",
        )

    def test_delete_failing_partway_reports_what_was_deleted(self) -> None:
        """With 4 snapshots and a limit of 2, the second deletion fails: the
        first is gone, and the remaining 3 are counted afresh."""
        older = "2026-02-01T10:00:00.000Z"
        run = _nas_with((older, OLD, MID, NEW), (OLD, MID, NEW)).on(
            "btrfs",
            "subvolume",
            "delete",
            returns=[completed(0), completed(1, stderr="Permission denied")],
        )

        [result] = _prune(run)

        assert result.deleted == (f"{SNAPSHOTS}/{older}",)
        assert result.kept == 3
        assert result.error == SnapshotResultError(
            kind=SnapshotErrorKind.OPERATION_FAILED,
            operation=SnapshotOp.DELETE,
            path=f"{SNAPSHOTS}/{OLD}",
            message="Permission denied",
        )

    def test_infra_errors_fail_by_default(self) -> None:
        run = FakeCommandRunner()

        [result] = _prune(run, infra_error_statuses(_BTRFS))

        assert result.skip_reason is None
        assert result.error == SnapshotResultError(
            kind=SnapshotErrorKind.PREFLIGHT_ERRORS,
            preflight_errors=(SyncErrorKind.DESTINATION_ENDPOINT_INACTIVE,),
        )
        assert run.calls == []

    def test_infra_errors_skipped_under_ignore_all(self) -> None:
        run = FakeCommandRunner()

        [result] = _prune(
            run, infra_error_statuses(_BTRFS), strictness=Strictness.IGNORE_ALL
        )

        assert result.skip_reason is SnapshotSkipReason.PREFLIGHT_ERRORS
        assert result.error is None
        assert run.calls == []

    def test_only_selected_syncs(self) -> None:
        config = nas_config(snapshots="btrfs", max_snapshots=2, syncs=("a", "b"))
        run = FakeCommandRunner()

        results = _prune(run, config=config, only_syncs=["b"])

        assert [r.sync_slug for r in results] == ["b"]
        assert all("/backup/a" not in " ".join(cmd) for cmd in run.commands)


class TestShow:
    def test_lists_the_snapshots_and_latest(self) -> None:
        [result] = _show(_nas_with((OLD, NEW)))

        assert result.sync_slug == "photos"
        assert result.snapshot_mode == "btrfs"
        assert [s.name for s in result.snapshots] == [OLD, NEW]
        assert result.latest is not None and result.latest.name == NEW
        assert result.max_snapshots == 2
        assert (result.skip_reason, result.error) == (None, None)

    def test_sync_without_snapshots_is_skipped(self) -> None:
        run = FakeCommandRunner()

        [result] = _show(run, config=nas_config())

        assert result.skip_reason is SnapshotSkipReason.NO_SNAPSHOTS
        assert run.calls == []

    def test_inactive_sync_is_skipped(self) -> None:
        run = FakeCommandRunner()

        [result] = _show(run, inactive_statuses(_BTRFS))

        assert result.skip_reason is SnapshotSkipReason.INACTIVE
        assert result.skipped
        assert run.calls == []

    def test_inactive_sync_fails_under_ignore_none(self) -> None:
        [result] = _show(
            FakeCommandRunner(),
            inactive_statuses(_BTRFS),
            strictness=Strictness.IGNORE_NONE,
        )

        assert result.error == SnapshotResultError(
            kind=SnapshotErrorKind.INACTIVE,
            preflight_errors=(SyncErrorKind.SOURCE_ENDPOINT_INACTIVE,),
        )

    def test_disabled_sync_skipped_under_ignore_none(self) -> None:
        """A disabled sync is never fatal, under any strictness."""
        disabled = {
            name: status.model_copy(update={"errors": [SyncErrorKind.DISABLED]})
            for name, status in active_statuses(_BTRFS).items()
        }

        [result] = _show(
            FakeCommandRunner(), disabled, strictness=Strictness.IGNORE_NONE
        )

        assert result.skip_reason is SnapshotSkipReason.INACTIVE
        assert result.error is None

    def test_infra_errors_fail(self) -> None:
        """A sync broken by infrastructure errors is not silently skipped."""
        [result] = _show(FakeCommandRunner(), infra_error_statuses(_BTRFS))

        assert result.skip_reason is None
        assert result.error is not None
        assert result.error.kind is SnapshotErrorKind.PREFLIGHT_ERRORS

    def test_listing_failure_is_reported(self) -> None:
        run = FakeCommandRunner().on("ls", returns=OSError("connection failed"))

        [result] = _show(run)

        assert result.error == SnapshotResultError(
            kind=SnapshotErrorKind.OPERATION_FAILED,
            operation=SnapshotOp.LIST,
            path=SNAPSHOTS,
            message="connection failed",
        )


class TestOutcome:
    def _active(self) -> dict[str, SyncStatus]:
        return active_statuses(_BTRFS)

    def test_no_errors(self) -> None:
        results = [
            ShowResult(
                sync_slug="photos",
                snapshot_mode="btrfs",
                snapshots=(),
                latest=None,
                max_snapshots=None,
            )
        ]
        assert not has_errors(results)
        assert not failed_in_preflight(results, self._active())

    def test_preflight_errors_point_at_troubleshoot(self) -> None:
        statuses = infra_error_statuses(_BTRFS)
        [result] = _show(FakeCommandRunner(), statuses)

        assert has_errors([result])
        assert failed_in_preflight([result], statuses)

    def test_failed_operation_is_not_a_preflight_failure(self) -> None:
        """Troubleshoot explains preflight findings, not a failed delete."""
        failed = PruneResult(
            sync_slug="photos",
            deleted=(),
            kept=0,
            dry_run=False,
            error=SnapshotResultError(
                kind=SnapshotErrorKind.OPERATION_FAILED,
                operation=SnapshotOp.DELETE,
                path=f"{SNAPSHOTS}/{OLD}",
                message="Permission denied",
            ),
        )

        assert has_errors([failed])
        assert not failed_in_preflight([failed], self._active())
