"""Fabric-based remote command execution."""

from __future__ import annotations

import shlex
import subprocess

import paramiko
from fabric import Connection  # type: ignore[import-untyped]

from ..config import SshEndpoint
from .ssh import build_ssh_base_args as build_ssh_base_args  # noqa: F401
from .ssh import build_ssh_e_option as build_ssh_e_option  # noqa: F401
from .ssh import format_remote_path as format_remote_path  # noqa: F401


def _build_single_connection(
    server: SshEndpoint,
    gateway: Connection | None = None,
) -> Connection:
    """Build a single Fabric Connection with optional gateway."""
    opts = server.connection_options
    connect_kwargs: dict[str, object] = {
        "allow_agent": opts.allow_agent,
        "look_for_keys": opts.look_for_keys,
        "compress": opts.compress,
    }
    if opts.banner_timeout is not None:
        connect_kwargs["banner_timeout"] = opts.banner_timeout
    if opts.auth_timeout is not None:
        connect_kwargs["auth_timeout"] = opts.auth_timeout
    if opts.channel_timeout is not None:
        connect_kwargs["channel_timeout"] = opts.channel_timeout
    if opts.disabled_algorithms is not None:
        connect_kwargs["disabled_algorithms"] = opts.disabled_algorithms
    if server.key:
        connect_kwargs["key_filename"] = server.key

    conn = Connection(
        host=server.host,
        port=server.port,
        user=server.user,
        connect_kwargs=connect_kwargs,
        connect_timeout=opts.connect_timeout,
        forward_agent=opts.forward_agent,
        gateway=gateway,
    )

    if not opts.strict_host_key_checking:
        conn.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    return conn


def _build_connection(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> Connection:
    """Build a Fabric Connection with optional proxy chain."""
    gateway: Connection | None = None
    for proxy in proxy_chain or []:
        gateway = _build_single_connection(proxy, gateway)
    return _build_single_connection(server, gateway)


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote host via Fabric."""
    cmd_string = " ".join(shlex.quote(arg) for arg in command)
    with _build_connection(server, proxy_chain) as conn:
        if server.connection_options.server_alive_interval is not None:
            conn.transport.set_keepalive(
                server.connection_options.server_alive_interval
            )
        result = conn.run(cmd_string, warn=True, hide=True, in_stream=False)
    return subprocess.CompletedProcess(
        args=cmd_string,
        returncode=result.exited,
        stdout=result.stdout,
        stderr=result.stderr,
    )
