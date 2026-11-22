"""
Reuse of open SSH connections for the duration of one CLI command.

Preflight, mounting, snapshots and pruning each run many short remote
commands against the same hosts; connecting for every one of them costs a
TCP and SSH handshake plus authentication each time, once more per proxy hop.

A command opts in with :func:`connection_scope`. Inside the scope,
``run_remote_command`` keeps one connection per (endpoint, proxy chain) and
closes them all when the scope ends; outside any scope it connects for each
command as before. The scope is held in a context variable rather than passed
down because every remote call site sits several layers below the CLI, and a
parameter would have to thread through all of them.

This module imports neither Fabric nor Paramiko, so the CLI can open a scope
without paying their import cost on commands that never connect.
"""

import threading
from collections.abc import Callable, Generator, Hashable
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from typing import Protocol


class Closeable(Protocol):
    """A connection the pool can hold: anything with a ``close`` method."""

    def close(self) -> None:
        """Release the connection."""
        ...


class ConnectionPool:
    """
    Open connections by key, reused until :meth:`discard` or :meth:`close`.

    Thread-safe: a lock guards the table, and is held while a missing
    connection is opened so that two threads never open the same one twice.
    """

    def __init__(self) -> None:
        """Build an empty pool."""
        self._connections: dict[Hashable, Closeable] = {}
        self._lock = threading.Lock()

    def get_or_open(
        self,
        key: Hashable,
        open_: Callable[[], Closeable],
    ) -> tuple[Closeable, bool]:
        """
        Return the connection for *key*, and whether it was already open.

        A connection that fails to open is not kept: the exception
        propagates and the next call tries again.
        """
        with self._lock:
            cached = self._connections.get(key)
            if cached is not None:
                return (cached, True)
            else:
                opened = open_()
                self._connections[key] = opened
                return (opened, False)

    def discard(self, key: Hashable) -> None:
        """Close and forget the connection for *key*, if any."""
        with self._lock:
            stale = self._connections.pop(key, None)
        if stale is not None:
            stale.close()

    def close(self) -> None:
        """Close every connection, each one even if closing another raised."""
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        with ExitStack() as stack:
            for conn in connections:
                stack.callback(conn.close)


_active_pool: ContextVar[ConnectionPool | None] = ContextVar(
    "nbkp_ssh_connection_pool",
    default=None,
)


def active_pool() -> ConnectionPool | None:
    """Return the pool of the enclosing :func:`connection_scope`, or ``None``."""
    return _active_pool.get()


@contextmanager
def connection_scope() -> Generator[ConnectionPool]:
    """
    Reuse SSH connections until the block exits, then close them.

    A nested scope joins the enclosing one, which keeps ownership of the
    connections.
    """
    enclosing = _active_pool.get()
    if enclosing is not None:
        yield enclosing
    else:
        pool = ConnectionPool()
        token = _active_pool.set(pool)
        try:
            yield pool
        finally:
            _active_pool.reset(token)
            pool.close()
