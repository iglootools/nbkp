"""SSH host resolution, network classification, and endpoint resolution."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from functools import reduce
from pathlib import Path

import paramiko

from ..config.epresolution import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoint,
    ResolvedEndpoints,
)
from ..config.protocol import (
    Config,
    RemoteVolume,
    SshEndpoint,
)


def _load_ssh_config() -> paramiko.SSHConfig | None:
    """Load the user's SSH config if it exists."""
    config_path = Path.home() / ".ssh" / "config"
    if config_path.exists():
        return paramiko.SSHConfig.from_path(str(config_path))
    else:
        return None


def resolve_hostname(hostname: str) -> str:
    """Resolve an SSH hostname through ~/.ssh/config.

    If the hostname is defined in SSH config (via HostName),
    returns the resolved hostname. Otherwise returns the
    original hostname unchanged.
    """
    ssh_config = _load_ssh_config()
    if ssh_config is not None:
        result = ssh_config.lookup(hostname)
        return result.get("hostname", hostname)
    else:
        return hostname


def resolve_host(hostname: str) -> set[str] | None:
    """Resolve hostname to IP addresses.

    First resolves through SSH config, then via DNS.
    Returns None if the hostname cannot be resolved.
    """
    real_host = resolve_hostname(hostname)
    try:
        results = socket.getaddrinfo(real_host, None)
        return {str(r[4][0]) for r in results}
    except socket.gaierror:
        return None


def is_private_host(hostname: str) -> bool | None:
    """Check whether hostname resolves to private addresses.

    Returns True if all resolved addresses are private,
    False if any is public, or None if the hostname
    cannot be resolved.
    """
    addrs = resolve_host(hostname)
    if addrs is None:
        return None
    else:
        return all(ipaddress.ip_address(a).is_private for a in addrs)


def _ssh_config_updates(
    endpoint: SshEndpoint,
    result: paramiko.SSHConfigDict,
) -> dict[str, object]:
    """Extract enrichment updates from an SSH config lookup result.

    Only fields NOT explicitly set on the endpoint are candidates.
    """
    fields_set = endpoint.model_fields_set
    return {
        k: v
        for k, v in {
            "port": int(result["port"]) if "port" in result else None,
            "user": result.get("user"),
            "key": (
                str(Path(result["identityfile"][0]).expanduser())
                if "identityfile" in result
                else None
            ),
        }.items()
        if v is not None and k not in fields_set
    }


def enrich_from_ssh_config(
    endpoint: SshEndpoint,
) -> SshEndpoint:
    """Fill unset endpoint fields from ``~/.ssh/config``.

    Uses ``model_fields_set`` to determine which fields were
    explicitly provided in the config (or inherited via
    ``extends``).  Only fields NOT in that set are candidates
    for SSH config enrichment.

    Enriched fields: ``port``, ``user``, ``key``.
    """
    ssh_config = _load_ssh_config()
    if ssh_config is None:
        return endpoint
    else:
        updates = _ssh_config_updates(endpoint, ssh_config.lookup(endpoint.host))
        return endpoint.model_copy(update=updates) if updates else endpoint


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
    else:
        ef = endpoint_filter
        result = _apply_soft_filters(candidates, eps, ef)
        return eps[result[0]]


def _apply_soft_filters(
    candidates: list[str],
    eps: dict[str, SshEndpoint],
    ef: EndpointFilter,
) -> list[str]:
    """Apply the soft filter chain: DNS → exclude → include → network."""
    excl = set(ef.exclude_locations)
    locs = set(ef.locations)
    want_private = ef.network == NetworkType.PRIVATE if ef.network is not None else None

    filters: list[Callable[[str], bool]] = [
        # DNS reachability
        lambda s: is_private_host(eps[s].host) is not None,
        *([lambda s: not (excl & set(eps[s].location_list))] if excl else []),
        *([lambda s: bool(locs & set(eps[s].location_list))] if locs else []),
        *(
            [lambda s: is_private_host(eps[s].host) == want_private]
            if want_private is not None
            else []
        ),
    ]

    return reduce(_soft_filter, filters, candidates)


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
