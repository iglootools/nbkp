"""Endpoint resolution: resolve SSH endpoints once per command."""

from __future__ import annotations

from pydantic import ConfigDict

from pydantic import Field

from ..remote.resolution import enrich_from_ssh_config, is_private_host
from .protocol import (
    Config,
    EndpointFilter,
    NetworkType,
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


def resolve_endpoint_for_volume(
    config: Config,
    vol: RemoteVolume,
    endpoint_filter: EndpointFilter | None = None,
) -> SshEndpoint:
    """Select the best SSH endpoint for a remote volume.

    Uses ``endpoint_filter`` (location, network) to narrow
    candidates.  Falls back to the primary ``ssh_endpoint``.
    """
    candidates = list(vol.ssh_endpoints) if vol.ssh_endpoints else [vol.ssh_endpoint]

    ef = endpoint_filter
    if ef is None:
        return config.ssh_endpoints[candidates[0]]

    # DNS reachability: drop endpoints whose host
    # cannot be resolved
    reachable = [
        slug
        for slug in candidates
        if is_private_host(config.ssh_endpoints[slug].host) is not None
    ]
    if not reachable:
        return config.ssh_endpoints[vol.ssh_endpoint]

    # Exclude locations
    if ef.exclude_locations:
        excl = set(ef.exclude_locations)
        filtered = [
            slug
            for slug in reachable
            if not (excl & set(config.ssh_endpoints[slug].location_list))
        ]
        if filtered:
            reachable = filtered

    # Include locations
    if ef.locations:
        filter_locs = set(ef.locations)
        by_loc = [
            slug
            for slug in reachable
            if filter_locs & set(config.ssh_endpoints[slug].location_list)
        ]
        if by_loc:
            reachable = by_loc

    # Network filter (private / public)
    if ef.network is not None:
        want_private = ef.network == NetworkType.PRIVATE
        by_net = [
            slug
            for slug in reachable
            if is_private_host(config.ssh_endpoints[slug].host) == want_private
        ]
        if by_net:
            reachable = by_net

    # Deterministic pick: first candidate in original order
    return config.ssh_endpoints[reachable[0]]


def resolve_proxy_chain(
    config: Config,
    server: SshEndpoint,
) -> list[SshEndpoint]:
    """Resolve the proxy-jump chain as a list of SshEndpoints."""
    return [config.ssh_endpoints[slug] for slug in server.proxy_jump_chain]


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
    candidates = list(vol.ssh_endpoints) if vol.ssh_endpoints else [vol.ssh_endpoint]
    excl = set(endpoint_filter.exclude_locations)
    return all(
        excl & set(config.ssh_endpoints[slug].location_list) for slug in candidates
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
                server = resolve_endpoint_for_volume(config, vol, endpoint_filter)
                server = enrich_from_ssh_config(server)
                proxy_chain = resolve_proxy_chain(config, server)
                proxy_chain = [enrich_from_ssh_config(ep) for ep in proxy_chain]
                result[vol.slug] = ResolvedEndpoint(
                    server=server,
                    proxy_chain=proxy_chain,
                )
    return result
