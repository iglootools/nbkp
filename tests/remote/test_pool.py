from unittest.mock import MagicMock

import pytest

from nbkp.remote.pool import ConnectionPool, active_pool, connection_scope


class TestConnectionPool:
    def test_opens_once_per_key(self) -> None:
        pool = ConnectionPool()
        conn = MagicMock()
        open_ = MagicMock(return_value=conn)

        assert pool.get_or_open("a", open_) == (conn, False)
        assert pool.get_or_open("a", open_) == (conn, True)
        open_.assert_called_once()

    def test_keys_are_independent(self) -> None:
        pool = ConnectionPool()
        a, b = MagicMock(), MagicMock()

        assert pool.get_or_open("a", lambda: a) == (a, False)
        assert pool.get_or_open("b", lambda: b) == (b, False)

    def test_failed_open_is_not_kept(self) -> None:
        pool = ConnectionPool()
        conn = MagicMock()
        open_ = MagicMock(side_effect=[OSError("refused"), conn])

        with pytest.raises(OSError, match="refused"):
            pool.get_or_open("a", open_)
        assert pool.get_or_open("a", open_) == (conn, False)

    def test_discard_closes_and_forgets(self) -> None:
        pool = ConnectionPool()
        stale, fresh = MagicMock(), MagicMock()
        pool.get_or_open("a", lambda: stale)

        pool.discard("a")

        stale.close.assert_called_once()
        assert pool.get_or_open("a", lambda: fresh) == (fresh, False)

    def test_discard_unknown_key_is_a_no_op(self) -> None:
        ConnectionPool().discard("a")

    def test_close_closes_every_connection_even_if_one_fails(self) -> None:
        pool = ConnectionPool()
        failing, other = MagicMock(), MagicMock()
        failing.close.side_effect = RuntimeError("boom")
        pool.get_or_open("a", lambda: failing)
        pool.get_or_open("b", lambda: other)

        with pytest.raises(RuntimeError, match="boom"):
            pool.close()

        other.close.assert_called_once()
        reopened = MagicMock()
        assert pool.get_or_open("a", lambda: reopened) == (reopened, False)


class TestConnectionScope:
    def test_no_pool_outside_a_scope(self) -> None:
        assert active_pool() is None

    def test_pool_is_active_inside_and_closed_after(self) -> None:
        conn = MagicMock()
        with connection_scope() as pool:
            assert active_pool() is pool
            pool.get_or_open("a", lambda: conn)
            conn.close.assert_not_called()
        assert active_pool() is None
        conn.close.assert_called_once()

    def test_closes_on_error(self) -> None:
        conn = MagicMock()
        with pytest.raises(ValueError), connection_scope() as pool:
            pool.get_or_open("a", lambda: conn)
            raise ValueError
        assert active_pool() is None
        conn.close.assert_called_once()

    def test_nested_scope_joins_the_enclosing_one(self) -> None:
        conn = MagicMock()
        with connection_scope() as outer:
            with connection_scope() as inner:
                assert inner is outer
                inner.get_or_open("a", lambda: conn)
            conn.close.assert_not_called()
        conn.close.assert_called_once()
