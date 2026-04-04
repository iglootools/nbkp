"""Mount status display helpers (Rich tables and JSON)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence

from rich.table import Table

if TYPE_CHECKING:
    from ..config.protocol.volume import LocalVolume, RemoteVolume


class MountStatusData(Protocol):
    """Structural protocol for mount runtime state.

    Satisfied by both ``MountObservation`` (dataclass) and
    ``MountCapabilities`` (Pydantic model).
    """

    @property
    def resolved_backend(self) -> str | None: ...

    @property
    def device_present(self) -> bool | None: ...

    @property
    def luks_attached(self) -> bool | None: ...

    @property
    def mounted(self) -> bool | None: ...


def display_name(vol: LocalVolume | RemoteVolume) -> str:
    """Display name for a volume: ``ssh-endpoint:slug`` for remote, ``slug`` for local."""
    from ..config.protocol.volume import RemoteVolume

    return (
        f"{vol.ssh_endpoint}:{vol.slug}" if isinstance(vol, RemoteVolume) else vol.slug
    )


def mount_state_icon(value: bool | None) -> str:
    """Format a mount state value as checkmark, cross, or dash."""
    match value:
        case True:
            return "[green]\u2713[/green]"
        case False:
            return "[red]\u2717[/red]"
        case None:
            return "\u2014"


def build_mount_status_table(
    statuses: Sequence[tuple[str, MountStatusData]],
    *,
    title: str = "Volume Mount Status:",
) -> Table:
    """Build a Rich table showing mount status for each volume."""
    table = Table(title=title)
    table.add_column("Name", style="bold")
    table.add_column("Strategy")
    table.add_column("Device")
    table.add_column("LUKS")
    table.add_column("Mounted")
    for slug, status in statuses:
        table.add_row(
            slug,
            status.resolved_backend or "?",
            mount_state_icon(status.device_present),
            mount_state_icon(status.luks_attached),
            mount_state_icon(status.mounted),
        )
    return table


def build_mount_status_json(
    statuses: Sequence[tuple[str, MountStatusData]],
) -> list[dict[str, object]]:
    """Build a JSON-serializable list of mount status entries."""
    return [
        {
            "volume": slug,
            "strategy": status.resolved_backend,
            "device_present": status.device_present,
            "luks_attached": status.luks_attached,
            "mounted": status.mounted,
        }
        for slug, status in statuses
    ]
