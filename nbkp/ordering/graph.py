from graphlib import CycleError, TopologicalSorter

from ..config import ConfigError
from ..config.loader import ConfigErrorReason
from ..config.protocol import SyncConfig


def sync_predecessors(
    syncs: dict[str, SyncConfig],
) -> dict[str, set[str]]:
    """Direct upstream syncs for each sync slug.

    A sync B depends on sync A when A's destination endpoint
    slug matches B's source endpoint slug.
    """
    return {
        sync_slug: {
            writer
            for writer, upstream in syncs.items()
            if upstream.destination == sync.source and writer != sync_slug
        }
        for sync_slug, sync in syncs.items()
    }


def sort_syncs(syncs: dict[str, SyncConfig]) -> list[str]:
    """Raises ``ConfigError`` when a dependency cycle is detected."""
    graph = sync_predecessors(syncs)

    ts = TopologicalSorter(graph)
    try:
        return list(ts.static_order())
    except CycleError as exc:
        cycle = exc.args[1]
        raise ConfigError(
            "Cyclic sync dependency detected: " + " -> ".join(cycle),
            reason=ConfigErrorReason.CYCLIC_DEPENDENCY,
        ) from exc


def build_adjacency(
    syncs: dict[str, SyncConfig],
) -> tuple[dict[str, list[SyncConfig]], set[str]]:
    """Returns (children, roots) where:
    - children maps source endpoint slug → list of SyncConfig
    - roots is the set of endpoint slugs that are never destinations
    """
    all_sources = {sync.source for sync in syncs.values()}
    all_destinations = {sync.destination for sync in syncs.values()}
    children = {
        source: [sync for sync in syncs.values() if sync.source == source]
        for source in dict.fromkeys(sync.source for sync in syncs.values())
    }
    return children, all_sources - all_destinations
