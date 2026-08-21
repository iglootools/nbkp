"""Mount lifecycle context manager.

Provides a display-agnostic building block for mount/umount lifecycle
that both the CLI and tests can share.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager

from ..config import Config
from ..config.epresolution import ResolvedEndpoints
from ..credentials import PassphrasePrefetch, prefetch_passphrases
from .lifecycle import (
    MountResult,
    UmountResult,
    mount_volumes,
    umount_volumes,
)
from .observation import (
    MountObservation,
    apply_effective_paths,
    build_mount_observations,
)


@contextmanager
def managed_mount(
    config: Config,
    resolved: ResolvedEndpoints,
    passphrase_fn: Callable[[str], str],
    *,
    mount: bool = True,
    umount: bool = True,
    names: list[str] | None = None,
    on_prefetch_start: Callable[[str], None] | None = None,
    on_prefetch_end: Callable[[str, PassphrasePrefetch], None] | None = None,
    on_mount_start: Callable[[str], None] | None = None,
    on_mount_end: Callable[[str, MountResult], None] | None = None,
    on_umount_start: Callable[[str], None] | None = None,
    on_umount_end: Callable[[str, UmountResult], None] | None = None,
) -> Generator[
    tuple[Config, dict[str, MountObservation]],
    None,
    None,
]:
    """Context manager that mounts volumes on entry and umounts on exit.

    Yields ``(resolved_config, mount_observations)``.  ``resolved_config`` is
    *config* with discovered mountpoints filled in for mount-managed volumes
    that omitted ``path`` (see :func:`disks.observation.apply_effective_paths`)
    — downstream consumers should use it instead of the original config.
    Observations capture the runtime state discovered during mount so that
    preflight checks can reuse it instead of re-probing.

    Parameters
    ----------
    passphrase_fn:
        Callable that returns a passphrase for a given passphrase-id.
        The caller is responsible for cache management (see
        ``credentials.build_passphrase_fn``).  Before any device is touched,
        it is called once for *every* configured passphrase-id so that all
        credential-store access — and any approval it prompts for — happens
        up front (see :func:`credentials.prefetch_passphrases`).
    mount:
        When ``False`` (or no volumes have mount config), mounting and
        umounting are both skipped.
    umount:
        When ``False``, the umount phase is skipped even if volumes
        were mounted.  Useful for debugging (``run --no-umount``).
    names:
        When set, only mount/umount these volume names.  Passphrase
        prefetching ignores this filter on purpose.
    on_prefetch_start / on_prefetch_end:
        Called around each passphrase retrieval in the prefetch phase.
    """
    has_mount_config = any(
        getattr(v, "mount", None) is not None for v in config.volumes.values()
    )
    do_mount = mount and has_mount_config
    do_umount = do_mount and umount

    mount_observations: dict[str, MountObservation] = {}
    resolved_config = config

    if do_mount:
        # Retrieve *every* configured passphrase before touching a device,
        # not just the ones this run needs.  Unfiltered by *names* on
        # purpose: the point is that one approval pass covers every drive,
        # so a later run with a different drive attached needs no operator.
        # See :func:`credentials.prefetch_passphrases`.
        prefetch_passphrases(
            config,
            passphrase_fn,
            on_prefetch_start=on_prefetch_start,
            on_prefetch_end=on_prefetch_end,
        )
        mount_results = mount_volumes(
            config,
            resolved,
            passphrase_fn,
            names=names,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
        )
        mount_observations = build_mount_observations(mount_results)
        resolved_config = apply_effective_paths(config, mount_observations)

    try:
        yield resolved_config, mount_observations
    finally:
        if do_umount:
            umount_volumes(
                config,
                resolved,
                names=names,
                on_umount_start=on_umount_start,
                on_umount_end=on_umount_end,
            )
