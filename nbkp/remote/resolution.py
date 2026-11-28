"""SSH host resolution, network classification, and endpoint resolution."""

import ipaddress
import socket
from collections.abc import Callable, Collection
from enum import StrEnum
from functools import cache, partial, reduce
from pathlib import Path

lazy import paramiko

from nbkp.config.protocol import Config, RemoteVolume, SshEndpoint
from nbkp.remote.endpoints import (
    EndpointFilter,
    NetworkType,
    ResolvedEndpoint,
    ResolvedEndpoints,
)

#: Resolves a hostname to its IP addresses, or ``None`` when it does not resolve.
type AddressLookupFn = Callable[[str], set[str] | None]


class SshConfigErrorKind(StrEnum):
    """Which SSH config value nbkp could not use."""

    INVALID_PORT = "invalid-port"


class SshConfigError(ValueError):
    """A value the user's SSH config gives a host that nbkp cannot use."""

    def __init__(self, kind: SshConfigErrorKind, host: str, value: str) -> None:
        """Build the error from its kind, the config ``Host`` and the bad value."""
        super().__init__(f"{kind}: Host {host}: {value!r}")
        self.kind = kind
        self.host = host
        self.value = value


def default_ssh_config_path() -> Path:
    """Return the user's OpenSSH client config path, ``~/.ssh/config``."""
    return Path.home() / ".ssh" / "config"


def load_ssh_config(path: Path | None = None) -> paramiko.SSHConfig:
    """
    Load an OpenSSH client config (default: the user's ``~/.ssh/config``).

    A missing file yields an empty config, whose lookups return only the
    queried hostname — the same as having no config at all.
    """
    config_path = path if path is not None else default_ssh_config_path()
    return (
        paramiko.SSHConfig.from_path(str(config_path))
        if config_path.exists()
        else paramiko.SSHConfig()
    )


def dns_lookup(hostname: str) -> set[str] | None:
    """
    Return the IP addresses *hostname* resolves to, or ``None`` if it does not.

    A malformed name (``nas..lan``, a label over 63 characters) fails IDNA
    encoding with a ``UnicodeError`` before any lookup: it resolves no more than
    an unknown name does, so it is ``None`` too, not a traceback.
    """
    try:
        return {str(r[4][0]) for r in socket.getaddrinfo(hostname, None)}
    except socket.gaierror, UnicodeError:
        return None


def _or_default(ssh_config: paramiko.SSHConfig | None) -> paramiko.SSHConfig:
    return ssh_config if ssh_config is not None else load_ssh_config()


def resolve_hostname(
    hostname: str,
    ssh_config: paramiko.SSHConfig | None = None,
) -> str:
    """Apply the SSH config's ``HostName`` for *hostname*, if it has one."""
    return _or_default(ssh_config).lookup(hostname).get("hostname", hostname)


def resolve_host(
    hostname: str,
    ssh_config: paramiko.SSHConfig | None = None,
    lookup: AddressLookupFn = dns_lookup,
) -> set[str] | None:
    """
    Return the IP addresses *hostname* resolves to, or ``None`` if it does not.

    The SSH config's ``HostName`` for *hostname* is applied before the lookup, so
    an alias resolves the way ``ssh`` would.
    """
    return lookup(resolve_hostname(hostname, ssh_config))


def is_private_host(
    hostname: str,
    ssh_config: paramiko.SSHConfig | None = None,
    lookup: AddressLookupFn = dns_lookup,
) -> bool | None:
    """
    Return whether every address *hostname* resolves to is private.

    True if all resolved addresses are private, False if any is public, or None if the
    hostname cannot be resolved.
    """
    addrs = resolve_host(hostname, ssh_config, lookup)
    if addrs is None:
        return None
    else:
        return all(ipaddress.ip_address(a).is_private for a in addrs)


