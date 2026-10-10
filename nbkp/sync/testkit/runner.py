"""Fake sync and prune result builders for manual testing."""

from __future__ import annotations

from ...config import Config
from ...fsprotocol import SNAPSHOTS_DIR
from ...snapshots.models import PruneResult
from ..runner import (
    SyncFailureKind,
    SyncOutcome,
    SyncResult,
    SyncWarning,
    SyncWarningKind,
)


def _snap_base(config: Config, sync_slug: str) -> str:
    sync = config.syncs[sync_slug]
    dst_ep = config.destination_endpoint(sync)
    vol = config.volumes[dst_ep.volume]
    base = vol.path
    if dst_ep.subdir:
        base = f"{base}/{dst_ep.subdir}"
    return f"{base}/{SNAPSHOTS_DIR}"


def run_results(config: Config) -> list[SyncResult]:
    """Sync results: success, success+snapshot (local & remote), failure."""
    local_snap_base = _snap_base(config, "photos-to-usb")
    local_snap = f"{local_snap_base}/2026-02-19T10-30-00.000Z"
    _docs_src = config.source_endpoint(config.syncs["docs-to-nas"])
    src_vol = config.volumes[_docs_src.volume]
    src_subdir = _docs_src.subdir
    return [
        SyncResult(
            sync_slug="music-to-usb",
            outcome=SyncOutcome.SUCCESS,
            dry_run=False,
            rsync_exit_code=0,
            output="",
        ),
        SyncResult(
            sync_slug="photos-to-usb",
            outcome=SyncOutcome.SUCCESS,
            dry_run=False,
            rsync_exit_code=0,
            output="",
            snapshot_path=local_snap,
            pruned_paths=(
                f"{local_snap_base}/2026-02-01T08-00-00.000Z",
                f"{local_snap_base}/2026-02-10T12-00-00.000Z",
            ),
            warnings=(
                SyncWarning(
                    kind=SyncWarningKind.ORPHAN_CLEANUP,
                    message=(
                        "Orphaned snapshot cleanup failed: delete snapshot failed"
                        f" for {local_snap_base}/2026-02-18T10-30-00.000Z:"
                        " Permission denied"
                    ),
                ),
            ),
        ),
        SyncResult(
            sync_slug="docs-to-nas",
            outcome=SyncOutcome.FAILED,
            dry_run=False,
            rsync_exit_code=23,
            output=(
                "rsync: [sender] link_stat"
                f' "{src_vol.path}/{src_subdir}" failed:'
                " No such file or directory (2)\n"
                "rsync error: some files/attrs"
                " were not transferred (code 23)\n"
            ),
            failure=SyncFailureKind.RSYNC,
            detail="rsync exited with code 23",
        ),
    ]


def dry_run_results(config: Config) -> list[SyncResult]:
    """Same results as run_results but flagged as dry run."""
    return [r.model_copy(update={"dry_run": True}) for r in run_results(config)]


def prune_results(config: Config) -> list[PruneResult]:
    """Prune results: success, noop, error."""
    snap_base = _snap_base(config, "photos-to-usb")
    return [
        PruneResult(
            sync_slug="photos-to-usb",
            deleted=(
                f"{snap_base}/2026-01-01T00-00-00.000Z",
                f"{snap_base}/2026-01-15T00-00-00.000Z",
                f"{snap_base}/2026-02-01T00-00-00.000Z",
            ),
            kept=7,
            dry_run=False,
        ),
        PruneResult(
            sync_slug="music-to-usb",
            deleted=(),
            kept=5,
            dry_run=False,
        ),
        PruneResult(
            sync_slug="docs-to-nas",
            deleted=(),
            kept=0,
            dry_run=False,
            error=(
                "delete snapshot failed for"
                f" {_snap_base(config, 'docs-to-nas')}/2026-01-01T00:00:00.000Z:"
                " Permission denied"
            ),
        ),
    ]


def prune_dry_run_results(
    config: Config,
) -> list[PruneResult]:
    """Prune dry-run results."""
    snap_base = _snap_base(config, "photos-to-usb")
    return [
        PruneResult(
            sync_slug="photos-to-usb",
            deleted=(
                f"{snap_base}/2026-01-01T00-00-00.000Z",
                f"{snap_base}/2026-01-15T00-00-00.000Z",
            ),
            kept=10,
            dry_run=True,
        ),
    ]
