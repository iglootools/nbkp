"""Endpoint resolution: resolve SSH endpoints once per command."""

from __future__ import annotations

from pydantic import ConfigDict

from pydantic import Field

from ..remote.resolution import enrich_from_ssh_config
from .protocol import (
    Config,
    EndpointFilter,
    RemoteVolume,
    SshEndpoint,
    _BaseModel,
)


class ResolvedEndpoint(_BaseModel):
    """Pre-resolved SSH endpoint with proxy chain."""

    model_config = ConfigDict(frozen=True)
    server: SshEndpoint
    proxy_chain: list[SshEndpoint] = Field(default_factory=list)


ResolvedEndpoints = dict[str, ResolvedEndpoint]


def _is_volume_excluded(
    config: Config,
    vol: RemoteVolume,
    endpoint_filter: EndpointFilter | None,
) -> bool:
    """Check if all SSH endpoints for a volume are at excluded locations.

    Returns True when every candidate endpoint has a location tag
    matching the exclude list — meaning the volume should be skipped
    entirely.
    """
    if not endpoint_filter or not endpoint_filter.exclude_locations:
        return False
    candidates = (
        list(vol.ssh_endpoints)
        if vol.ssh_endpoints
        else [vol.ssh_endpoint]
    )
    excl = set(endpoint_filter.exclude_locations)
    return all(
        excl & set(config.ssh_endpoints[slug].location_list)
        for slug in candidates
    )


def resolve_all_endpoints(
    config: Config,
    endpoint_filter: EndpointFilter | None = None,
) -> ResolvedEndpoints:
    """Resolve SSH endpoints for all remote volumes.

    Returns a mapping from volume slug to ResolvedEndpoint.
    Local volumes are not included in the result.
    Volumes whose endpoints are all at excluded locations
    are omitted (no SSH attempt).
    """
    result: dict[str, ResolvedEndpoint] = {}
    for vol in config.volumes.values():
        match vol:
            case RemoteVolume():
                if _is_volume_excluded(config, vol, endpoint_filter):
                    continue
                server = config.resolve_endpoint_for_volume(
                    vol, endpoint_filter
                )
                server = enrich_from_ssh_config(server)
                proxy_chain = config.resolve_proxy_chain(server)
                proxy_chain = [
                    enrich_from_ssh_config(ep)
                    for ep in proxy_chain
                ]
                result[vol.slug] = ResolvedEndpoint(
                    server=server,
                    proxy_chain=proxy_chain,
                )
    return result
