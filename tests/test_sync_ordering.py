"""Tests for nbkp.sync.ordering."""

from __future__ import annotations

import pytest

from nbkp.config import (
    ConfigError,
    DestinationSyncEndpoint,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.sync.ordering import endpoint_key, sort_syncs, sync_predecessors


def _sync(
    slug: str,
    src_vol: str,
    dst_vol: str,
    src_sub: str | None = None,
    dst_sub: str | None = None,
) -> SyncConfig:
    return SyncConfig(
        slug=slug,
        source=SyncEndpoint(volume=src_vol, subdir=src_sub),
        destination=DestinationSyncEndpoint(volume=dst_vol, subdir=dst_sub),
    )


class TestEndpointKey:
    def test_with_subdir(self) -> None:
        ep = SyncEndpoint(volume="v", subdir="photos")
        assert endpoint_key(ep) == ("v", "photos")

    def test_without_subdir(self) -> None:
        ep = SyncEndpoint(volume="v")
        assert endpoint_key(ep) == ("v", None)


class TestSortSyncs:
    def test_independent_syncs(self) -> None:
        syncs = {
            "a": _sync("a", "v1", "v2"),
            "b": _sync("b", "v3", "v4"),
        }
        result = sort_syncs(syncs)
        assert set(result) == {"a", "b"}

    def test_upstream_downstream_syncs_sorted(self) -> None:
        # upstream a writes to (usb, None), downstream b reads from (usb, None)
        syncs = {
            "b": _sync("b", "usb", "nas"),
            "a": _sync("a", "laptop", "usb"),
        }
        result = sort_syncs(syncs)
        assert result.index("a") < result.index("b")

    def test_upstream_downstream_syncs_with_subdir(self) -> None:
        # upstream a → (usb, photos), downstream b ← (usb, photos)
        syncs = {
            "b": _sync("b", "usb", "nas", src_sub="photos"),
            "a": _sync("a", "laptop", "usb", dst_sub="photos"),
        }
        result = sort_syncs(syncs)
        assert result.index("a") < result.index("b")

    def test_no_dependency_different_subdir(self) -> None:
        # a writes to (usb, photos), b reads from (usb, music)
        # No dependency since subdirs differ
        syncs = {
            "a": _sync("a", "laptop", "usb", dst_sub="photos"),
            "b": _sync("b", "usb", "nas", src_sub="music"),
        }
        result = sort_syncs(syncs)
        assert set(result) == {"a", "b"}

    def test_chain_dependency(self) -> None:
        # a -> b -> c
        syncs = {
            "c": _sync("c", "v2", "v3"),
            "a": _sync("a", "v0", "v1"),
            "b": _sync("b", "v1", "v2"),
        }
        result = sort_syncs(syncs)
        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")

    def test_cycle_raises_config_error(self) -> None:
        # a writes to v1, b reads from v1 and writes to v2,
        # a reads from v2 → cycle
        syncs = {
            "a": _sync("a", "v2", "v1"),
            "b": _sync("b", "v1", "v2"),
        }
        with pytest.raises(ConfigError, match="Cyclic"):
            sort_syncs(syncs)

    def test_empty_syncs(self) -> None:
        assert sort_syncs({}) == []

    def test_single_sync(self) -> None:
        syncs = {"a": _sync("a", "v1", "v2")}
        assert sort_syncs(syncs) == ["a"]

    def test_self_loop_ignored(self) -> None:
        # a reads and writes to same volume — not a cycle
        syncs = {
            "a": _sync("a", "v1", "v1"),
        }
        assert sort_syncs(syncs) == ["a"]


class TestSyncPredecessors:
    def test_no_dependencies(self) -> None:
        syncs = {
            "a": _sync("a", "v1", "v2"),
            "b": _sync("b", "v3", "v4"),
        }
        preds = sync_predecessors(syncs)
        assert preds == {"a": set(), "b": set()}

    def test_chain_dependency(self) -> None:
        syncs = {
            "a": _sync("a", "v0", "v1"),
            "b": _sync("b", "v1", "v2"),
            "c": _sync("c", "v2", "v3"),
        }
        preds = sync_predecessors(syncs)
        assert preds["a"] == set()
        assert preds["b"] == {"a"}
        assert preds["c"] == {"b"}
