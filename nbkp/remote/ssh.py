"""SSH command building and remote command execution helpers."""

from __future__ import annotations

import shlex
import subprocess

from ..config import SshEndpoint, SshConnectionOptions


def _ssh_o_options(opts: SshConnectionOptions) -> list[str]:
    """Derive SSH -o option values from structured options."""
    result = [
        f"ConnectTimeout={opts.connect_timeout}",
        "BatchMode=yes",
    ]
    if opts.compress:
        result.append("Compression=yes")
    if opts.server_alive_interval is not None:
        result.append(f"ServerAliveInterval=" f"{opts.server_alive_interval}")
    if not opts.strict_host_key_checking:
        result.append("StrictHostKeyChecking=no")
    if opts.known_hosts_file is not None:
        result.append(f"UserKnownHostsFile={opts.known_hosts_file}")
    # Suppress "Permanently added ... to the list of known hosts"
    # warnings when host-key verification is fully disabled.
    if not opts.strict_host_key_checking and opts.known_hosts_file:
        result.append("LogLevel=ERROR")
    if opts.forward_agent:
        result.append("ForwardAgent=yes")
    return result


def format_proxy_jump_chain(proxies: list[SshEndpoint]) -> str:
    """Format proxy chain as comma-separated [user@]host[:port] for -J."""
    parts: list[str] = []
    for proxy in proxies:
        host = f"{proxy.user}@{proxy.host}" if proxy.user else proxy.host
        if proxy.port != 22:
            host += f":{proxy.port}"
        parts.append(host)
    return ",".join(parts)


def _build_proxy_command(
    proxies: list[SshEndpoint],
) -> str:
    """Build a nested ProxyCommand string for the proxy chain.

    Uses ProxyCommand instead of -J so that per-proxy SSH
    options (e.g. StrictHostKeyChecking) are propagated to
    each hop.
    """
    proxy = proxies[0]
    parts: list[str] = ["ssh"]
    for opt in _ssh_o_options(proxy.connection_options):
        parts.extend(["-o", opt])
    if proxy.port != 22:
        parts.extend(["-p", str(proxy.port)])
    if proxy.key:
        parts.extend(["-i", proxy.key])
    parts.append("-W")
    parts.append("%h:%p")
    host = f"{proxy.user}@{proxy.host}" if proxy.user else proxy.host
    parts.append(host)

    inner_cmd = " ".join(parts)

    for proxy in proxies[1:]:
        escaped_inner = inner_cmd.replace("%", "%%")
        parts = ["ssh"]
        for opt in _ssh_o_options(proxy.connection_options):
            parts.extend(["-o", opt])
        parts.extend(["-o", f"ProxyCommand={escaped_inner}"])
        if proxy.port != 22:
            parts.extend(["-p", str(proxy.port)])
        if proxy.key:
            parts.extend(["-i", proxy.key])
        parts.append("-W")
        parts.append("%h:%p")
        host = f"{proxy.user}@{proxy.host}" if proxy.user else proxy.host
        parts.append(host)
        inner_cmd = " ".join(parts)

    return inner_cmd


def build_ssh_base_args(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> list[str]:
    """Build base SSH command args for a remote volume.

    Returns args like:
        ssh -o ConnectTimeout=10 -o BatchMode=yes [opts] host
    """
    args = ["ssh"]
    for opt in _ssh_o_options(server.connection_options):
        args.extend(["-o", opt])
    if server.port != 22:
        args.extend(["-p", str(server.port)])
    if server.key:
        args.extend(["-i", server.key])
    if proxy_chain:
        proxy_cmd = _build_proxy_command(proxy_chain)
        args.extend(["-o", f"ProxyCommand={proxy_cmd}"])

    host = f"{server.user}@{server.host}" if server.user else server.host
    args.append(host)
    return args


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote host via SSH."""
    cmd_string = " ".join(shlex.quote(arg) for arg in command)
    args = build_ssh_base_args(server, proxy_chain) + [cmd_string]
    return subprocess.run(
        args,
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
    ssh_cmd_parts = ["ssh"]
    for opt in _ssh_o_options(server.connection_options):
        ssh_cmd_parts.extend(["-o", opt])
    if server.port != 22:
        ssh_cmd_parts.extend(["-p", str(server.port)])
    if server.key:
        ssh_cmd_parts.extend(["-i", server.key])
    if proxy_chain:
        proxy_cmd = _build_proxy_command(proxy_chain)
        quoted = shlex.quote(f"ProxyCommand={proxy_cmd}")
        ssh_cmd_parts.extend(["-o", quoted])

    return ["-e", " ".join(ssh_cmd_parts)]


def format_remote_path(server: SshEndpoint, path: str) -> str:
    """Format a remote path as [user@]host:path."""
    host = f"{server.user}@{server.host}" if server.user else server.host
    return f"{host}:{path}"
