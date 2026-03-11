"""Tests for nbkp.sync.ordering."""

from __future__ import annotations

import pytest

from nbkp.config import (
    ConfigError,
    ConfigErrorReason,
    SyncConfig,
)
from nbkp.sync.ordering import sort_syncs, sync_predecessors


def _sync(
    slug: str,
    src_ep: str,
    dst_ep: str,
) -> SyncConfig:
    return SyncConfig(
        slug=slug,
        source=src_ep,
        destination=dst_ep,
    )


class TestSortSyncs:
    def test_independent_syncs(self) -> None:
        syncs = {
            "a": _sync("a", "ep-v1", "ep-v2"),
            "b": _sync("b", "ep-v3", "ep-v4"),
        }
        result = sort_syncs(syncs)
        assert set(result) == {"a", "b"}

    def test_upstream_downstream_syncs_sorted(self) -> None:
        # upstream a writes to ep-usb, downstream b reads from ep-usb
        syncs = {
            "b": _sync("b", "ep-usb", "ep-nas"),
            "a": _sync("a", "ep-laptop", "ep-usb"),
        }
        result = sort_syncs(syncs)
        assert result.index("a") < result.index("b")

    def test_no_dependency_different_endpoints(self) -> None:
        # a writes to ep-usb-photos, b reads from ep-usb-music
        # No dependency since endpoint slugs differ
        syncs = {
            "a": _sync("a", "ep-laptop", "ep-usb-photos"),
            "b": _sync("b", "ep-usb-music", "ep-nas"),
        }
        result = sort_syncs(syncs)
        assert set(result) == {"a", "b"}

    def test_chain_dependency(self) -> None:
        # a -> b -> c
        syncs = {
            "c": _sync("c", "ep-v2", "ep-v3"),
            "a": _sync("a", "ep-v0", "ep-v1"),
            "b": _sync("b", "ep-v1", "ep-v2"),
        }
        result = sort_syncs(syncs)
        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")

    def test_cycle_raises_config_error(self) -> None:
        # a writes to ep-v1, b reads from ep-v1 and writes to ep-v2,
        # a reads from ep-v2 → cycle
        syncs = {
            "a": _sync("a", "ep-v2", "ep-v1"),
            "b": _sync("b", "ep-v1", "ep-v2"),
        }
        with pytest.raises(ConfigError) as excinfo:
            sort_syncs(syncs)
        assert excinfo.value.reason == ConfigErrorReason.CYCLIC_DEPENDENCY

    def test_empty_syncs(self) -> None:
        assert sort_syncs({}) == []

    def test_single_sync(self) -> None:
        syncs = {"a": _sync("a", "ep-v1", "ep-v2")}
        assert sort_syncs(syncs) == ["a"]

    def test_self_loop_ignored(self) -> None:
        # a reads and writes to same endpoint — not a cycle
        syncs = {
            "a": _sync("a", "ep-v1", "ep-v1"),
        }
        assert sort_syncs(syncs) == ["a"]


class TestSyncPredecessors:
    def test_no_dependencies(self) -> None:
        syncs = {
            "a": _sync("a", "ep-v1", "ep-v2"),
            "b": _sync("b", "ep-v3", "ep-v4"),
        }
        preds = sync_predecessors(syncs)
        assert preds == {"a": set(), "b": set()}

    def test_chain_dependency(self) -> None:
        syncs = {
            "a": _sync("a", "ep-v0", "ep-v1"),
            "b": _sync("b", "ep-v1", "ep-v2"),
            "c": _sync("c", "ep-v2", "ep-v3"),
        }
        preds = sync_predecessors(syncs)
        assert preds["a"] == set()
        assert preds["b"] == {"a"}
        assert preds["c"] == {"b"}
