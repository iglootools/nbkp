"""Sync dependency graph and topological ordering."""

from __future__ import annotations

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from ..config import ConfigError
from ..config.protocol import SyncConfig, SyncEndpoint

EndpointKey = tuple[str, str | None]


def endpoint_key(endpoint: SyncEndpoint) -> EndpointKey:
    """Return a hashable key for a sync endpoint."""
    return (endpoint.volume, endpoint.subdir)


def _build_graph(
    syncs: dict[str, SyncConfig],
) -> dict[str, set[str]]:
    """Build dependency graph: node → set of predecessors."""
    writers: dict[EndpointKey, list[str]] = defaultdict(list)
    for sync_slug, sync in syncs.items():
        dst_key = endpoint_key(sync.destination)
        writers[dst_key].append(sync_slug)

    graph: dict[str, set[str]] = {}
    for sync_slug, sync in syncs.items():
        src_key = endpoint_key(sync.source)
        deps = {
            writer
            for writer in writers.get(src_key, [])
            if writer != sync_slug
        }
        graph[sync_slug] = deps

    return graph


def sync_predecessors(
    syncs: dict[str, SyncConfig],
) -> dict[str, set[str]]:
    """Return direct predecessors for each sync slug.

    A sync B has predecessor A when A's destination matches
    B's source (same volume and subdir).
    """
    return _build_graph(syncs)


def sort_syncs(syncs: dict[str, SyncConfig]) -> list[str]:
    """Topologically sort syncs by their endpoint dependencies.

    A sync B depends on sync A when A's destination matches
    B's source (same volume and subdir).  Returns sync slugs
    in an order where dependees come before dependents.

    Raises ``ConfigError`` when a dependency cycle is detected.
    """
    graph = _build_graph(syncs)

    ts = TopologicalSorter(graph)
    try:
        return list(ts.static_order())
    except CycleError as exc:
        cycle = exc.args[1]
        raise ConfigError(
            "Cyclic sync dependency detected: " + " -> ".join(cycle)
        ) from exc
