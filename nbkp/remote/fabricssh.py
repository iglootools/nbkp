import subprocess
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from functools import partial, reduce
from pathlib import Path
from typing import cast

import paramiko
from fabric import Connection  # type: ignore[import-untyped]

from ..config import SshConnectionOptions, SshEndpoint
from .errors import ssh_connection_errors
from .hostkeys import UnknownHostKeyError
from .pool import ConnectionPool, active_pool
from .ssh import shell_join


def _build_single_connection(
    server: SshEndpoint,
    gateway: Connection | None = None,
) -> Connection:
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

    _configure_host_key_verification(conn.client, opts)  # pyright: ignore[reportArgumentType]
    return conn


class _RejectUnknownHostKey(paramiko.MissingHostKeyPolicy):
    """Like ``paramiko.RejectPolicy``, but raises a dedicated exception so an
    unknown host is classified as an authentication failure rather than as
    an unreachable host (``RejectPolicy`` raises a bare ``SSHException``)."""

    def missing_host_key(  # noqa: DC04 -- Paramiko calls it
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        raise UnknownHostKeyError(hostname)


def _configure_host_key_verification(
    client: paramiko.SSHClient, opts: SshConnectionOptions
) -> None:
    """Mirror OpenSSH, which the rsync path uses: with strict checking, the
    host key must already be in ``known-hosts-file`` (which replaces
    ``~/.ssh/known_hosts``, like ``UserKnownHostsFile``). Fabric's own default
    is to trust any host key without loading a known_hosts file at all.
    """
    if not opts.strict_host_key_checking:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if opts.known_hosts_file is not None:
            client.load_host_keys(opts.known_hosts_file)
        return

    client.set_missing_host_key_policy(_RejectUnknownHostKey())
    if opts.known_hosts_file is None:
        client.load_system_host_keys()
    else:
        # A missing file is an empty one for OpenSSH: every host is unknown.
        known_hosts = Path(opts.known_hosts_file).expanduser()
        if known_hosts.exists():
            client.load_system_host_keys(str(known_hosts))


def _build_hops(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> tuple[Connection, ...]:
    """Connections from the first proxy to *server*, each the next one's gateway."""
    return reduce(_add_hop, [*(proxy_chain or []), server], ())


def _add_hop(
    hops: tuple[Connection, ...], endpoint: SshEndpoint
) -> tuple[Connection, ...]:
    return (*hops, _build_single_connection(endpoint, hops[-1] if hops else None))


@dataclass(frozen=True)
class _OpenConnection:
    """An open connection to a host and the proxy hops it tunnels through."""

    hops: tuple[Connection, ...]

    @property
    def target(self) -> Connection:
        return self.hops[-1]

    def close(self) -> None:
        # Fabric's close() leaves a connection's gateway open, so every hop is
        # closed, the target first. A hop that is already gone has nothing
        # left to release.
        for hop in reversed(self.hops):
            with suppress(*ssh_connection_errors()):
                hop.close()


def _open_connection(
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None = None,
) -> _OpenConnection:
    opened = _OpenConnection(_build_hops(server, proxy_chain))
    conn = opened.target
    try:
        # Fabric connects lazily: the transport only exists once open.
        conn.open()
        if server.connection_options.server_alive_interval is not None:
            conn.transport.set_keepalive(  # pyright: ignore[reportOptionalMemberAccess]
                server.connection_options.server_alive_interval
            )
    except BaseException:
        # A failed authentication leaves the transport connected.
        opened.close()
        raise
    return opened


def _run_on_connection(
    conn: Connection,
    cmd_string: str,
    input: str | None,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd_string* on a session channel of our own, with *input* on its
    stdin, then stdin closed.

    Not ``conn.run``: Fabric writes stdin from a worker thread, and when the
    command exits before reading it (``udisksctl unlock`` on a device udisks
    does not know yet), that thread fails on the closed channel and
    ``conn.run`` raises instead of returning, losing the command's stderr and
    exit status (https://github.com/fabric/fabric/issues/2351). Go back to
    ``conn.run`` once Fabric fixes that or offers a supported way to run a
    command that may exit without reading its input.
    """
    channel = conn.create_session()
    try:
        channel.exec_command(cmd_string)
        _send_input(channel, (input or "").encode())
        stdout, stderr = _read_output(channel)
        return subprocess.CompletedProcess(
            args=cmd_string,
            returncode=channel.recv_exit_status(),
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        channel.close()


def _send_input(channel: paramiko.Channel, data: bytes) -> None:
    # A command may exit without reading its input; its output and exit
    # status are what tell the caller why, so a closed channel is no error.
    with suppress(OSError):
        channel.sendall(data)
        channel.shutdown_write()


def _read_output(channel: paramiko.Channel) -> tuple[str, str]:
    # stderr is read on its own thread: stdout and stderr share the channel's
    # window, so reading one to the end first would stall a command that
    # fills the window with the other.
    with ThreadPoolExecutor(max_workers=1) as executor:
        stderr = executor.submit(channel.makefile_stderr("rb").read)
        stdout = channel.makefile("rb").read()
        return _decode(stdout), _decode(stderr.result())


def _decode(output: bytes) -> str:
    return output.decode(errors="replace")


def run_remote_command(
    server: SshEndpoint,
    command: list[str],
    proxy_chain: list[SshEndpoint] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *command* on *server*, reusing an open connection inside a
    :func:`~nbkp.remote.pool.connection_scope`, connecting just for this
    command otherwise."""
    cmd_string = shell_join(command)
    pool = active_pool()
    if pool is None:
        opened = _open_connection(server, proxy_chain)
        try:
            return _run_on_connection(opened.target, cmd_string, input)
        finally:
            opened.close()
    else:
        return _run_pooled(pool, server, proxy_chain, cmd_string, input)


def _run_pooled(
    pool: ConnectionPool,
    server: SshEndpoint,
    proxy_chain: list[SshEndpoint] | None,
    cmd_string: str,
    input: str | None,
) -> subprocess.CompletedProcess[str]:
    key = _pool_key(server, proxy_chain)
    open_ = partial(_open_connection, server, proxy_chain)
    opened, reused = _get_or_open(pool, key, open_)
    if reused and not opened.target.is_connected:
        # The transport noticed the connection drop (server restart, network
        # change) since the previous command.
        pool.discard(key)
        opened, reused = _get_or_open(pool, key, open_)
    try:
        return _run_on_connection(opened.target, cmd_string, input)
    except OSError, EOFError, paramiko.SSHException:
        pool.discard(key)
        if not reused:
            raise
    # A reused connection can be dead without the transport knowing yet (a
    # NAT or firewall dropped it). _run_on_connection raises a transport error
    # only while opening the session channel or starting the command: a
    # connection lost mid-command ends the command with exit status -1
    # instead, and a failed write of its input is ignored. The command therefore never
    # started, and running it once more on a fresh connection is safe.
    fresh, _ = _get_or_open(pool, key, open_)
    return _run_on_connection(fresh.target, cmd_string, input)


def _pool_key(server: SshEndpoint, proxy_chain: list[SshEndpoint] | None) -> Hashable:
    # Endpoints hold dicts (disabled-algorithms), so they are not hashable:
    # key on their full serialized settings instead.
    return tuple(ep.model_dump_json() for ep in [server, *(proxy_chain or [])])


def _get_or_open(
    pool: ConnectionPool,
    key: Hashable,
    open_: Callable[[], _OpenConnection],
) -> tuple[_OpenConnection, bool]:
    opened, reused = pool.get_or_open(key, open_)
    # Only this module fills the pool, always with _OpenConnection values.
    return (cast(_OpenConnection, opened), reused)