def _parse_port(host: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as e:
        raise SshConfigError(SshConfigErrorKind.INVALID_PORT, host, value) from e


def _ssh_config_updates(
    endpoint: SshEndpoint,
    result: paramiko.SSHConfigDict,
) -> dict[str, object]:
    """
    Only fields NOT explicitly set on the endpoint are candidates.

    Raises ``SshConfigError`` for a ``Port`` that is not a number.
    """
    fields_set = endpoint.model_fields_set
    return {
        k: v
        for k, v in {
            "port": (
                _parse_port(endpoint.host, result["port"])
                if "port" in result and "port" not in fields_set
                else None
            ),
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
    ssh_config: paramiko.SSHConfig | None = None,
) -> SshEndpoint:
    """
    Fill unset endpoint fields (``port``, ``user``, ``key``) from the SSH config.

    "Unset" means absent from ``model_fields_set``: neither given in the
    config nor inherited via ``extends``.
    """
    lookup_result = _or_default(ssh_config).lookup(endpoint.host)
    updates = _ssh_config_updates(endpoint, lookup_result)
    return endpoint.model_copy(update=updates) if updates else endpoint


def _soft_filter(slugs: list[str], predicate: Callable[[str], bool]) -> list[str]:
    """Apply a filter, keeping the original list if it would leave no candidate."""
    filtered = [s for s in slugs if predicate(s)]
    return filtered or slugs


def resolve_endpoint_for_volume(
    config: Config,
    vol: RemoteVolume,
    endpoint_filter: EndpointFilter | None = None,
    ssh_config: paramiko.SSHConfig | None = None,
    lookup: AddressLookupFn = dns_lookup,
) -> SshEndpoint:
    """
    Return the first candidate left by the soft filters.

    The candidates are ``ssh_endpoints`` in order when set, else the primary
    ``ssh_endpoint`` alone. Unresolvable hostnames are always filtered out;
    ``endpoint_filter`` (location, network) narrows further. Each filter is
    skipped when it would leave no candidate, so the first candidate is the
    fallback. ``ssh_config`` and ``lookup`` resolve candidate hostnames for
    the DNS and network filters.
    """
    candidates = vol.endpoint_candidates
    eps = config.ssh_endpoints

    if len(candidates) == 1:
        # No filter can change a single candidate: skip its DNS lookup.
        return eps[candidates[0]]
    else:
        # The DNS and --network filters both classify each candidate's host.
        is_private = cache(
            partial(is_private_host, ssh_config=_or_default(ssh_config), lookup=lookup),
        )
        result = _apply_soft_filters(
            candidates,
            eps,
            endpoint_filter or EndpointFilter(),
            is_private,
        )
        return eps[result[0]]


def _apply_soft_filters(
    candidates: list[str],
    eps: dict[str, SshEndpoint],
    ef: EndpointFilter,
    is_private: Callable[[str], bool | None],
) -> list[str]:
    excl = set(ef.exclude_locations)
    locs = set(ef.locations)
    want_private = ef.network == NetworkType.PRIVATE if ef.network is not None else None

    filters: list[Callable[[str], bool]] = [
        # DNS reachability
        lambda s: is_private(eps[s].host) is not None,
        *([lambda s: not (excl & set(eps[s].location_list))] if excl else []),
        *([lambda s: bool(locs & set(eps[s].location_list))] if locs else []),
        *(
            [lambda s: is_private(eps[s].host) == want_private]
            if want_private is not None
            else []
        ),
    ]

    return reduce(_soft_filter, filters, candidates)


def resolve_proxy_chain(
    config: Config,
    server: SshEndpoint,
) -> list[SshEndpoint]:
    """
    Return the endpoints *server* jumps through, outermost first.

    A bastion with its own ``proxy-jump`` is reached through its chain, so each hop's
    chain comes before it, expanded the same way. A bastion several hops share is
    jumped through once, where it is first needed. Config validation rejects cycles,
    so the expansion terminates.
    """
    return [
        config.ssh_endpoints[slug]
        for slug in _expand_proxy_hops(config, server.proxy_jump_chain)
    ]


def _expand_proxy_hops(config: Config, hops: list[str]) -> list[str]:
    expanded = [
        slug
        for hop in hops
        for slug in [
            *_expand_proxy_hops(config, config.ssh_endpoints[hop].proxy_jump_chain),
            hop,
        ]
    ]
    return list(dict.fromkeys(expanded))


def endpoint_location_excluded(
    endpoint: SshEndpoint,
    exclude_locations: Collection[str],
) -> bool:
    """Whether *endpoint* is tagged with one of *exclude_locations*."""
    return not set(exclude_locations).isdisjoint(endpoint.location_list)


def _is_volume_excluded(
    config: Config,
    vol: RemoteVolume,
    endpoint_filter: EndpointFilter | None,
) -> bool:
    candidates = vol.endpoint_candidates
    excl = endpoint_filter.exclude_locations if endpoint_filter else []
    return bool(excl) and all(
        endpoint_location_excluded(config.ssh_endpoints[slug], excl)
        for slug in candidates
    )


def _resolve_volume(
    config: Config,
    vol: RemoteVolume,
    endpoint_filter: EndpointFilter | None,
    ssh_config: paramiko.SSHConfig,
    lookup: AddressLookupFn,
) -> ResolvedEndpoint:
    server = enrich_from_ssh_config(
        resolve_endpoint_for_volume(config, vol, endpoint_filter, ssh_config, lookup),
        ssh_config,
    )
    proxy_chain = [
        enrich_from_ssh_config(ep, ssh_config)
        for ep in resolve_proxy_chain(config, server)
    ]
    return ResolvedEndpoint(server=server, proxy_chain=proxy_chain)


def resolve_all_endpoints(
    config: Config,
    endpoint_filter: EndpointFilter | None = None,
    *,
    ssh_config: paramiko.SSHConfig | None = None,
    lookup: AddressLookupFn = dns_lookup,
) -> ResolvedEndpoints:
    """
    Keyed by volume slug; local volumes are not included.

    Volumes whose endpoints are all at excluded locations are omitted, so
    nothing attempts to reach them over SSH.

    ``ssh_config`` (default: the user's ``~/.ssh/config``, read once here,
    and only when some volume is remote) enriches endpoints and resolves
    aliases; ``lookup`` resolves hostnames for the DNS and network filters.
    """
    remote_volumes = [
        vol
        for vol in config.volumes.values()
        if isinstance(vol, RemoteVolume)
        and not _is_volume_excluded(config, vol, endpoint_filter)
    ]
    # A local-only config never reads ~/.ssh/config, so it never imports paramiko.
    return (
        _resolve_volumes(
            config,
            remote_volumes,
            endpoint_filter,
            _or_default(ssh_config),
            lookup,
        )
        if remote_volumes
        else {}
    )


def _resolve_volumes(
    config: Config,
    volumes: list[RemoteVolume],
    endpoint_filter: EndpointFilter | None,
    ssh_config: paramiko.SSHConfig,
    lookup: AddressLookupFn,
) -> ResolvedEndpoints:
    # Volumes share hosts (one server, several drives), and an unresolvable
    # name pays its DNS timeout on every lookup: resolve each name once.
    cached_lookup = cache(lookup)
    return {
        vol.slug: _resolve_volume(
            config,
            vol,
            endpoint_filter,
            ssh_config,
            cached_lookup,
        )
        for vol in volumes
    }
