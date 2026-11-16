from collections.abc import Callable

import pytest

from nbkp.remote.pool import ConnectionPool, active_pool, connection_scope


class _Conn:
    """A connection that counts how often it was closed, and raises
    *close_error* when closed if given."""

    def __init__(self, close_error: Exception | None = None) -> None:
        self.closes = 0
        self._close_error = close_error

    def close(self) -> None:
        self.closes += 1
        if self._close_error is not None:
            raise self._close_error


def _opener(*outcomes: _Conn | Exception) -> tuple[Callable[[], _Conn], list[None]]:
    """An open function returning (or raising) *outcomes* in turn, and the
    list it appends to on every call."""
    calls: list[None] = []
    pending = list(outcomes)

    def open_() -> _Conn:
        calls.append(None)
        outcome = pending.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return open_, calls


class TestConnectionPool:
    def test_opens_once_per_key(self) -> None:
        pool = ConnectionPool()
        conn = _Conn()
        open_, calls = _opener(conn)

        assert pool.get_or_open("a", open_) == (conn, False)
        assert pool.get_or_open("a", open_) == (conn, True)
        assert len(calls) == 1

    def test_keys_are_independent(self) -> None:
        pool = ConnectionPool()
        a, b = _Conn(), _Conn()

        assert pool.get_or_open("a", lambda: a) == (a, False)
        assert pool.get_or_open("b", lambda: b) == (b, False)

    def test_failed_open_is_not_kept(self) -> None:
        pool = ConnectionPool()
        conn = _Conn()
        open_, _ = _opener(OSError("refused"), conn)

        with pytest.raises(OSError, match="refused"):
            pool.get_or_open("a", open_)
        assert pool.get_or_open("a", open_) == (conn, False)

    def test_discard_closes_and_forgets(self) -> None:
        pool = ConnectionPool()
        stale, fresh = _Conn(), _Conn()
        pool.get_or_open("a", lambda: stale)

        pool.discard("a")

        assert stale.closes == 1
        assert pool.get_or_open("a", lambda: fresh) == (fresh, False)

    def test_discard_unknown_key_is_a_no_op(self) -> None:
        ConnectionPool().discard("a")

    def test_close_closes_every_connection_even_if_one_fails(self) -> None:
        pool = ConnectionPool()
        failing, other = _Conn(close_error=RuntimeError("boom")), _Conn()
        pool.get_or_open("a", lambda: failing)
        pool.get_or_open("b", lambda: other)

        with pytest.raises(RuntimeError, match="boom"):
            pool.close()

        assert other.closes == 1
        reopened = _Conn()
        assert pool.get_or_open("a", lambda: reopened) == (reopened, False)


class TestConnectionScope:
    def test_no_pool_outside_a_scope(self) -> None:
        assert active_pool() is None

    def test_pool_is_active_inside_and_closed_after(self) -> None:
        conn = _Conn()
        with connection_scope() as pool:
            assert active_pool() is pool
            pool.get_or_open("a", lambda: conn)
            assert conn.closes == 0
        assert active_pool() is None
        assert conn.closes == 1

    def test_closes_on_error(self) -> None:
        conn = _Conn()
        with pytest.raises(ValueError), connection_scope() as pool:
            pool.get_or_open("a", lambda: conn)
            raise ValueError
        assert active_pool() is None
        assert conn.closes == 1

    def test_nested_scope_joins_the_enclosing_one(self) -> None:
        conn = _Conn()
        with connection_scope() as outer:
            with connection_scope() as inner:
                assert inner is outer
                inner.get_or_open("a", lambda: conn)
            assert conn.closes == 0
        assert conn.closes == 1
