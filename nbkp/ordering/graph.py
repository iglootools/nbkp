from graphlib import TopologicalSorter

from nbkp.config.protocol import SyncConfig, sync_predecessors


def sort_syncs(syncs: dict[str, SyncConfig]) -> list[str]:
    """Slugs in dependency order, upstream first.

    ``Config`` validation rejects dependency cycles, so the ``CycleError``
    ``static_order`` raises on one would mean a bug, not a config error.
    """
    return list(TopologicalSorter(sync_predecessors(syncs)).static_order())


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
