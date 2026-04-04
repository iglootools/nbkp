"""Backup run orchestration.

Composes mount lifecycle with the check-and-run sync pipeline:

* ``mount_and_run`` — mount + check-and-run + umount in one call
"""

from __future__ import annotations

from typing import Callable

from ..config import Config
from ..config.epresolution import ResolvedEndpoints
from ..disks.context import managed_mount
from ..disks.lifecycle import MountResult, UmountResult
from ..preflight import PreflightResult
from ..sync.pipeline import Strictness, PipelineResult, check_and_run
from ..sync.rsync import ProgressMode
from ..sync.runner import SyncResult


def mount_and_run(
    config: Config,
    resolved: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    # Mount options
    mount: bool = True,
    umount: bool = True,
    names: list[str] | None = None,
    on_mount_start: Callable[[str], None] | None = None,
    on_mount_end: Callable[[str, MountResult], None] | None = None,
    on_umount_start: Callable[[str], None] | None = None,
    on_umount_end: Callable[[str, UmountResult], None] | None = None,
    # check_and_run options
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_check_start: Callable[[str], None] | None = None,
    on_check_end: Callable[[str, bool, str | None], None] | None = None,
    on_checks_done: Callable[[PreflightResult], None] | None = None,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
) -> PipelineResult:
    """Mount volumes, run the check-and-run pipeline, then umount.

    Combines :func:`~nbkp.disks.context.managed_mount` with
    :func:`~nbkp.sync.pipeline.check_and_run` into a single call.
    Mount observations are automatically forwarded to preflight checks
    so they don't re-probe device/mount state.
    """
    with managed_mount(
        config,
        resolved,
        passphrase_fn,
        mount=mount,
        umount=umount,
        names=names,
        on_mount_start=on_mount_start,
        on_mount_end=on_mount_end,
        on_umount_start=on_umount_start,
        on_umount_end=on_umount_end,
    ) as (_, mount_observations):
        return check_and_run(
            config,
            strictness=strictness,
            dry_run=dry_run,
            only_syncs=only_syncs,
            progress=progress,
            prune=prune,
            on_check_start=on_check_start,
            on_check_end=on_check_end,
            on_checks_done=on_checks_done,
            on_rsync_output=on_rsync_output,
            on_sync_start=on_sync_start,
            on_sync_end=on_sync_end,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
        )
