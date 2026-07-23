"""Mount lifecycle context manager.

Provides a display-agnostic building block for mount/umount lifecycle
that both the CLI and tests can share.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Generator

from ..config import Config
from ..config.epresolution import ResolvedEndpoints
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
        ``credentials.build_passphrase_fn``).
    mount:
        When ``False`` (or no volumes have mount config), mounting and
        umounting are both skipped.
    umount:
        When ``False``, the umount phase is skipped even if volumes
        were mounted.  Useful for debugging (``run --no-umount``).
    names:
        When set, only mount/umount these volume names.
    """
    has_mount_config = any(
        getattr(v, "mount", None) is not None for v in config.volumes.values()
    )
    do_mount = mount and has_mount_config
    do_umount = do_mount and umount

    mount_observations: dict[str, MountObservation] = {}
    resolved_config = config

    if do_mount:
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
