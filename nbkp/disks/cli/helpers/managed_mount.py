"""Managed mount context manager with Rich display callbacks."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from rich.console import Console

from ....clihelpers import (
    OutputFormat,
    Severity,
    Strictness,
    classify_severity,
    severity_icon,
)
from ....config import Config
from ....config.epresolution import ResolvedEndpoints
from ....credentials import build_passphrase_fn
from ...context import managed_mount as _disks_managed_mount
from ...lifecycle import MountFailureReason, MountResult, UmountResult, mount_count
from ...observation import MountObservation
from ...output import build_mount_status_table, display_name
from .progress import DisksProgressBar


# Mount failure reasons that correspond to "expected inactive" preflight
# states (e.g. drive not plugged in maps to VolumeError.DEVICE_NOT_PRESENT
# in INACTIVE_VOLUME_ERRORS; UNREACHABLE maps to SshEndpointError.UNREACHABLE
# in INACTIVE_SSH_ERRORS).
_INACTIVE_MOUNT_REASONS: frozenset[MountFailureReason] = frozenset(
    {
        MountFailureReason.DEVICE_NOT_PRESENT,
        MountFailureReason.UNREACHABLE,
    }
)


def mount_result_severity(
    result: MountResult,
    strictness: Strictness = Strictness.IGNORE_INACTIVE,
) -> Severity:
    """Map a mount lifecycle result to display severity under *strictness*.

    A drive not being plugged in is an expected condition (the user
    backs up to removable media), so under the default
    ``IGNORE_INACTIVE`` it renders as a warning.  Under ``IGNORE_NONE``
    every mount failure is fatal, so it renders as an error to stay
    consistent with the preflight abort that will follow.  Under
    ``IGNORE_ALL`` every mount failure is non-fatal and renders as a
    warning.
    """
    if result.success:
        return Severity.OK
    return classify_severity(
        result.failure_reason in _INACTIVE_MOUNT_REASONS,
        strictness,
    )


def _managed_format_mount_result(
    slug: str, severity: Severity, detail: str | None, _warning: str | None
) -> str:
    detail_str = f" ({detail})" if detail else ""
    return f"{severity_icon(severity)} mount {slug}{detail_str}"


def _managed_format_umount_result(
    slug: str, severity: Severity, detail: str | None, warning: str | None
) -> str:
    detail_str = f" ({detail})" if detail else ""
    warning_str = f" [yellow]warning: {warning}[/yellow]" if warning else ""
    return f"{severity_icon(severity)} umount {slug}{detail_str}{warning_str}"


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
        Controls whether Rich spinner / result lines are printed.
    strictness:
        Picks the per-mount severity icon when the operation fails.
        See :func:`mount_result_severity`.
    """
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command
    )

    use_progress = output_format is OutputFormat.HUMAN
    total = mount_count(cfg)
    display_names = {
        slug: display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }

    mount_bar = (
        DisksProgressBar(total, "Mounting", _managed_format_mount_result)
        if use_progress
        else None
    )
    umount_bar = (
        DisksProgressBar(total, "Umounting", _managed_format_umount_result)
        if use_progress
        else None
    )

    def on_mount_start(slug: str) -> None:
        if mount_bar is not None:
            mount_bar.on_start(display_names.get(slug, slug))

    def on_mount_end(slug: str, result: MountResult) -> None:
        if mount_bar is not None:
            mount_bar.on_end(
                display_names.get(slug, slug),
                mount_result_severity(result, strictness),
                result.detail,
            )

    def on_umount_start(slug: str) -> None:
        if umount_bar is not None:
            umount_bar.on_start(display_names.get(slug, slug))

    def on_umount_end(slug: str, result: UmountResult) -> None:
        if umount_bar is not None:
            umount_bar.on_end(
                display_names.get(slug, slug),
                Severity.OK if result.success else Severity.ERROR,
                result.detail,
                result.warning,
            )

    # try/finally instead of `with` because mount_bar/umount_bar are
    # conditionally created (None when output is JSON), and cache.clear()
    # must also run.
    try:
        with _disks_managed_mount(
            cfg,
            resolved,
            passphrase_fn,
            mount=mount,
            umount=umount,
            on_mount_start=on_mount_start,
            on_mount_end=on_mount_end,
            on_umount_start=on_umount_start,
            on_umount_end=on_umount_end,
        ) as result:
            if mount_bar is not None:
                mount_bar.stop()
            _resolved_config, mount_observations = result
            if use_progress and mount_observations:
                display_statuses = [
                    (display_names.get(slug, slug), obs)
                    for slug, obs in mount_observations.items()
                ]
                Console().print(build_mount_status_table(display_statuses))
            yield result
    finally:
        if umount_bar is not None:
            umount_bar.stop()
        cache.clear()
