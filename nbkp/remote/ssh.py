"""SSH command building and remote command execution helpers."""

from __future__ import annotations

import shlex
import subprocess

from ..config import SshConnectionOptions, SshEndpoint


def _format_host(endpoint: SshEndpoint) -> str:
    """Format [user@]host for an SSH endpoint."""
    return f"{endpoint.user}@{endpoint.host}" if endpoint.user else endpoint.host


def _format_host_port(endpoint: SshEndpoint) -> str:
    """Format [user@]host[:port] for proxy-jump notation."""
    host = _format_host(endpoint)
    return f"{host}:{endpoint.port}" if endpoint.port != 22 else host


def _ssh_o_options(opts: SshConnectionOptions) -> list[str]:
    """Derive SSH -o option values from structured options."""
    return [
        opt
        for opt in [
            f"ConnectTimeout={opts.connect_timeout}",
            "BatchMode=yes",
            "Compression=yes" if opts.compress else None,
            (
                f"ServerAliveInterval={opts.server_alive_interval}"
                if opts.server_alive_interval is not None
                else None
            ),
            "StrictHostKeyChecking=no" if not opts.strict_host_key_checking else None,
            (
                f"UserKnownHostsFile={opts.known_hosts_file}"
                if opts.known_hosts_file is not None
                else None
            ),
            # Suppress "Permanently added ... to the list of known hosts"
            # warnings when host-key verification is fully disabled.
            (
                "LogLevel=ERROR"
                if not opts.strict_host_key_checking and opts.known_hosts_file
                else None
            ),
            "ForwardAgent=yes" if opts.forward_agent else None,
        ]
        if opt is not None
    ]


def _ssh_endpoint_args(endpoint: SshEndpoint) -> list[str]:
    """Build SSH args for a single endpoint: -o options, -p port, -i key."""
    o_args = [
        arg
        for opt in _ssh_o_options(endpoint.connection_options)
        for arg in ["-o", opt]
    ]
    return [
        *o_args,
        *(["-p", str(endpoint.port)] if endpoint.port != 22 else []),
        *(["-i", endpoint.key] if endpoint.key else []),
    ]


def format_proxy_jump_chain(proxies: list[SshEndpoint]) -> str:
    """Format proxy chain as comma-separated [user@]host[:port] for -J."""
    return ",".join(_format_host_port(p) for p in proxies)


def _build_proxy_hop(proxy: SshEndpoint, inner_cmd: str | None) -> str:
    """Build the SSH command string for a single proxy hop."""
    o_args = [
        arg for opt in _ssh_o_options(proxy.connection_options) for arg in ["-o", opt]
    ]
    parts = [
        "ssh",
        *o_args,
        *(["-o", f"ProxyCommand={inner_cmd.replace('%', '%%')}"] if inner_cmd else []),
        *(["-p", str(proxy.port)] if proxy.port != 22 else []),
        *(["-i", proxy.key] if proxy.key else []),
        "-W",
        "%h:%p",
        _format_host(proxy),
    ]
    return " ".join(parts)


def _build_proxy_command(
    proxies: list[SshEndpoint],
) -> str:
    """Build a nested ProxyCommand string for the proxy chain.

    Uses ProxyCommand instead of -J so that per-proxy SSH
    options (e.g. StrictHostKeyChecking) are propagated to
    each hop.
    """
    inner_cmd: str | None = None
    for proxy in proxies:
        inner_cmd = _build_proxy_hop(proxy, inner_cmd)
    assert inner_cmd is not None
    return inner_cmd


def _build_ssh_core_args(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    """Build common SSH args: -o options, port, key, proxy command."""
    return [
        *_ssh_endpoint_args(server),
        *(
            ["-o", f"ProxyCommand={_build_proxy_command(proxy_chain)}"]
            if proxy_chain
            else []
        ),
    ]


def build_ssh_base_args(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    """Build base SSH command args for a remote volume.

    Returns args like:
        ssh -o ConnectTimeout=10 -o BatchMode=yes [opts] host
    """
    return ["ssh", *_build_ssh_core_args(server, proxy_chain), _format_host(server)]


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote host via SSH."""
    cmd_string = " ".join(shlex.quote(arg) for arg in command)
    return subprocess.run(
        [*build_ssh_base_args(server, proxy_chain), cmd_string],
        capture_output=True,
        text=True,
    )


def build_ssh_e_option(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    """Build rsync's -e option for SSH with custom port/key.

    Returns a list like:
        ["-e", "ssh -o ConnectTimeout=10 -o BatchMode=yes ..."]
    """
    core = _build_ssh_core_args(server, proxy_chain)
    # For -e, the ProxyCommand value needs shell quoting
    parts = ["ssh"]
    for arg in core:
        if arg.startswith("ProxyCommand="):
            parts.append(shlex.quote(arg))
        else:
            parts.append(arg)
    return ["-e", " ".join(parts)]


def format_remote_path(server: SshEndpoint, path: str) -> str:
    """Format a remote path as [user@]host:path."""
    return f"{_format_host(server)}:{path}"
