"""SSH host resolution and network classification helpers."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path

import paramiko

from ..config.protocol import SshEndpoint


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

    updates = _ssh_config_updates(endpoint, ssh_config.lookup(endpoint.host))
    return endpoint.model_copy(update=updates) if updates else endpoint
