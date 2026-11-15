import io
import subprocess
from collections.abc import Callable, Hashable
from contextlib import suppress
from dataclasses import dataclass
from functools import partial, reduce
from typing import cast

import invoke.exceptions  # type: ignore[import-untyped]
import paramiko
from fabric import Connection  # type: ignore[import-untyped]

from ..config import SshEndpoint
from .errors import ssh_connection_errors
from .pool import ConnectionPool, active_pool
from .ssh import shell_join


class StdinClosedProcess(subprocess.CompletedProcess[str]):
    """A remote command that exited before consuming the stdin sent to it.

    Returned instead of crashing on the raw Paramiko/Fabric thread exception
    (e.g. ``udisksctl unlock`` refusing fast under ``--no-user-interaction``).
    The remote stderr is lost with the channel, so callers classify the
    failure from the type — ``isinstance(result, StdinClosedProcess)`` — and
    ``stderr`` only carries a human-readable explanation.
    """

    def __init__(self, args: str) -> None:
        super().__init__(
            args=args,
            returncode=1,
            stdout="",
            stderr="remote process exited before consuming stdin",
        )


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

    if not opts.strict_host_key_checking:
        conn.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # pyright: ignore[reportOptionalMemberAccess]

    if opts.known_hosts_file is not None:
        conn.client.load_host_keys(opts.known_hosts_file)  # pyright: ignore[reportOptionalMemberAccess]

    return conn


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
    # in_stream=False disables stdin; Fabric's default would forward this
    # process's own stdin to the remote command.
    in_stream: io.BytesIO | bool = (
        io.BytesIO(input.encode()) if input is not None else False
    )
    try:
        result = conn.run(cmd_string, warn=True, hide=True, in_stream=in_stream)
    except invoke.exceptions.ThreadException:
        # Stdin/stdout/stderr worker raised — usually because the remote
        # process exited before the stdin writer finished sending data
        # (Paramiko then raises OSError("Socket is closed")). The actual
        # remote stderr is lost with the channel, so we return a typed
        # result and let callers classify based on context.
        return StdinClosedProcess(args=cmd_string)
    return subprocess.CompletedProcess(
        args=cmd_string,
        returncode=result.exited,
        stdout=result.stdout,
        stderr=result.stderr,
    )


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
    # NAT or firewall dropped it). run() raises a transport error only while
    # opening the session channel: a connection lost mid-command ends the
    # command with exit status -1 instead. The command therefore never
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
