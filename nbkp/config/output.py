"""Config-level display formatting helpers."""

from __future__ import annotations

from . import (
    LocalVolume,
    RemoteVolume,
    ResolvedEndpoints,
)


def format_volume_display(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Format a volume for human display."""
    match vol:
        case RemoteVolume():
            ep = resolved_endpoints.get(vol.slug)
            if ep is None:
                return f"{vol.ssh_endpoint}:{vol.path}"
            if ep.server.user:
                host_part = f"{ep.server.user}@{ep.server.host}"
            else:
                host_part = ep.server.host
            if ep.server.port != 22:
                host_part += f":{ep.server.port}"
            return f"{host_part}:{vol.path}"
        case LocalVolume():
            return vol.path


def host_label(
    vol: LocalVolume | RemoteVolume,
    resolved_endpoints: ResolvedEndpoints,
) -> str:
    """Human-readable host label for a volume."""
    match vol:
        case LocalVolume():
            return "this machine"
        case RemoteVolume():
            ep = resolved_endpoints[vol.slug]
            return ep.server.host


def endpoint_path(
    vol: LocalVolume | RemoteVolume,
    subdir: str | None,
) -> str:
    """Resolve the full endpoint path."""
    if subdir:
        return f"{vol.path}/{subdir}"
    else:
        return vol.path
