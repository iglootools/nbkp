"""Shared CLI helpers: re-exports from clihelpers + domain-specific helpers.

This module is a transitional shim. Shared helpers live in clihelpers/,
domain-specific helpers will move to their respective <domain>.cli packages.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from rich.console import Console

from ..config import Config, LocalVolume
from ..config.epresolution import ResolvedEndpoints
from ..credentials import build_passphrase_fn
from ..disks.lifecycle import MountResult, UmountResult, mount_volume_count
from ..disks.observation import MountObservation
from ..disks.output import build_mount_status_table, volume_display_name
from ..disks.strategy import MountStrategy
from ..disks.context import managed_mount as _disks_managed_mount
from ..preflight.output import print_human_check
from ..preflight import (
    PreflightResult,
    check_all_syncs,
)
from ..run.pipeline import Strictness, has_fatal_errors

# ── Re-exports from clihelpers ──────────────────────────────────────
from ..clihelpers.output import OutputFormat as OutputFormat
from ..clihelpers.config import load_config_or_exit as load_config_or_exit
from ..clihelpers.endpoints import (
    build_endpoint_filter as build_endpoint_filter,
    resolve_endpoints as resolve_endpoints,
)
from ..clihelpers.progress import (
    CheckProgressBar as CheckProgressBar,
    VolumeProgressBar as VolumeProgressBar,
)


# ── Disks-specific helpers (will move to disks/cli/helpers.py) ──────

def _format_mount_result(
    slug: str, success: bool, detail: str | None, _warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    return f"{icon} mount {slug}{detail_str}"


def _format_umount_result(
    slug: str, success: bool, detail: str | None, warning: str | None
) -> str:
    icon = "[green]\u2713[/green]" if success else "[red]\u2717[/red]"
    detail_str = f" ({detail})" if detail else ""
    warning_str = f" [yellow]warning: {warning}[/yellow]" if warning else ""
    return f"{icon} umount {slug}{detail_str}{warning_str}"


@contextmanager
def managed_mount(
    cfg: Config,
    resolved: ResolvedEndpoints,
    *,
    mount: bool = True,
    umount: bool = True,
    output_format: OutputFormat = OutputFormat.HUMAN,
) -> Generator[
    tuple[dict[str, MountStrategy], dict[str, MountObservation]],
    None,
    None,
]:
    """Context manager that mounts volumes on entry and umounts on exit.

    Thin wrapper around :func:`disks.context.managed_mount` that adds
    Rich display callbacks and credential management.

    Yields a tuple of ``(mount_strategy, mount_observations)``.  When
    mounting is skipped both dicts are empty.  Observations capture
    the runtime state discovered during mount so that preflight checks
    can reuse it instead of re-probing.

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
    """
    passphrase_fn, cache = build_passphrase_fn(
        cfg.credential_provider, cfg.credential_command
    )

    use_progress = output_format is OutputFormat.HUMAN
    total = mount_volume_count(cfg)
    display_names = {
        slug: volume_display_name(vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None
    }

    mount_bar = (
        VolumeProgressBar(total, "Mounting", _format_mount_result)
        if use_progress
        else None
    )
    umount_bar = (
        VolumeProgressBar(total, "Umounting", _format_umount_result)
        if use_progress
        else None
    )

    def on_mount_start(slug: str) -> None:
        if mount_bar is not None:
            mount_bar.on_start(display_names.get(slug, slug))

    def on_mount_end(slug: str, result: MountResult) -> None:
        if mount_bar is not None:
            mount_bar.on_end(
                display_names.get(slug, slug), result.success, result.detail
            )

    def on_umount_start(slug: str) -> None:
        if umount_bar is not None:
            umount_bar.on_start(display_names.get(slug, slug))

    def on_umount_end(slug: str, result: UmountResult) -> None:
        if umount_bar is not None:
            umount_bar.on_end(
                display_names.get(slug, slug),
                result.success,
                result.detail,
                result.warning,
            )

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
            _mount_strategy, mount_observations = result
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


# ── Preflight-specific helpers (will move to preflight/cli/helpers.py) ──

def _check_total(cfg: Config, only_syncs: list[str] | None) -> int:
    """Count progress steps: SSH endpoints + volumes + sync endpoints.

    Matches the ``_track()`` calls in ``check_all_syncs``: one per SSH
    endpoint (volume-referenced + all remaining defined endpoints), one
    per volume, and one per source/destination sync endpoint.
    """
    syncs = (
        {s: sc for s, sc in cfg.syncs.items() if s in only_syncs}
        if only_syncs
        else cfg.syncs
    )
    src_eps = {cfg.source_endpoint(sc).slug for sc in syncs.values()}
    dst_eps = {cfg.destination_endpoint(sc).slug for sc in syncs.values()}
    volumes = (
        {cfg.source_endpoint(sc).volume for sc in syncs.values()}
        | {cfg.destination_endpoint(sc).volume for sc in syncs.values()}
        if only_syncs
        else set(cfg.volumes.keys())
    )

    # SSH endpoints: volume-referenced + all remaining defined endpoints
    volume_ssh_slugs = {
        "localhost"
        if isinstance(cfg.volumes[v_slug], LocalVolume)
        else cfg.volumes[v_slug].ssh_endpoint  # type: ignore[union-attr]
        for v_slug in volumes
    }
    remaining_slugs = set(cfg.ssh_endpoints.keys()) - volume_ssh_slugs
    ssh_count = len(volume_ssh_slugs) + len(remaining_slugs)

    return ssh_count + len(volumes) + len(src_eps) + len(dst_eps)


def check_all_with_progress(
    cfg: Config,
    use_progress: bool,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
) -> PreflightResult:
    """Run check_all_syncs with an optional progress bar."""
    total = _check_total(cfg, only_syncs)

    if not use_progress or total == 0:
        return check_all_syncs(
            cfg,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )

    bar = CheckProgressBar(total)
    try:
        return check_all_syncs(
            cfg,
            on_check_start=bar.on_start,
            on_check_end=bar.on_end,
            only_syncs=only_syncs,
            resolved_endpoints=resolved_endpoints,
            dry_run=dry_run,
            mount_observations=mount_observations,
        )
    finally:
        bar.stop()


def check_and_display(
    cfg: Config,
    output_format: OutputFormat,
    strictness: Strictness,
    only_syncs: list[str] | None = None,
    resolved_endpoints: ResolvedEndpoints | None = None,
    dry_run: bool = False,
    mount_observations: dict[str, MountObservation] | None = None,
) -> tuple[PreflightResult, bool]:
    """Compute statuses, display human output, and check for errors.

    Returns the preflight result and whether there are fatal errors.
    When *only_syncs* is given, only those syncs (and the volumes
    they reference) are checked.
    """
    preflight = check_all_with_progress(
        cfg,
        use_progress=output_format is OutputFormat.HUMAN,
        only_syncs=only_syncs,
        resolved_endpoints=resolved_endpoints,
        dry_run=dry_run,
        mount_observations=mount_observations,
    )

    if output_format is OutputFormat.HUMAN:
        print_human_check(
            preflight.ssh_endpoint_statuses,
            preflight.volume_statuses,
            preflight.sync_statuses,
            cfg,
            resolved_endpoints=resolved_endpoints,
        )

    return preflight, has_fatal_errors(preflight.sync_statuses, strictness=strictness)
