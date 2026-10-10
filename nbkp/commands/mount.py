"""Managed mount context manager with Rich display callbacks."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from rich.console import Console

from ..clihelpers import OutputFormat
from ..config import Config
from ..config.epresolution import ResolvedEndpoints
from ..credentials import build_passphrase_fn
from ..disks.context import managed_mount as _disks_managed_mount
from ..disks.observation import MountObservation
from ..disks.output import build_mount_status_table
from ..policy import Strictness
from .credentials import prompt_passphrase
from .mount_progress import LifecycleProgress


def _print_mount_status(
    progress: LifecycleProgress,
    mount_observations: dict[str, MountObservation],
) -> None:
    """Print the post-mount status table (human output only)."""
    Console().print(
        build_mount_status_table(
            [
                (progress.display_names.get(slug, slug), obs)
                for slug, obs in mount_observations.items()
            ],
            strictness=progress.strictness,
        )
    )


@contextmanager
def managed_mount(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    mount: bool = True,
    umount: bool = True,
    output_format: OutputFormat = OutputFormat.HUMAN,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Generator[
    tuple[Config, dict[str, MountObservation]],
    None,
    None,
]:
    """Context manager that mounts volumes on entry and umounts on exit.

    Thin wrapper around :func:`disks.context.managed_mount` that adds
    Rich display callbacks and credential management.

    Yields ``(resolved_config, mount_observations)``.  ``resolved_config``
    is *cfg* with discovered mountpoints filled in for mount-managed volumes
    that omitted ``path``.  Observations capture the runtime state discovered
    during mount so that preflight checks can reuse it instead of re-probing.

    Parameters
    ----------
    mount:
        When ``False`` (or no volumes have mount config), mounting and
        umounting are both skipped.
    umount:
        When ``False``, the umount phase is skipped even if volumes
        were mounted.  Useful for debugging (``run --no-umount``).
    output_format:
        Controls whether Rich spinner / result lines are printed.  The
        credential-prefetch phase gets its own progress bar, shown only when
        the provider is prefetchable and encrypted volumes are configured.
    strictness:
        Picks the per-mount severity icon when the operation fails.
        See :func:`.mount_progress.mount_result_severity`.
    """
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command, prompt=prompt_passphrase
    )
    use_progress = output_format is OutputFormat.HUMAN
    progress = LifecycleProgress.create(
        cfg, enabled=use_progress, strictness=strictness
    )
    # try/finally instead of `with` because the bars are conditionally
    # created (None when output is JSON), and cache.clear() must also run.
    try:
        with _disks_managed_mount(
            cfg,
            resolved,
            passphrase_fn,
            mount=mount,
            umount=umount,
            on_prefetch_start=progress.on_prefetch_start,
            on_prefetch_end=progress.on_prefetch_end,
            on_mount_start=progress.on_mount_start,
            on_mount_end=progress.on_mount_end,
            on_umount_start=progress.on_umount_start,
            on_umount_end=progress.on_umount_end,
        ) as result:
            progress.stop_mounting()
            _resolved_config, mount_observations = result
            if use_progress and mount_observations:
                _print_mount_status(progress, mount_observations)
            yield result
    finally:
        progress.stop_all()
        cache.clear()
