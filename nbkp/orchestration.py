"""Mount lifecycle + sync pipeline orchestration.

Provides display-agnostic building blocks that both the CLI and tests
can share:

* ``managed_mount`` — context manager for mount/umount lifecycle
* ``mount_and_run`` — mount + check-and-run + umount in one call
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Generator

from .config import Config, ResolvedEndpoints
from .mount.detection import resolve_mount_strategy
from .mount.lifecycle import (
    MountResult,
    UmountResult,
    mount_volumes,
    umount_volumes,
)
from .mount.observation import MountObservation, build_mount_observations
from .mount.strategy import MountStrategy
from .preflight import SyncError, SyncStatus, VolumeStatus
from .sync.pipeline import INACTIVE_ERRORS, PipelineResult, check_and_run
from .sync.rsync import ProgressMode
from .sync.runner import SyncResult


@contextmanager
def managed_mount(
    config: Config,
    resolved: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    mount: bool = True,
    umount: bool = True,
    names: list[str] | None = None,
    on_mount_start: Callable[[str], None] | None = None,
    on_mount_end: Callable[[str, MountResult], None] | None = None,
    on_umount_start: Callable[[str], None] | None = None,
    on_umount_end: Callable[[str, UmountResult], None] | None = None,
) -> Generator[
    tuple[dict[str, MountStrategy], dict[str, MountObservation]],
    None,
    None,
]:
    """Context manager that mounts volumes on entry and umounts on exit.

    Yields a tuple of ``(mount_strategy, mount_observations)``.  When
    mounting is skipped both dicts are empty.  Observations capture
    the runtime state discovered during mount so that preflight checks
    can reuse it instead of re-probing.

    Parameters
    ----------
    passphrase_fn:
        Callable that returns a passphrase for a given passphrase-id.
        The caller is responsible for cache management (see
        ``credentials.build_passphrase_fn``).
    mount:
        When ``False`` (or no volumes have mount config), mounting and
        umounting are both skipped.
    umount:
        When ``False``, the umount phase is skipped even if volumes
        were mounted.  Useful for debugging (``run --no-umount``).
    names:
        When set, only resolve mount strategies for these volume names.
    """
    has_mount_config = any(
        getattr(v, "mount", None) is not None for v in config.volumes.values()
    )
    do_mount = mount and has_mount_config
    do_umount = do_mount and umount

    mount_strategy: dict[str, MountStrategy] = {}
    mount_observations: dict[str, MountObservation] = {}

    if do_mount:
        mount_strategy = resolve_mount_strategy(config, resolved, names=names)
        mount_results = mount_volumes(
            config,
            resolved,
            passphrase_fn,
            mount_strategy=mount_strategy,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
        )
        mount_observations = build_mount_observations(
            mount_results, mount_strategy, config
        )

    try:
        yield mount_strategy, mount_observations
    finally:
        if do_umount:
            umount_volumes(
                config,
                resolved,
                mount_strategy=mount_strategy,
                on_umount_start=on_umount_start,
                on_umount_end=on_umount_end,
            )


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
    strict: bool = False,
    dry_run: bool = False,
    only_syncs: list[str] | None = None,
    progress: ProgressMode | None = None,
    prune: bool = True,
    on_check_progress: Callable[[str], None] | None = None,
    on_checks_done: (
        Callable[[dict[str, VolumeStatus], dict[str, SyncStatus]], None] | None
    ) = None,
    on_rsync_output: Callable[[str], None] | None = None,
    on_sync_start: Callable[[str], None] | None = None,
    on_sync_end: Callable[[str, SyncResult], None] | None = None,
    inactive_errors: frozenset[SyncError] = INACTIVE_ERRORS,
) -> PipelineResult:
    """Mount volumes, run the check-and-run pipeline, then umount.

    Combines :func:`managed_mount` with :func:`~nbkp.sync.pipeline.check_and_run`
    into a single call.  Mount observations are automatically forwarded
    to preflight checks so they don't re-probe device/mount state.
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
    ) as (_mount_strategy, mount_observations):
        return check_and_run(
            config,
            strict=strict,
            dry_run=dry_run,
            only_syncs=only_syncs,
            progress=progress,
            prune=prune,
            on_check_progress=on_check_progress,
            on_checks_done=on_checks_done,
            on_rsync_output=on_rsync_output,
            on_sync_start=on_sync_start,
            on_sync_end=on_sync_end,
            resolved_endpoints=resolved,
            mount_observations=mount_observations,
            inactive_errors=inactive_errors,
        )
