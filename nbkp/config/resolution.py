"""Endpoint resolution: resolve SSH endpoints once per command."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import List, Optional

from pydantic import ConfigDict, Field

from ..remote.resolution import enrich_from_ssh_config, is_private_host
from .protocol import (
    Config,
    RemoteVolume,
    SshEndpoint,
    _BaseModel,
)


class NetworkType(str, Enum):
    """Network type for endpoint filtering."""

    PRIVATE = "private"
    PUBLIC = "public"


class EndpointFilter(_BaseModel):
    """Endpoint selection filter (not serialized)."""

    model_config = ConfigDict(frozen=True)
    locations: List[str] = Field(default_factory=list)
    exclude_locations: List[str] = Field(default_factory=list)
    network: Optional[NetworkType] = None


class ResolvedEndpoint(_BaseModel):
    """Pre-resolved SSH endpoint with proxy chain."""

    model_config = ConfigDict(frozen=True)
    server: SshEndpoint
    proxy_chain: list[SshEndpoint] = Field(default_factory=list)


ResolvedEndpoints = dict[str, ResolvedEndpoint]


def _soft_filter(slugs: list[str], predicate: Callable[[str], bool]) -> list[str]:
    """Apply a filter, keeping the original list if it would eliminate all candidates."""
    filtered = [s for s in slugs if predicate(s)]
    return filtered if filtered else slugs


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
    eps = config.ssh_endpoints

    if endpoint_filter is None:
        return eps[candidates[0]]

    ef = endpoint_filter

    # DNS reachability
    reachable = _soft_filter(
        candidates, lambda s: is_private_host(eps[s].host) is not None
    )

    # Exclude locations
    if ef.exclude_locations:
        excl = set(ef.exclude_locations)
        reachable = _soft_filter(
            reachable, lambda s: not (excl & set(eps[s].location_list))
        )

    # Include locations
    if ef.locations:
        locs = set(ef.locations)
        reachable = _soft_filter(
            reachable, lambda s: bool(locs & set(eps[s].location_list))
        )

    # Network filter (private / public)
    if ef.network is not None:
        want_private = ef.network == NetworkType.PRIVATE
        reachable = _soft_filter(
            reachable, lambda s: is_private_host(eps[s].host) == want_private
        )

    return eps[reachable[0]]


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
    """Check if all SSH endpoints for a volume are at excluded locations."""
    candidates = list(vol.ssh_endpoints) if vol.ssh_endpoints else [vol.ssh_endpoint]
    excl = set(endpoint_filter.exclude_locations) if endpoint_filter else set()
    return bool(excl) and all(
        excl & set(config.ssh_endpoints[slug].location_list) for slug in candidates
    )


def _resolve_volume(
    config: Config,
    vol: RemoteVolume,
    endpoint_filter: EndpointFilter | None,
) -> ResolvedEndpoint:
    """Resolve the SSH endpoint and proxy chain for a single remote volume."""
    server = enrich_from_ssh_config(
        resolve_endpoint_for_volume(config, vol, endpoint_filter)
    )
    proxy_chain = [
        enrich_from_ssh_config(ep) for ep in resolve_proxy_chain(config, server)
    ]
    return ResolvedEndpoint(server=server, proxy_chain=proxy_chain)


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
    return {
        vol.slug: _resolve_volume(config, vol, endpoint_filter)
        for vol in config.volumes.values()
        if isinstance(vol, RemoteVolume)
        and not _is_volume_excluded(config, vol, endpoint_filter)
    }
