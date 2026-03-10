"""Sync dependency graph and topological ordering."""

from __future__ import annotations

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from ..config import ConfigError
from ..config.loader import ConfigErrorReason
from ..config.protocol import SyncConfig


def _build_graph(
    syncs: dict[str, SyncConfig],
) -> dict[str, set[str]]:
    """Build dependency graph: node -> set of upstream syncs.

    A sync B depends on sync A when A's destination endpoint
    slug matches B's source endpoint slug.
    """
    writers: dict[str, list[str]] = defaultdict(list)
    for sync_slug, sync in syncs.items():
        writers[sync.destination].append(sync_slug)

    graph: dict[str, set[str]] = {}
    for sync_slug, sync in syncs.items():
        deps = {
            writer for writer in writers.get(sync.source, []) if writer != sync_slug
        }
        graph[sync_slug] = deps

    return graph


def sync_predecessors(
    syncs: dict[str, SyncConfig],
) -> dict[str, set[str]]:
    """Return direct upstream syncs for each sync slug.

    A sync B has upstream sync A when A's destination endpoint
    matches B's source endpoint (same slug).
    """
    return _build_graph(syncs)


def sort_syncs(syncs: dict[str, SyncConfig]) -> list[str]:
    """Topologically sort syncs by their endpoint dependencies.

    A sync B depends on sync A when A's destination endpoint
    matches B's source endpoint (same slug).  Returns sync
    slugs in an order where upstream syncs come before
    downstream syncs.

    Raises ``ConfigError`` when a dependency cycle is detected.
    """
    graph = _build_graph(syncs)

    ts = TopologicalSorter(graph)
    try:
        return list(ts.static_order())
    except CycleError as exc:
        cycle = exc.args[1]
        raise ConfigError(
            "Cyclic sync dependency detected: " + " -> ".join(cycle),
            reason=ConfigErrorReason.CYCLIC_DEPENDENCY,
        ) from exc
