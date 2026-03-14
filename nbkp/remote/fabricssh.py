"""Fabric-based remote command execution."""

from __future__ import annotations

import io
import shlex
import subprocess
from functools import reduce

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
        **{
            k: v
            for k, v in {
                "banner_timeout": opts.banner_timeout,
                "auth_timeout": opts.auth_timeout,
                "channel_timeout": opts.channel_timeout,
                "disabled_algorithms": opts.disabled_algorithms,
                "key_filename": server.key,
            }.items()
            if v is not None
        },
    }

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
        conn.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # pyright: ignore[reportOptionalMemberAccess]

    if opts.known_hosts_file is not None:
        conn.client.load_host_keys(opts.known_hosts_file)  # pyright: ignore[reportOptionalMemberAccess]

    return conn


def _build_connection(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> Connection:
    """Build a Fabric Connection with optional proxy chain."""
    gateway = reduce(
        lambda gw, proxy: _build_single_connection(proxy, gw),
        proxy_chain or [],
        None,
    )
    return _build_single_connection(server, gateway)


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote host via Fabric."""
    cmd_string = " ".join(shlex.quote(arg) for arg in command)
    # When input is provided, wrap it as a BytesIO stream for Fabric.
    # Otherwise, use in_stream=False to disable stdin entirely.
    in_stream: io.BytesIO | bool = (
        io.BytesIO(input.encode()) if input is not None else False
    )
    with _build_connection(server, proxy_chain) as conn:
        if server.connection_options.server_alive_interval is not None:
            conn.transport.set_keepalive(  # pyright: ignore[reportOptionalMemberAccess]
                server.connection_options.server_alive_interval
            )
        result = conn.run(cmd_string, warn=True, hide=True, in_stream=in_stream)
    return subprocess.CompletedProcess(
        args=cmd_string,
        returncode=result.exited,
        stdout=result.stdout,
        stderr=result.stderr,
    )
