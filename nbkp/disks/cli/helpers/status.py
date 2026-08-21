"""Volume status probing and display helpers."""

from __future__ import annotations

from dataclasses import dataclass

import typer
from rich.console import Console
from rich.text import Text

from ....clihelpers import (
    OutputFormat,
    Severity,
    StepProgressBar,
    echo_json,
    severity_style,
    severity_symbol,
)
from ....config import Config, LocalVolume, RemoteVolume
from ....config.epresolution import ResolvedEndpoints
from ...mount_checks import check_mount_status
from ...output import (
    MountStatusData,
    MountStatusLabel,
    build_mount_status_json,
    build_mount_status_table,
    display_name,
)


@dataclass(frozen=True)
class _ErrorStatus:
    """Synthetic, all-unknown mount status for unreachable/unmanaged volumes.

    The detail (error text, ``not managed``) is folded into the Name column
    by the caller since the status table only shows device/unlock/mount state.
    """

    device_present: bool | None = None
    luks_unlocked: bool | None = None
    mounted: bool | None = None
    mount_failure_reason: str | None = None


def _unmanaged_statuses(
    cfg: Config,
    names: list[str] | None = None,
) -> list[tuple[Text, MountStatusData]]:
    """Build status entries for volumes without mount config."""
    return [
        (
            Text.assemble(display_name(vol), " ", ("(not managed)", "dim")),
            _ErrorStatus(),
        )
        for slug, vol in cfg.volumes.items()
        if vol.mount is None and (names is None or slug in names)
    ]


def _error_label(name: str, detail: str | None) -> Text:
    """Fold an error detail into the volume's display name (red).

    Assembled as ``Text`` rather than a markup string: *detail* is external
    text (an exception message, udisksctl's stderr) that Rich would otherwise
    parse for ``[...]`` style tags, and the same value is reused as the JSON
    ``volume`` field — where markup has no business appearing at all.
    """
    if detail is None:
        return Text(name)
    else:
        return Text.assemble(
            name,
            " ",
            (
                f"{severity_symbol(Severity.ERROR)} {detail}",
                severity_style(Severity.ERROR),
            ),
        )


def _probe_volume_status(
    vol: LocalVolume | RemoteVolume,
    resolved: ResolvedEndpoints,
    bar: StepProgressBar | None,
) -> tuple[MountStatusLabel, MountStatusData]:
    """Probe a single volume's mount status with progress bar updates.

    Per-volume result lines are prefixed with ``status`` so they're
    distinguishable from ``mount`` / ``umount`` action lines printed by
    the same Rich console (e.g. ``disks umount`` runs the umount step
    and then this probe back-to-back).
    """
    assert vol.mount is not None
    label = display_name(vol)
    line = f"status {label}"
    if bar is not None:
        bar.on_start(line)
    try:
        status: MountStatusData = check_mount_status(vol, vol.mount, resolved)
        if bar is not None:
            bar.on_end(line, Severity.OK)
        return label, status
    except Exception as e:  # noqa: BLE001
        if bar is not None:
            bar.on_end(line, Severity.ERROR, str(e))
        return _error_label(label, f"unreachable: {e}"), _ErrorStatus()


def _show_status_table(
    statuses: list[tuple[MountStatusLabel, MountStatusData]],
    output_format: OutputFormat,
) -> None:
    """Display the mount status table or JSON."""
    match output_format:
        case OutputFormat.JSON:
            echo_json(build_mount_status_json(statuses))
        case OutputFormat.HUMAN:
            if statuses:
                Console().print(build_mount_status_table(statuses))
            else:
                typer.echo("No volumes found.")


def _probe_and_show_status(
    cfg: Config,
    resolved: ResolvedEndpoints,
    output_format: OutputFormat,
    names: list[str] | None = None,
) -> None:
    """Probe mount status and display as table or JSON."""
    managed = [
        (slug, vol)
        for slug, vol in cfg.volumes.items()
        if vol.mount is not None and (names is None or slug in names)
    ]

    bar = (
        StepProgressBar(len(managed))
        if output_format is OutputFormat.HUMAN and managed
        else None
    )
    statuses: list[tuple[MountStatusLabel, MountStatusData]] = [
        *[_probe_volume_status(vol, resolved, bar) for _slug, vol in managed],
        *_unmanaged_statuses(cfg, names),
    ]
    if bar is not None:
        bar.stop()

    _show_status_table(statuses, output_format)
